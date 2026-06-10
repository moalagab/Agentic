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

# ── تسلسل الإرسال ─────────────────────────────────────────────────────────────

FOLLOWUP_SEQUENCES: dict[str, list[dict]] = {
    "high": [
        {"stage": "seq_welcome",       "delay_hours": 0},
        {"stage": "seq_service_intro", "delay_hours": 24},
        {"stage": "seq_trial_offer",   "delay_hours": 72},
    ],
    "medium": [
        {"stage": "seq_welcome",       "delay_hours": 1},
        {"stage": "seq_service_intro", "delay_hours": 72},
        {"stage": "seq_trial_offer",   "delay_hours": 168},
    ],
    "low": [
        {"stage": "seq_service_intro", "delay_hours": 72},
        {"stage": "seq_trial_offer",   "delay_hours": 168},
    ],
}

# ── نصوص الرسائل (عربي حر — لا templates) ────────────────────────────────────

def _build_message(stage: str, name: str) -> str:
    """بناء نص الرسالة بناءً على المرحلة واسم العميل."""
    greeting = f"{name}" if name and name not in ("عميل", "WhatsApp Contact", "") else "أهلاً"

    if stage == "seq_welcome":
        return (
            f"أهلاً {greeting} 👋\n"
            "شكراً على تواصلك مع Smart Field للنقل المبرد في الرياض.\n"
            "كيف نقدر نخدمك؟ 🌡️"
        )
    if stage == "seq_service_intro":
        return (
            f"مرحباً {greeting} 🚐\n"
            "نحن Smart Field — متخصصين في النقل المبرد الموثوق بالرياض.\n"
            "كل رحلة معنا تأتي مع تقرير حراري موثق يحمي بضاعتك.\n"
            "نرتّب معك رحلة تجريبية؟"
        )
    if stage == "seq_trial_offer":
        return (
            f"مرحباً {greeting} 🌡️\n"
            "ما زلنا جاهزين لخدمتك — لو احتجت نقل مبرد في أي وقت نحن هنا.\n"
            "رحلة تجريبية بدون التزام — تواصل معنا: wa.me/966561167169"
        )
    # fallback
    return (
        f"مرحباً {greeting}، نتابع معك بخصوص خدمات النقل المبرد من Smart Field.\n"
        "هل في أي استفسار نقدر نساعد فيه؟ 🌡️"
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
        Send all follow-up messages that are due now.
        Called by the scheduler every 2 hours.
        Returns the number of messages sent.
        """
        if not self.notifier:
            self._log.warning("followup.no_notifier — skipping")
            return 0

        due = get_due_follow_ups()
        # Only handle pipeline-sequenced steps (stage starts with "seq_")
        seq_due = [f for f in due if str(f.get("stage", "")).startswith(self._SEQ_PREFIX)]

        sent = 0
        for fu in seq_due:
            phone = fu.get("lead_phone", "")
            name  = fu.get("lead_name", "")
            stage = fu.get("stage", "")

            if not phone:
                mark_follow_up_done(fu["id"], notes="لا يوجد رقم")
                continue

            # Skip if customer already engaged (replied or registered in CRM)
            profile   = get_lead_profile(phone)
            if profile.get("crm_registered") or profile.get("conv_closed"):
                mark_follow_up_done(fu["id"], notes="عميل مسجّل أو أغلق المحادثة")
                continue

            msg = _build_message(stage, name)

            try:
                ok = await self.notifier.send_custom_message(phone, msg)
                if ok:
                    mark_follow_up_done(fu["id"], notes=f"أُرسل: {stage}")
                    log_action(
                        "seq_followup",
                        f"متابعة تلقائية ({stage}) → {name or phone}",
                        "تم الإرسال",
                        fu["lead_id"],
                    )
                    sent += 1
                    self._log.info("followup.sent", phone=phone, stage=stage)
                else:
                    self._log.warning("followup.send_failed", phone=phone, stage=stage)
            except Exception as exc:
                self._log.error("followup.exception", phone=phone, error=str(exc))

            await asyncio.sleep(0.5)   # avoid flooding WAHA

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
        results = {"approval_cards_sent": 0, "marked_lost": 0}
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
                    results["approval_cards_sent"] += 1
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
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None,
                lambda: self.crm.client.table("leads")
                    .update({
                        "deal_stage": "LOST",
                        "status": "lost",
                        "notes": "[auto] تم إغلاق الملف بعد 3 محاولات متابعة بدون رد",
                        "updated_at": datetime.utcnow().isoformat(),
                    })
                    .eq("id", lead["id"])
                    .execute()
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
