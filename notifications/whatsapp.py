"""
WhatsApp notification sender for the Smartfield sales team.
إرسال إشعارات واتساب لفريق مبيعات سمارت فيلد

Uses Twilio's WhatsApp API to send formatted Arabic messages
to configured sales team phone numbers.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog

from models.lead import Lead, LeadPriority, ProcessedLead

if TYPE_CHECKING:
    from config import Settings

logger = structlog.get_logger(__name__)

PRIORITY_EMOJI = {
    "high": "🔴",
    "medium": "🟡",
    "low": "🟢",
}

CATEGORY_AR = {
    "food_transport": "نقل مواد غذائية",
    "pharma_transport": "نقل أدوية ومستلزمات طبية",
    "industrial_cold": "تبريد صناعي",
    "retail_chain": "سلاسل تجزئة",
    "logistics_company": "شركة لوجستيات",
    "individual": "فرد / شركة صغيرة",
    "other": "أخرى",
}

SOURCE_AR = {
    "linkedin": "LinkedIn",
    "website": "الموقع الإلكتروني",
    "whatsapp": "واتساب",
    "google_forms": "نموذج Google",
    "ads": "إعلانات",
    "manual": "إدخال يدوي",
}


def _format_lead_message(lead: Lead, processed: ProcessedLead) -> str:
    """
    Build a formatted Arabic WhatsApp message about a new lead.
    يبني رسالة واتساب عربية منسقة عن عميل محتمل جديد.
    """
    priority_str = str(lead.priority).lower()
    priority_emoji = PRIORITY_EMOJI.get(priority_str, "⚪")
    category_ar = CATEGORY_AR.get(str(lead.category).lower(), str(lead.category))
    source_ar = SOURCE_AR.get(str(lead.source).lower(), str(lead.source))

    next_action = processed.next_actions[0] if processed.next_actions else "مراجعة العميل"

    lines = [
        "🚛 *عميل جديد - Smartfield*",
        "━━━━━━━━━━━━━━━━━━━━",
        f"👤 *الاسم:* {lead.name}",
    ]

    if lead.company:
        lines.append(f"🏢 *الشركة:* {lead.company}")
    if lead.phone:
        lines.append(f"📱 *الهاتف:* {lead.phone}")
    if lead.email:
        lines.append(f"📧 *البريد:* {lead.email}")

    lines.extend([
        f"📦 *نوع البضاعة:* {lead.cargo_type or 'غير محدد'}",
    ])

    if lead.route_from or lead.route_to:
        route = f"{lead.route_from or '؟'} ← {lead.route_to or '؟'}"
        lines.append(f"🗺️ *المسار:* {route}")

    if lead.fleet_size_needed:
        lines.append(f"🚚 *عدد الشاحنات:* {lead.fleet_size_needed}")

    if lead.budget_monthly:
        lines.append(f"💰 *الميزانية:* {lead.budget_monthly:,.0f} ريال/شهر")

    lines.extend([
        "━━━━━━━━━━━━━━━━━━━━",
        f"🏷️ *التصنيف:* {category_ar}",
        f"⭐ *التقييم:* {lead.score}/100",
        f"📊 *الأولوية:* {priority_emoji} {priority_str.upper()}",
        f"📥 *المصدر:* {source_ar}",
        "━━━━━━━━━━━━━━━━━━━━",
        f"✅ *الإجراء الفوري:*\n{next_action}",
    ])

    if lead.crm_id:
        lines.append(f"\n🔗 *رقم السجل في CRM:* `{lead.crm_id}`")

    return "\n".join(lines)


def _format_lead_summary(lead: Lead) -> str:
    """Build a shorter lead summary message."""
    priority_emoji = PRIORITY_EMOJI.get(str(lead.priority).lower(), "⚪")
    return (
        f"📋 *ملخص العميل*\n"
        f"الاسم: {lead.name}\n"
        f"الشركة: {lead.company or 'غير محدد'}\n"
        f"الهاتف: {lead.phone or 'غير محدد'}\n"
        f"التقييم: {lead.score}/100\n"
        f"الأولوية: {priority_emoji} {str(lead.priority).upper()}\n"
        f"البضاعة: {lead.cargo_type or 'غير محدد'}"
    )


class WhatsAppNotifier:
    """
    Sends WhatsApp notifications to the Smartfield sales team via Twilio.
    يرسل إشعارات واتساب لفريق مبيعات سمارت فيلد عبر Twilio.
    """

    def __init__(self, config: "Settings") -> None:
        self.config = config
        self._log = logger.bind(component="WhatsAppNotifier")
        self._twilio_client = None

        if config.is_twilio_configured():
            try:
                from twilio.rest import Client as TwilioClient

                self._twilio_client = TwilioClient(
                    config.TWILIO_ACCOUNT_SID,
                    config.TWILIO_AUTH_TOKEN,
                )
                self._log.info("Twilio client initialized")
            except ImportError:
                self._log.warning("Twilio package not installed; notifications disabled")
            except Exception as exc:
                self._log.error("Failed to initialize Twilio client", error=str(exc))

    async def notify_new_lead(self, lead: Lead, processed: ProcessedLead) -> None:
        """
        Send a new lead notification to all configured sales team numbers.
        يرسل إشعار عميل جديد لجميع أرقام فريق المبيعات المكوّنة.
        """
        if not self._twilio_client:
            self._log.warning("Twilio not configured; skipping new lead notification")
            return

        if not self.config.SALES_TEAM_WHATSAPP:
            self._log.warning("No SALES_TEAM_WHATSAPP numbers configured")
            return

        message_body = _format_lead_message(lead, processed)

        tasks = [
            self._send_message(phone, message_body)
            for phone in self.config.SALES_TEAM_WHATSAPP
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for phone, result in zip(self.config.SALES_TEAM_WHATSAPP, results):
            if isinstance(result, Exception):
                self._log.error("Failed to send notification", phone=phone, error=str(result))
            else:
                self._log.info("Notification sent", phone=phone, message_sid=result)

    async def send_lead_summary(self, phone: str, lead: Lead) -> bool:
        """
        Send a lead summary to a specific phone number.
        يرسل ملخص عميل لرقم هاتف محدد.
        """
        if not self._twilio_client:
            self._log.warning("Twilio not configured; skipping lead summary")
            return False

        message_body = _format_lead_summary(lead)
        try:
            sid = await self._send_message(phone, message_body)
            return bool(sid)
        except Exception as exc:
            self._log.error("send_lead_summary failed", phone=phone, error=str(exc))
            return False

    async def send_custom_message(self, phone: str, message: str) -> bool:
        """
        Send any custom message to a WhatsApp number.
        يرسل رسالة مخصصة لأي رقم واتساب.
        """
        if not self._twilio_client:
            return False
        try:
            await self._send_message(phone, message)
            return True
        except Exception as exc:
            self._log.error("send_custom_message failed", phone=phone, error=str(exc))
            return False

    async def _send_message(self, to_phone: str, body: str) -> str:
        """
        Execute the blocking Twilio API call in a thread pool executor
        so we don't block the async event loop.
        """
        # Normalize phone number format for Twilio
        if not to_phone.startswith("whatsapp:"):
            to_phone = f"whatsapp:{to_phone}"

        from_number = self.config.TWILIO_WHATSAPP_FROM
        if not from_number.startswith("whatsapp:"):
            from_number = f"whatsapp:{from_number}"

        loop = asyncio.get_event_loop()

        def _send() -> str:
            message = self._twilio_client.messages.create(
                body=body,
                from_=from_number,
                to=to_phone,
            )
            return message.sid

        sid = await loop.run_in_executor(None, _send)
        self._log.debug("Twilio message sent", to=to_phone, sid=sid)
        return sid
