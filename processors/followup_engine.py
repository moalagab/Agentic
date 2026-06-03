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
