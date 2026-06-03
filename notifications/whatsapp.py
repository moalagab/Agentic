"""
WhatsApp notification sender for the Smartfield sales team.
إرسال إشعارات واتساب لفريق مبيعات سمارت فيلد

Supports two backends:
  1. WAHA (self-hosted WhatsApp HTTP API) — preferred
  2. Twilio WhatsApp — fallback
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import httpx
import structlog

from models.lead import Lead, LeadPriority, ProcessedLead

if TYPE_CHECKING:
    from config import Settings

logger = structlog.get_logger(__name__)

PRIORITY_LABEL = {
    "high": "عالية",
    "medium": "متوسطة",
    "low": "منخفضة",
}

PRIORITY_MARKER = {
    "high": "●",
    "medium": "◑",
    "low": "○",
}

CATEGORY_AR = {
    "food_transport": "نقل غذاء",
    "pharma_transport": "نقل أدوية",
    "industrial_cold": "تبريد صناعي",
    "retail_chain": "سلاسل تجزئة",
    "logistics_company": "لوجستيات",
    "individual": "فرد / شركة صغيرة",
    "other": "أخرى",
}

SOURCE_AR = {
    "linkedin": "LinkedIn",
    "website": "الموقع",
    "whatsapp": "واتساب",
    "google_forms": "Google Forms",
    "ads": "إعلانات",
    "manual": "يدوي",
}


def _format_lead_message(lead: Lead, processed: ProcessedLead) -> str:
    priority_str = str(lead.priority).lower()
    marker = PRIORITY_MARKER.get(priority_str, "◑")
    priority_ar = PRIORITY_LABEL.get(priority_str, priority_str.upper())
    category_ar = CATEGORY_AR.get(str(lead.category).lower(), str(lead.category))
    source_ar = SOURCE_AR.get(str(lead.source).lower(), str(lead.source))
    next_action = processed.next_actions[0] if processed.next_actions else "مراجعة العميل"

    lines = [
        "*عميل جديد — سمارت فيلد*",
        "━━━━━━━━━━━━━━━━━━━━",
        f"الاسم: {lead.name}",
    ]
    if lead.company:
        lines.append(f"الشركة: {lead.company}")
    if lead.phone:
        lines.append(f"الهاتف: {lead.phone}")
    if lead.email:
        lines.append(f"البريد: {lead.email}")

    lines.append(f"البضاعة: {lead.cargo_type or 'غير محدد'}")

    if lead.route_from or lead.route_to:
        route = f"{lead.route_from or '؟'} ← {lead.route_to or '؟'}"
        lines.append(f"المسار: {route}")
    if lead.fleet_size_needed:
        lines.append(f"الشاحنات: {lead.fleet_size_needed}")
    if lead.budget_monthly:
        lines.append(f"الميزانية: {lead.budget_monthly:,.0f} ريال/شهر")

    lines.extend([
        "━━━━━━━━━━━━━━━━━━━━",
        f"التصنيف: {category_ar}",
        f"التقييم: {lead.score}/100  |  {marker} {priority_ar}",
        f"المصدر: {source_ar}",
        "━━━━━━━━━━━━━━━━━━━━",
        f"الإجراء التالي: {next_action}",
    ])
    if lead.crm_id:
        lines.append(f"CRM: `{lead.crm_id}`")

    return "\n".join(lines)


def _format_lead_summary(lead: Lead) -> str:
    priority_str = str(lead.priority).lower()
    priority_ar = PRIORITY_LABEL.get(priority_str, priority_str.upper())
    return (
        f"*ملخص العميل*\n"
        f"الاسم: {lead.name}\n"
        f"الشركة: {lead.company or 'غير محدد'}\n"
        f"الهاتف: {lead.phone or 'غير محدد'}\n"
        f"التقييم: {lead.score}/100  |  {priority_ar}\n"
        f"البضاعة: {lead.cargo_type or 'غير محدد'}"
    )


def _normalize_chat_id(phone: str) -> str:
    """Convert phone/chatId to WAHA chatId format.
    Accepts: +966xxx, 966xxx, 966xxx@c.us, 966xxx@lid
    """
    p = phone.strip()
    # Already has @suffix — pass as-is
    if "@" in p:
        return p
    # Strip + and spaces
    num = p.lstrip("+").replace(" ", "").replace("-", "")
    return f"{num}@c.us"


class WhatsAppNotifier:
    """
    Sends WhatsApp notifications via WAHA (preferred) or Twilio (fallback).
    """

    def __init__(self, config: "Settings") -> None:
        self.config = config
        self._log = logger.bind(component="WhatsAppNotifier")
        self._twilio_client = None
        self._waha_enabled = False

        # ── WAHA ──────────────────────────────────────────────────────────────
        if config.WAHA_API_KEY:
            self._waha_url = config.WAHA_URL.rstrip("/")
            self._waha_session = config.WAHA_SESSION
            self._waha_headers = {
                "X-Api-Key": config.WAHA_API_KEY,
                "Content-Type": "application/json",
            }
            self._waha_enabled = True
            self._log.info("WAHA notifier enabled", url=self._waha_url)

        # ── Twilio fallback ────────────────────────────────────────────────────
        elif config.is_twilio_configured():
            try:
                self._twilio_client = config.get_twilio_client()
                self._log.info("Twilio fallback initialized")
            except Exception as exc:
                self._log.error("Twilio init failed", error=str(exc))

    # ─── Public API ───────────────────────────────────────────────────────────

    async def notify_new_lead(self, lead: Lead, processed: ProcessedLead) -> None:
        if not self.config.SALES_TEAM_WHATSAPP:
            self._log.warning("No SALES_TEAM_WHATSAPP numbers configured")
            return

        message = _format_lead_message(lead, processed)
        tasks = [self.send_custom_message(phone, message) for phone in self.config.SALES_TEAM_WHATSAPP]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def send_lead_summary(self, phone: str, lead: Lead) -> bool:
        return await self.send_custom_message(phone, _format_lead_summary(lead))

    async def send_custom_message(self, phone: str | None, message: str) -> bool:
        targets = [phone] if phone else self.config.SALES_TEAM_WHATSAPP
        results = await asyncio.gather(
            *[self._send(t, message) for t in targets if t],
            return_exceptions=True,
        )
        return all(r is True for r in results)

    # ─── Internal senders ─────────────────────────────────────────────────────

    async def _send(self, phone: str, body: str) -> bool:
        if self._waha_enabled:
            return await self._send_waha(phone, body)
        elif self._twilio_client:
            return bool(await self._send_twilio(phone, body))
        else:
            self._log.warning("No WhatsApp backend configured")
            return False

    async def _resolve_lid_to_cus(self, lid: str) -> str:
        """Resolve a @lid identifier to phone@c.us via WAHA chats API."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self._waha_url}/api/{self._waha_session}/chats",
                    headers=self._waha_headers,
                )
                if resp.status_code == 200:
                    chats = resp.json()
                    for chat in chats:
                        cid = chat.get("id", {})
                        serialized = cid.get("_serialized", "") if isinstance(cid, dict) else str(cid)
                        if serialized == lid:
                            name = chat.get("name", "")
                            # Extract digits from display name like "+966 50 998 9313"
                            digits = "".join(c for c in name if c.isdigit())
                            if digits and len(digits) >= 9:
                                self._log.info("LID resolved", lid=lid, phone=digits)
                                return f"{digits}@c.us"
        except Exception as exc:
            self._log.warning("LID resolution failed", lid=lid, error=str(exc))
        return lid  # fallback to original

    async def _send_waha(self, phone: str, body: str) -> bool:
        chat_id = _normalize_chat_id(phone)

        # Resolve @lid to @c.us — WAHA can't send to @lid format
        if chat_id.endswith("@lid"):
            chat_id = await self._resolve_lid_to_cus(chat_id)

        payload = {
            "session": self._waha_session,
            "chatId": chat_id,
            "text": body,
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{self._waha_url}/api/sendText",
                    headers=self._waha_headers,
                    json=payload,
                )
                resp.raise_for_status()
                self._log.info("WAHA message sent", to=phone, chat_id=chat_id)
                return True
        except Exception as exc:
            self._log.error("WAHA send failed", to=phone, error=str(exc))
            return False

    async def _send_twilio(self, to_phone: str, body: str) -> str:
        if not to_phone.startswith("whatsapp:"):
            to_phone = f"whatsapp:{to_phone}"
        from_number = self.config.TWILIO_WHATSAPP_FROM
        if not from_number.startswith("whatsapp:"):
            from_number = f"whatsapp:{from_number}"

        loop = asyncio.get_running_loop()

        def _send() -> str:
            msg = self._twilio_client.messages.create(
                body=body, from_=from_number, to=to_phone
            )
            return msg.sid

        sid = await loop.run_in_executor(None, _send)
        self._log.info("Twilio message sent", to=to_phone, sid=sid)
        return sid
