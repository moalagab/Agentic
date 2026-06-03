"""
Telegram Bot channel handler for Smartfield.
قناة تيليغرام - الأسهل والأسرع للربط
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import httpx
import structlog

from models.lead import LeadCreate, LeadSource

logger = structlog.get_logger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


class TelegramHandler:
    """
    Handles Telegram Bot API webhooks and sends messages back.
    يستقبل رسائل تيليغرام ويرد عليها.
    """

    def __init__(self, bot_token: str):
        self.token = bot_token
        self._log = logger.bind(channel="Telegram")

    def extract_message(self, payload: dict[str, Any]) -> Optional[dict[str, Any]]:
        """
        Extract chat_id, user info, and text from a Telegram update.
        يستخرج معرف المحادثة والنص من تحديث تيليغرام.
        """
        try:
            message = payload.get("message") or payload.get("edited_message")
            if not message:
                return None

            text = message.get("text", "").strip()
            if not text:
                return None

            chat = message.get("chat", {})
            user = message.get("from", {})

            chat_id = str(chat.get("id", ""))
            first = user.get("first_name", "")
            last = user.get("last_name", "")
            name = f"{first} {last}".strip() or "Telegram User"
            username = user.get("username", "")

            return {
                "chat_id": chat_id,
                "name": name,
                "username": username,
                "text": text,
                "message_id": message.get("message_id"),
            }
        except Exception as exc:
            self._log.error("extract_message failed", error=str(exc))
            return None

    async def send_message(self, chat_id: str, text: str) -> bool:
        """Send a message to a Telegram chat."""
        url = TELEGRAM_API.format(token=self.token, method="sendMessage")
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(url, json=payload)
                ok = r.json().get("ok", False)
                if not ok:
                    self._log.warning("Telegram send failed", response=r.json())
                return ok
        except Exception as exc:
            self._log.error("send_message failed", chat_id=chat_id, error=str(exc))
            return False

    async def set_webhook(self, webhook_url: str) -> bool:
        """Register the webhook URL with Telegram."""
        url = TELEGRAM_API.format(token=self.token, method="setWebhook")
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(url, json={"url": webhook_url})
                result = r.json()
                ok = result.get("ok", False)
                self._log.info("setWebhook", ok=ok, description=result.get("description"))
                return ok
        except Exception as exc:
            self._log.error("set_webhook failed", error=str(exc))
            return False

    async def delete_webhook(self) -> bool:
        """Remove the webhook (switch to polling mode)."""
        url = TELEGRAM_API.format(token=self.token, method="deleteWebhook")
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(url)
                return r.json().get("ok", False)
        except Exception:
            return False

    async def send_typing(self, chat_id: str):
        """Show typing indicator."""
        url = TELEGRAM_API.format(token=self.token, method="sendChatAction")
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post(url, json={"chat_id": chat_id, "action": "typing"})
        except Exception:
            pass
