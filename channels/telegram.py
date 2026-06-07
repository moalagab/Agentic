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
        """Send a message to a Telegram chat. Falls back to plain text on Markdown parse errors."""
        url = TELEGRAM_API.format(token=self.token, method="sendMessage")
        for parse_mode in ("Markdown", None):
            payload: dict = {"chat_id": chat_id, "text": text}
            if parse_mode:
                payload["parse_mode"] = parse_mode
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    r = await client.post(url, json=payload)
                    data = r.json()
                    if data.get("ok"):
                        return True
                    desc = data.get("description", "")
                    if parse_mode and "parse entities" in desc:
                        self._log.warning("Telegram Markdown parse failed, retrying as plain text")
                        continue
                    self._log.warning("Telegram send failed", response=data)
                    return False
            except Exception as exc:
                self._log.error("send_message failed", chat_id=chat_id, error=str(exc))
                return False
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

    async def send_message_with_keyboard(
        self,
        chat_id: str,
        text: str,
        inline_keyboard: dict,
    ) -> bool:
        """Send a message with an inline keyboard (approval buttons)."""
        url = TELEGRAM_API.format(token=self.token, method="sendMessage")
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "reply_markup": inline_keyboard,
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(url, json=payload)
                data = r.json()
                if data.get("ok"):
                    return True
                # Retry without Markdown on parse error
                if "parse entities" in data.get("description", ""):
                    payload.pop("parse_mode", None)
                    r2 = await client.post(url, json=payload)
                    return r2.json().get("ok", False)
                self._log.warning("send_keyboard failed", response=data)
                return False
        except Exception as exc:
            self._log.error("send_message_with_keyboard failed", chat_id=chat_id, error=str(exc))
            return False

    async def answer_callback_query(self, callback_query_id: str, text: str = "") -> bool:
        """Acknowledge an inline button press (removes loading spinner)."""
        url = TELEGRAM_API.format(token=self.token, method="answerCallbackQuery")
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.post(url, json={
                    "callback_query_id": callback_query_id,
                    "text": text,
                    "show_alert": False,
                })
                return r.json().get("ok", False)
        except Exception:
            return False

    def extract_callback_query(self, payload: dict) -> Optional[dict]:
        """Extract callback_query data from a Telegram update (inline button press)."""
        cq = payload.get("callback_query")
        if not cq:
            return None
        return {
            "id":      cq.get("id"),
            "chat_id": str(cq.get("message", {}).get("chat", {}).get("id", "")),
            "data":    cq.get("data", ""),
            "from":    cq.get("from", {}).get("first_name", ""),
        }
