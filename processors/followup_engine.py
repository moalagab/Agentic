"""
WhatsApp Follow-up Sequence Engine
محرك متابعة العملاء عبر واتساب

Automated outbound follow-up sequences based on lead priority and status.
Sequences:
  HIGH priority  → Day 0 (instant), Day 1, Day 3
  MEDIUM priority → Day 0 (instant), Day 3, Day 7
  LOW priority   → Day 3, Day 7
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Optional

import httpx
import structlog

logger = structlog.get_logger(__name__)


FOLLOWUP_SEQUENCES = {
    "high": [
        {"day": 0, "template": "sf_welcome",            "delay_hours": 0},
        {"day": 1, "template": "sf_service_intro",      "delay_hours": 24},
        {"day": 3, "template": "sf_followup_trial",     "delay_hours": 72},
    ],
    "medium": [
        {"day": 0, "template": "sf_welcome",            "delay_hours": 1},
        {"day": 3, "template": "sf_service_intro",      "delay_hours": 72},
        {"day": 7, "template": "sf_followup_trial",     "delay_hours": 168},
    ],
    "low": [
        {"day": 3, "template": "sf_service_intro",      "delay_hours": 72},
        {"day": 7, "template": "sf_followup_trial",     "delay_hours": 168},
    ],
}

TEMPLATE_SIDS = {
    "sf_welcome":          "HX645f22c5ffa41019a6b3c97a092f0b22",
    "sf_service_intro":    "HXeed6901a73d7573691693d59332ca4c4",
    "sf_followup_trial":   "HX2f707929467ebaf1e2016daf154e4ecd",
    "sf_quote":            "HXd29fab1a15eadb4821180a401edcb6e8",
    "sf_order_confirmation":"HXd702a91d379b39d4ba6e22013b33348f",
    "sf_delivery_scheduled":"HX4645f0afd5c5aad8e34e495943e81851",
    "sf_delivery_complete": "HX4a60debbcb488644c198a05ce7b4e7ee",
}


class FollowUpEngine:
    """
    Sends scheduled WhatsApp messages to leads based on their priority.
    يرسل رسائل واتساب مجدولة للعملاء بناءً على أولويتهم.
    """

    def __init__(self, config: Any) -> None:
        self.config = config
        self._log = logger.bind(component="FollowUpEngine")

    async def start_sequence(self, lead_phone: str, lead_name: str, priority: str) -> None:
        """
        Kick off the follow-up sequence for a new lead.
        تبدأ سلسلة المتابعة لعميل جديد.
        """
        priority_key = str(priority).lower().replace("leadpriority.", "")
        sequence = FOLLOWUP_SEQUENCES.get(priority_key, FOLLOWUP_SEQUENCES["low"])

        self._log.info(
            "Starting follow-up sequence",
            lead_phone=lead_phone,
            priority=priority_key,
            steps=len(sequence),
        )

        for step in sequence:
            delay = step["delay_hours"] * 3600
            asyncio.create_task(
                self._delayed_send(
                    delay_seconds=delay,
                    phone=lead_phone,
                    name=lead_name,
                    template=step["template"],
                    day=step["day"],
                )
            )

    async def _delayed_send(
        self,
        delay_seconds: float,
        phone: str,
        name: str,
        template: str,
        day: int,
    ) -> None:
        """Wait then send a WhatsApp template message."""
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)

        await self.send_template(phone=phone, name=name, template=template)

    async def send_template(self, phone: str, name: str, template: str) -> bool:
        """
        Send a Twilio WhatsApp template message to a lead.
        يرسل قالب واتساب عبر Twilio للعميل.
        """
        if not self.config.is_twilio_configured():
            self._log.warning("Twilio not configured — cannot send follow-up")
            return False

        template_sid = TEMPLATE_SIDS.get(template)
        if not template_sid:
            self._log.warning("Unknown template", template=template)
            return False

        # Normalize phone
        to_phone = phone if phone.startswith("+") else f"+{phone}"
        from_number = self.config.TWILIO_WHATSAPP_FROM  # whatsapp:+1...

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"https://api.twilio.com/2010-04-01/Accounts/{self.config.TWILIO_ACCOUNT_SID}/Messages.json",
                    auth=(self.config.TWILIO_ACCOUNT_SID, self.config.TWILIO_AUTH_TOKEN),
                    data={
                        "From": from_number,
                        "To": f"whatsapp:{to_phone}",
                        "ContentSid": template_sid,
                        "ContentVariables": f'{{"1":"{name}"}}',
                    },
                )

            if resp.status_code in (200, 201):
                self._log.info("Follow-up sent", template=template, phone=to_phone, sid=resp.json().get("sid"))
                return True
            else:
                self._log.warning("Follow-up send failed", status=resp.status_code, body=resp.text[:200])
                return False

        except Exception as exc:
            self._log.error("Follow-up exception", error=str(exc))
            return False

    async def send_quote_message(
        self,
        phone: str,
        name: str,
        fleet_size: int,
        budget: float,
        cargo_type: str,
    ) -> bool:
        """Send the quote template with lead-specific variables."""
        return await self.send_template(phone=phone, name=name, template="sf_quote")

    async def send_pending_followups(self, supabase_client: Any) -> int:
        """
        Check Supabase for leads that need follow-up and send messages.
        يفحص قاعدة البيانات ويرسل رسائل المتابعة المستحقة.

        Called by the scheduler every 2 hours.
        """
        sent_count = 0
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                lambda: supabase_client.table("leads")
                    .select("id,name,phone,priority,status,score,created_at,follow_up_date")
                    .in_("status", ["new", "contacted"])
                    .not_.is_("phone", "null")
                    .execute()
            )

            now = datetime.utcnow()
            for lead in (result.data or []):
                follow_up_date = lead.get("follow_up_date")
                if not follow_up_date:
                    continue

                due = datetime.fromisoformat(follow_up_date.replace("Z", ""))
                if due <= now:
                    phone = lead.get("phone", "")
                    name = lead.get("name", "")
                    priority = lead.get("priority", "low")

                    if phone:
                        sent = await self.send_template(
                            phone=phone,
                            name=name,
                            template="sf_followup_trial",
                        )
                        if sent:
                            sent_count += 1
                            # Clear follow_up_date so we don't resend
                            await loop.run_in_executor(
                                None,
                                lambda lid=lead["id"]: supabase_client.table("leads")
                                    .update({"follow_up_date": None, "status": "contacted"})
                                    .eq("id", lid)
                                    .execute()
                            )

        except Exception as exc:
            self._log.error("send_pending_followups failed", error=str(exc))

        self._log.info("Pending follow-ups processed", sent=sent_count)
        return sent_count


# ── Creative Follow-up Engine (FIX-005) ────────────────────────────────────────

class CreativeFollowupEngine:
    """
    3-attempt creative follow-up sequences with owner approval gate.
    - Segment A: no_reply_after_outbound — delays: 3/7/14 days
    - Segment B: inbound_went_cold — delays: 2/5/10 days
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

    def __init__(self, crm: Any, notifier: Any, telegram: Any = None, owner_chat_ids: list[str] | None = None):
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

    async def _get_lead(self, lead_id: str) -> Optional[dict]:
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
