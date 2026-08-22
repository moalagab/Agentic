"""
WhatsApp Follow-up Sequence Engine — WAHA + SQLite persistence
محرك متابعة العملاء عبر واتساب

الفلسفة الجديدة:
  - الخطوات تُحفظ في SQLite (lead_follow_ups) عند إنشاء العميل
  - الـ scheduler يقرأ كل ساعتين ويرسل ما استحق
  - الإرسال عبر WAHA (نصوص عربية حرة — لا templates مدفوعة)
  - إذا أُعيد تشغيل السيرفر → المتابعات محفوظة ولا تضيع

تسلسل الإرسال:
  HIGH   → فوري + يوم 1 + يوم 3
  MEDIUM → بعد ساعة + يوم 3 + يوم 7
  LOW    → يوم 3 + يوم 7
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import structlog

from employee.memory import (
    get_due_follow_ups,
    mark_follow_up_done,
    schedule_follow_up,
    get_lead_profile,
    cancel_follow_ups,
    log_action,
)

if TYPE_CHECKING:
    from notifications.whatsapp import WhatsAppNotifier

logger = structlog.get_logger(__name__)


# الحد الأقصى لمحاولات الإرسال الفاشلة قبل إغلاق سجل المتابعة نهائيًا.
_MAX_SEND_FAILURES = 3


def _normalize_wa_phone(raw: str) -> str:
    """
    طبّع الرقم إلى صيغة E.164 وأعد "" إذا كان غير صالح للواتساب.

    يمنع حالتين رصدناهما فعليًا في الإنتاج:
      • "+" وحده (375 محاولة فاشلة) — نص غير فارغ فيمر من `if not phone`
      • معرّف محادثة تيليجرام مثل 141476642177168 (106 محاولات) — 15 رقمًا
        بلا رمز دولة صالح، سُجّل في حقل الهاتف بالخطأ
    """
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if not digits:
        return ""
    # E.164: 8–15 رقمًا، ولا يبدأ بصفر
    if not (8 <= len(digits) <= 15) or digits[0] == "0":
        return ""
    # معرّفات تيليجرام تقع في نطاق 13–16 رقمًا بلا رمز دولة معروف؛
    # نقبل فقط الأرقام التي تبدأ برمز دولة معقول الطول.
    if len(digits) >= 14 and not digits.startswith(("966", "971", "973", "974", "965", "968", "962", "20")):
        return ""
    return "+" + digits


def _bump_send_failure(followup_id) -> int:
    """سجّل محاولة إرسال فاشلة لهذه المتابعة وأعد العدد التراكمي."""
    try:
        from employee.memory import _get_conn
        with _get_conn() as conn:
            conn.execute(
                "INSERT INTO agent_actions (action_type, description, result, lead_id, created_at)"
                " VALUES ('followup_send_failure', 'فشل إرسال متابعة', 'failed', ?, ?)",
                (str(followup_id), datetime.utcnow().isoformat()),
            )
            row = conn.execute(
                "SELECT COUNT(*) FROM agent_actions"
                " WHERE action_type='followup_send_failure' AND lead_id=?",
                (str(followup_id),),
            ).fetchone()
        return int(row[0]) if row else 1
    except Exception:
        # لا نستطيع العدّ — أعد الحد الأقصى حتى لا تعلق المتابعة في حلقة فشل صامتة
        return _MAX_SEND_FAILURES


def _queue_followup_card(lead_id: str) -> None:
    """سجّل أن بطاقة موافقة أُرسلت لهذا العميل وبانتظار الرد."""
    try:
        from employee.memory import _get_conn
        with _get_conn() as conn:
            conn.execute(
                "INSERT INTO agent_actions (action_type, description, result, lead_id, created_at)"
                " VALUES ('followup_card_queued', 'بطاقة موافقة متابعة أُرسلت', 'pending', ?, ?)",
                (lead_id, datetime.utcnow().isoformat()),
            )
    except Exception:
        pass


def _resolve_followup_card(lead_id: str) -> None:
    """سجّل أن البطاقة تم البتّ فيها (موافقة أو رفض)."""
    try:
        from employee.memory import _get_conn
        with _get_conn() as conn:
            conn.execute(
                "INSERT INTO agent_actions (action_type, description, result, lead_id, created_at)"
                " VALUES ('followup_card_resolved', 'بطاقة موافقة متابعة تمت معالجتها', 'done', ?, ?)",
                (lead_id, datetime.utcnow().isoformat()),
            )
    except Exception:
        pass


# بطاقة موافقة لم يُبتّ فيها خلال هذه المدة تُعتبر مهجورة ولا تحجب الدفعات
# التالية. بدون هذه المهلة يتحول الحارس إلى قفل دائم: بطاقة واحدة منسيّة
# توقف كل المتابعات إلى الأبد (حدث فعليًا — 177 بطاقة عالقة من 2026-06-10).
_CARD_TTL_HOURS = 48


def _count_pending_followup_cards() -> int:
    """
    عدد بطاقات المتابعة المرسلة خلال آخر _CARD_TTL_HOURS ولم يُبَتّ فيها بعد.

    تُطابَق كل بطاقة بمفردها عبر lead_id (بطاقة تُعتبر مبتوتة إذا وُجد سجل
    resolved لنفس العميل بعد وقت إرسالها)، بدلًا من طرح إجماليين تراكميين
    عبر كل التاريخ — الطرح التراكمي يختل عند أي resolved مفقود ولا يتعافى.
    """
    try:
        from employee.memory import _get_conn
        cutoff = (datetime.utcnow() - timedelta(hours=_CARD_TTL_HOURS)).isoformat()
        with _get_conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) FROM agent_actions q
                WHERE q.action_type = 'followup_card_queued'
                  AND q.created_at >= ?
                  AND NOT EXISTS (
                      SELECT 1 FROM agent_actions r
                      WHERE r.action_type = 'followup_card_resolved'
                        AND r.lead_id     = q.lead_id
                        AND r.created_at >= q.created_at
                  )
                """,
                (cutoff,),
            ).fetchone()
        return max(0, row[0] if row else 0)
    except Exception:
        return 0


def _was_sent_today(lead_id: str, today: str) -> bool:
    """
    True إذا تم إرسال رسالة متابعة لهذا العميل اليوم مسبقاً.
    يقرأ من جدول agent_actions في SQLite.
    """
    try:
        from employee.memory import _get_conn
        with _get_conn() as conn:
            row = conn.execute(
                """SELECT id FROM agent_actions
                   WHERE lead_id = ? AND action_type = 'seq_followup'
                   AND created_at LIKE ?
                   LIMIT 1""",
                (lead_id, f"{today}%"),
            ).fetchone()
        return row is not None
    except Exception:
        return False  # في حالة الخطأ نسمح بالإرسال


# ── تسلسل الإرسال: رسالتان فقط لكل عميل ─────────────────────────────────────
#
#   seq_welcome  → فور التسجيل (delay 0)
#   seq_followup → بعد يومين إذا لم يرد (delay 48h)
#   توقف تام بعدها — لا مزيد من الرسائل
#
FOLLOWUP_SEQUENCES: dict[str, list[dict]] = {
    "high":   [
        {"stage": "seq_welcome",  "delay_hours": 0},
        {"stage": "seq_followup", "delay_hours": 48},
    ],
    "medium": [
        {"stage": "seq_welcome",  "delay_hours": 0},
        {"stage": "seq_followup", "delay_hours": 48},
    ],
    "low": [
        {"stage": "seq_followup", "delay_hours": 48},
    ],
}

# ── نصوص الرسائل ─────────────────────────────────────────────────────────────

def _build_message(stage: str, name: str) -> str:
    """بناء نص الرسالة — قصير، مباشر، غير مزعج."""
    n = name if name and name not in ("عميل", "WhatsApp Contact", "") else ""

    if stage == "seq_welcome":
        greeting = f"أهلاً {n} 👋\n" if n else "أهلاً 👋\n"
        return (
            f"{greeting}"
            "معك Smart Field للنقل المبرد في الرياض.\n"
            "كيف نقدر نخدمك؟ 🌡️"
        )

    if stage == "seq_followup":
        greeting = f"مرحباً {n}،\n" if n else "مرحباً،\n"
        return (
            f"{greeting}"
            "نتابع معك بخصوص احتياجاتك في النقل المبرد.\n"
            "لو في أي استفسار نحن هنا. 🚐"
        )

    # fallback
    return (
        f"مرحباً{' ' + n if n else ''}، هل في شيء نقدر نساعدك فيه؟ 🌡️"
    )


class FollowUpEngine:
    """
    Sends scheduled WhatsApp messages to leads via WAHA.
    All steps are persisted in SQLite — safe across restarts.

    Usage:
        engine = FollowUpEngine(config, notifier)
        await engine.start_sequence(phone, name, priority)   # on lead creation
        await engine.run_due_followups()                     # every 2 hours by scheduler
    """

    # Prefix that distinguishes pipeline-scheduled steps from manual ones
    _SEQ_PREFIX = "seq_"

    def __init__(self, config: Any, notifier: "WhatsAppNotifier | None" = None) -> None:
        self.config = config
        self.notifier = notifier
        self._log = logger.bind(component="FollowUpEngine")

    def set_notifier(self, notifier: "WhatsAppNotifier") -> None:
        """Inject notifier after construction (e.g. from pipeline factory)."""
        self.notifier = notifier

    # ── Public API ────────────────────────────────────────────────────────────

    async def start_sequence(
        self,
        lead_phone: str,
        lead_name: str,
        priority: str,
    ) -> int:
        """
        Schedule all follow-up steps for a new lead in SQLite.
        Returns number of steps scheduled.
        """
        priority_key = str(priority).lower().replace("leadpriority.", "")
        sequence = FOLLOWUP_SEQUENCES.get(priority_key, FOLLOWUP_SEQUENCES["low"])

        self._log.info(
            "followup.sequence_start",
            phone=lead_phone,
            priority=priority_key,
            steps=len(sequence),
        )

        now = datetime.utcnow()
        scheduled = 0
        for step in sequence:
            send_at = now + timedelta(hours=step["delay_hours"])
            schedule_follow_up(
                lead_id=f"wa:{lead_phone}",
                lead_name=lead_name,
                lead_phone=lead_phone,
                crm_id=None,
                stage=step["stage"],
                # Pass exact datetime as hours_until=0 with a fake offset trick
                # Actually we write next_follow_up directly via hours_until
                hours_until=max(0, step["delay_hours"]),
            )
            scheduled += 1

        self._log.info("followup.sequence_scheduled", phone=lead_phone, scheduled=scheduled)
        return scheduled

    async def run_due_followups(self) -> int:
        """
        Send due follow-up messages — once per lead per day, max 2 messages total.
        Called by the scheduler every 2 hours.
        Returns the number of messages sent.
        """
        if not self.notifier:
            self._log.warning("followup.no_notifier — skipping")
            return 0

        due = get_due_follow_ups()
        seq_due = [f for f in due if str(f.get("stage", "")).startswith(self._SEQ_PREFIX)]

        today = datetime.utcnow().date().isoformat()   # "2026-06-10"
        sent_today: set[str] = set()                   # phones already messaged this run

        sent = 0
        for fu in seq_due:
            phone = fu.get("lead_phone", "")
            name  = fu.get("lead_name", "")
            stage = fu.get("stage", "")

            phone = _normalize_wa_phone(phone)
            if not phone:
                # أغلق السجل نهائيًا: رقم فاسد لن يصبح صالحًا بإعادة المحاولة،
                # وتركه مفتوحًا يعيد نفس الفشل كل ساعتين بلا نهاية.
                mark_follow_up_done(fu["id"], notes=f"رقم غير صالح: {fu.get('lead_phone', '')!r}")
                self._log.warning(
                    "followup.invalid_phone",
                    raw=fu.get("lead_phone", ""),
                    lead_id=fu.get("lead_id"),
                )
                continue

            # ── 1. لا ترسل لنفس الشخص مرتين في نفس اليوم ─────────────────
            if phone in sent_today:
                self._log.debug("followup.skip_already_sent_today", phone=phone)
                continue

            # ── 2. لا ترسل إذا تم الإرسال له اليوم مسبقاً (في run سابق) ──
            if _was_sent_today(fu["lead_id"], today):
                sent_today.add(phone)
                self._log.debug("followup.skip_sent_earlier_today", phone=phone)
                continue

            # ── 3. لا ترسل إذا العميل رد أو سُجِّل أو أغلق المحادثة ──────
            profile = get_lead_profile(phone)
            if profile.get("crm_registered") or profile.get("conv_closed"):
                mark_follow_up_done(fu["id"], notes="عميل مسجّل أو أغلق المحادثة")
                continue

            # ── 4. إرسال ─────────────────────────────────────────────────────
            msg = _build_message(stage, name)
            try:
                ok = await self.notifier.send_custom_message(phone, msg)
                if ok:
                    mark_follow_up_done(fu["id"], notes=f"أُرسل: {stage} | {today}")
                    log_action(
                        "seq_followup",
                        f"متابعة ({stage}) → {name or phone}",
                        "تم الإرسال",
                        fu["lead_id"],
                    )
                    sent_today.add(phone)
                    sent += 1
                    self._log.info("followup.sent", phone=phone, stage=stage)
                else:
                    failures = _bump_send_failure(fu["id"])
                    self._log.warning(
                        "followup.send_failed", phone=phone, stage=stage, failures=failures
                    )
                    if failures >= _MAX_SEND_FAILURES:
                        mark_follow_up_done(
                            fu["id"], notes=f"أُغلق بعد {failures} محاولات إرسال فاشلة"
                        )
                        self._log.warning(
                            "followup.gave_up", phone=phone, stage=stage, failures=failures
                        )
            except Exception as exc:
                self._log.error("followup.exception", phone=phone, error=str(exc))

            await asyncio.sleep(0.8)   # تجنب flood WAHA

        self._log.info("followup.run_complete", sent=sent, checked=len(seq_due))
        return sent

    # ── Convenience helpers (kept for backward compat) ───────────────────────

    async def send_pending_followups(self, supabase_client: Any = None) -> int:
        """Delegates to run_due_followups (Supabase client param kept for compat)."""
        return await self.run_due_followups()

    async def send_quote_message(
        self,
        phone: str,
        name: str,
        fleet_size: int = 0,
        budget: float = 0,
        cargo_type: str = "",
    ) -> bool:
        """Send a quote follow-up message via WAHA."""
        if not self.notifier:
            return False
        msg = (
            f"مرحباً {name} 📋\n"
            f"بناءً على احتياجاتك في نقل {cargo_type or 'البضاعة المبردة'}، "
            "سنُعدّ لك عرضاً مخصصاً.\n"
            "فريقنا سيتواصل معك خلال ساعات لتحديد التفاصيل. 🌡️"
        )
        return await self.notifier.send_custom_message(phone, msg)


# ── Creative Follow-up Engine ─────────────────────────────────────────────────
# (لا يتغير — يستخدم notifier.send_custom_message مباشرة → WAHA ✓)

class CreativeFollowupEngine:
    """
    3-attempt creative follow-up sequences with owner approval gate.
    - Segment A: no_reply_after_outbound — delays: 3/7/14 days
    - Segment B: inbound_went_cold      — delays: 2/5/10 days
    Owner approves each message via Telegram ✅/❌ before WhatsApp send.
    After 3 failed attempts: lead marked LOST automatically.
    """

    _NO_REPLY_DELAYS = [3, 7, 14]
    _COLD_DELAYS     = [2, 5, 10]

    _NO_REPLY_TEMPLATES = [
        "لاحظنا إن كثير من {category} في الرياض خسروا شحنات هذا الصيف بسبب انقطاع التبريد — شاركناك هذا عشان تكون على دراية 🌡️",
        "هذا الأسبوع أكملنا رحلات لعدد من {category} في الرياض بدون انقطاع واحد في سلسلة التبريد — لو حابت تجرب رحلة واحدة معنا الكلام لنا 📦",
        "هذه آخر مرة نتواصل — لو احتجت نقل مبرد موثوق في أي وقت، رقمنا محفوظ عندك 🤝",
    ]

    _COLD_TEMPLATES = [
        "مرحباً — متابعين معك بخصوص طلب النقل المبرد، هل في تفاصيل إضافية تحتاجها منا؟ 🚐",
        "نعرف إن الاختيار يحتاج وقت — عرضنا: رحلة تجريبية بدون التزام عشان تشوف الفرق بنفسك 📋",
        "نقدّر وقتك — لو قررت لاحقاً، نحن هنا. رقمنا محفوظ عندك دايم 🌡️",
    ]

    MAX_ATTEMPTS = 3

    # أقصى عدد بطاقات موافقة تُرسَل في الدورة الواحدة. بدون هذا السقف كانت
    # أول دورة بعد رفع الانسداد سترسل بطاقة لكل عميل مؤهَّل دفعةً واحدة
    # (336 عميلًا حاليًا) — إغراق لتيليجرام يجعل المراجعة البشرية مستحيلة.
    MAX_CARDS_PER_RUN = 10

    def __init__(
        self,
        crm: Any,
        notifier: Any,
        telegram: Any = None,
        owner_chat_ids: list[str] | None = None,
    ):
        self.crm = crm
        self.notifier = notifier
        self.telegram = telegram
        self.owner_chat_ids: list[str] = owner_chat_ids or []
        self._log = logger.bind(component="CreativeFollowupEngine")

    async def run_creative_sequence(self) -> dict:
        """Fetch follow-up candidates and send Telegram approval cards. Called every 2 hours."""
        results = {"approval_cards_sent": 0, "marked_lost": 0, "blocked": 0}

        # لا ترسل دفعة جديدة إذا لا تزال بطاقات سابقة بانتظار ردك
        pending = _count_pending_followup_cards()
        if pending > 0:
            self._log.info("creative_followup.blocked_pending_approval", pending=pending)
            results["blocked"] = pending
            return results

        leads = await self._fetch_followup_candidates()
        for lead in leads:
            count = int(lead.get("followup_count") or 0)
            if count >= self.MAX_ATTEMPTS:
                await self._mark_lost(lead)
                results["marked_lost"] += 1
                continue
            if self._is_due(lead, count):
                ok = await self._send_approval_card(lead, count)
                if ok:
                    _queue_followup_card(lead["id"])   # سجّل البطاقة كـ pending
                    results["approval_cards_sent"] += 1
                    if results["approval_cards_sent"] >= self.MAX_CARDS_PER_RUN:
                        self._log.info(
                            "creative_followup.batch_cap_reached",
                            sent=results["approval_cards_sent"],
                            remaining_candidates=len(leads) - leads.index(lead) - 1,
                        )
                        break
        return results

    def _is_due(self, lead: dict, attempt: int) -> bool:
        source = str(lead.get("source") or "")
        delays = self._COLD_DELAYS if "whatsapp" in source.lower() else self._NO_REPLY_DELAYS
        base_field = "followup_last_sent" if attempt > 0 else "updated_at"
        base = lead.get(base_field) or lead.get("created_at") or ""
        if not base:
            return True
        try:
            base_dt = datetime.fromisoformat(str(base).replace("Z", ""))
            return (datetime.utcnow() - base_dt).days >= delays[min(attempt, len(delays) - 1)]
        except Exception:
            return True

    async def _fetch_followup_candidates(self) -> list[dict]:
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: self.crm.client.table("leads")
                    .select("*")
                    .in_("status", ["new", "contacted"])
                    .not_.is_("phone", "null")
                    .neq("phone", "")
                    .execute()
            )
            return [r for r in (result.data or []) if int(r.get("followup_count") or 0) <= self.MAX_ATTEMPTS]
        except Exception as exc:
            self._log.error("followup.fetch_failed", error=str(exc))
            return []

    async def _send_approval_card(self, lead: dict, attempt: int) -> bool:
        if not self.telegram or not self.owner_chat_ids:
            return False
        source = str(lead.get("source") or "")
        is_cold = "whatsapp" in source.lower()
        templates = self._COLD_TEMPLATES if is_cold else self._NO_REPLY_TEMPLATES
        msg = templates[min(attempt, len(templates) - 1)].replace(
            "{category}", str(lead.get("category") or "العملاء")
        )
        attempt_ar = ["الأولى", "الثانية", "الثالثة"][min(attempt, 2)]
        text = (
            f"📩 *متابعة — المحاولة {attempt_ar}*\n"
            f"الاسم: {lead.get('name', '—')}\n"
            f"الهاتف: `{lead.get('phone', '—')}`\n"
            f"الشريحة: {'مراجعة واردة → انقطع' if is_cold else 'لم يرد على الـ Outreach'}\n\n"
            f"_الرسالة المقترحة:_\n{msg}"
        )
        keyboard = {"inline_keyboard": [[
            {"text": "✅ أرسل", "callback_data": f"followup_approve:{lead['id']}:{attempt}"},
            {"text": "❌ تخطى",  "callback_data": f"followup_reject:{lead['id']}"},
        ]]}
        ok = False
        for cid in self.owner_chat_ids:
            ok = await self.telegram.send_message_with_keyboard(cid, text, keyboard) or ok
        return ok

    async def handle_approval(self, lead_id: str, attempt: int, approved: bool) -> str:
        _resolve_followup_card(lead_id)   # سجّل البتّ بالبطاقة (موافقة أو رفض)
        if not approved:
            return "⏭ تم التخطي"
        lead = await self._get_lead(lead_id)
        if not lead:
            return "⚠️ العميل غير موجود"
        phone = (lead.get("phone") or "").strip()
        if not phone:
            return "⚠️ لا يوجد رقم هاتف"
        source = str(lead.get("source") or "")
        is_cold = "whatsapp" in source.lower()
        templates = self._COLD_TEMPLATES if is_cold else self._NO_REPLY_TEMPLATES
        msg = templates[min(attempt, len(templates) - 1)].replace(
            "{category}", str(lead.get("category") or "العملاء")
        )
        try:
            sent = await self.notifier.send_custom_message(phone, msg)
        except Exception:
            sent = False
        if sent:
            await self._update_followup_count(lead_id, attempt + 1)
            return f"✅ أُرسلت للمتابعة — {lead.get('name', '')}"
        return f"❌ فشل الإرسال — {phone}"

    async def _mark_lost(self, lead: dict) -> None:
        # Routed through update_deal_stage() (not a raw table update) so this
        # transition gets a deal_stage_history row like every other stage
        # change, and status stays in sync with deal_stage automatically.
        try:
            await self.crm.update_deal_stage(
                lead["id"],
                "LOST",
                changed_by="system",
                notes="[auto] تم إغلاق الملف بعد 3 محاولات متابعة بدون رد",
            )
        except Exception as exc:
            self._log.warning("followup.mark_lost_failed", error=str(exc))
        if self.telegram and self.owner_chat_ids:
            msg = f"🔕 *{lead.get('name', 'عميل')}* — تم إغلاق الملف بعد 3 محاولات بدون رد"
            for cid in self.owner_chat_ids:
                try:
                    await self.telegram.send_message(cid, msg)
                except Exception:
                    pass

    async def _get_lead(self, lead_id: str) -> dict | None:
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: self.crm.client.table("leads").select("*").eq("id", lead_id).limit(1).execute()
            )
            rows = result.data or []
            return rows[0] if rows else None
        except Exception:
            return None

    async def _update_followup_count(self, lead_id: str, new_count: int) -> None:
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None,
                lambda: self.crm.client.table("leads")
                    .update({
                        "followup_count": new_count,
                        "followup_last_sent": datetime.utcnow().isoformat(),
                        "status": "contacted",
                        "updated_at": datetime.utcnow().isoformat(),
                    })
                    .eq("id", lead_id)
                    .execute()
            )
        except Exception as exc:
            self._log.warning("followup.update_count_failed", error=str(exc))
