"""
Telegram notification sender for the Smartfield sales team.
إرسال إشعارات Telegram لفريق مبيعات سمارت فيلد

Uses Telegram Bot API to send formatted Arabic messages
to configured sales team chat IDs.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx
import structlog

from models.lead import Lead, ProcessedLead

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
    priority_str = str(lead.priority).lower()
    priority_emoji = PRIORITY_EMOJI.get(priority_str, "⚪")
    category_ar = CATEGORY_AR.get(str(lead.category).lower(), str(lead.category))
    source_ar = SOURCE_AR.get(str(lead.source).lower(), str(lead.source))
    next_action = processed.next_actions[0] if processed.next_actions else "مراجعة العميل"

    lines = [
        "🚛 *عميل جديد — Smartfield*",
        "━━━━━━━━━━━━━━━━━━━━",
        f"👤 *الاسم:* {lead.name}",
    ]

    if lead.company:
        lines.append(f"🏢 *الشركة:* {lead.company}")
    if lead.phone:
        lines.append(f"📱 *الهاتف:* {lead.phone}")
    if lead.email:
        lines.append(f"📧 *البريد:* {lead.email}")

    lines.append(f"📦 *نوع البضاعة:* {lead.cargo_type or 'غير محدد'}")

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


class TelegramNotifier:
    """
    Sends Telegram notifications to the Smartfield sales team via Bot API.
    يرسل إشعارات Telegram لفريق مبيعات سمارت فيلد عبر Bot API.
    """

    def __init__(self, config: "Settings") -> None:
        self.config = config
        self._log = logger.bind(component="TelegramNotifier")
        self._token = config.TELEGRAM_BOT_TOKEN
        self._chat_ids = config.TELEGRAM_OWNER_CHAT_IDS
        self._base_url = f"https://api.telegram.org/bot{self._token}"

        if self._token and self._chat_ids:
            self._log.info("Telegram notifier initialized", chat_count=len(self._chat_ids))
        else:
            self._log.warning("Telegram not configured; notifications disabled")

    def is_configured(self) -> bool:
        return bool(self._token and self._chat_ids)

    async def notify_new_lead(self, lead: Lead, processed: ProcessedLead) -> None:
        if not self.is_configured():
            self._log.warning("Telegram not configured; skipping notification")
            return

        message = _format_lead_message(lead, processed)
        tasks = [self._send_message(chat_id, message) for chat_id in self._chat_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for chat_id, result in zip(self._chat_ids, results):
            if isinstance(result, Exception):
                self._log.error("Failed to send Telegram notification", chat_id=chat_id, error=str(result))
            else:
                self._log.info("Telegram notification sent", chat_id=chat_id)

    async def send_lead_summary(self, chat_id: str, lead: Lead) -> bool:
        if not self.is_configured():
            return False
        message = _format_lead_summary(lead)
        try:
            await self._send_message(chat_id, message)
            return True
        except Exception as exc:
            self._log.error("send_lead_summary failed", chat_id=chat_id, error=str(exc))
            return False

    async def send_custom_message(self, chat_id: str | None, message: str) -> bool:
        """Send a message to a specific chat, or broadcast to all owner chats if chat_id is None."""
        if not self.is_configured():
            return False
        try:
            targets = [chat_id] if chat_id else self._chat_ids
            for cid in targets:
                await self._send_message(cid, message)
            return True
        except Exception as exc:
            self._log.error("send_custom_message failed", chat_id=chat_id, error=str(exc))
            return False

    async def _send_message(self, chat_id: str, text: str) -> dict:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{self._base_url}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                },
            )
            resp.raise_for_status()
            return resp.json()
