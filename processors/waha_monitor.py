"""
WAHA WhatsApp Session Monitor — آلية الربط الدائم
يراقب حالة الجلسة كل 5 دقائق ويعيد الربط تلقائياً عند الانقطاع.

الحالات وردود الفعل:
  CONNECTED    → لا شيء
  STOPPED      → start (يحاول الربط بالبيانات المحفوظة)
  FAILED       → stop → start (إعادة تشغيل نظيفة)
  SCAN_QR_CODE → إشعار تيليغرام برابط QR (مرة كل 10 دقائق)
  STARTING     → انتظار
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional

import httpx
import structlog

if TYPE_CHECKING:
    from channels.telegram import TelegramHandler

logger = structlog.get_logger(__name__)

_QR_PAGE_URL = "https://agent.smartfield.sa/qr"
_ALERT_COOLDOWN_MINUTES = 10


class WAHAMonitor:
    """
    Monitors WAHA session and auto-reconnects on failure.
    يعمل بشكل مستقل — لا يحتاج تدخل بشري إلا عند الحاجة لمسح QR.
    """

    def __init__(
        self,
        waha_url: str,
        api_key: str,
        session: str = "default",
        telegram: Optional["TelegramHandler"] = None,
        owner_chat_ids: Optional[list[str]] = None,
    ) -> None:
        self.waha_url = waha_url.rstrip("/")
        self.api_key = api_key
        self.session = session
        self.telegram = telegram
        self.owner_chat_ids = owner_chat_ids or []
        self._last_qr_alert: Optional[datetime] = None
        self._log = logger.bind(component="WAHAMonitor")

    # ── Public entry point ────────────────────────────────────────────────────

    async def check_and_reconnect(self) -> str:
        """
        Main health check — called by scheduler every 5 minutes.
        Returns the resulting session status string.
        """
        status = await self._get_status()
        self._log.debug("waha.health_check", status=status)

        if status in ("CONNECTED", "WORKING"):
            return status

        if status == "STARTING":
            return status

        if status in ("STOPPED", None):
            await self._start_session()
            await asyncio.sleep(5)
            status = await self._get_status()

        elif status == "FAILED":
            self._log.warning("waha.session_failed", action="restarting")
            await self._stop_session()
            await asyncio.sleep(3)
            await self._start_session()
            await asyncio.sleep(8)
            status = await self._get_status()

        if status == "SCAN_QR_CODE":
            await self._maybe_alert_qr()

        self._log.info("waha.health_check_done", status=status)
        return status or "UNKNOWN"

    # ── WAHA API calls ────────────────────────────────────────────────────────

    async def _get_status(self) -> Optional[str]:
        url = f"{self.waha_url}/api/sessions/{self.session}"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(url, headers={"X-Api-Key": self.api_key})
                if r.status_code == 404:
                    return None
                return r.json().get("status")
        except Exception as exc:
            self._log.warning("waha.get_status_error", error=str(exc))
            return None

    async def _start_session(self) -> None:
        url = f"{self.waha_url}/api/sessions/start"
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(
                    url,
                    headers={"X-Api-Key": self.api_key, "Content-Type": "application/json"},
                    json={"name": self.session},
                )
                self._log.info("waha.session_start", status_code=r.status_code)
        except Exception as exc:
            self._log.error("waha.start_error", error=str(exc))

    async def _stop_session(self) -> None:
        url = f"{self.waha_url}/api/sessions/{self.session}/stop"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(url, headers={"X-Api-Key": self.api_key})
        except Exception as exc:
            self._log.warning("waha.stop_error", error=str(exc))

    # ── Telegram QR alert ─────────────────────────────────────────────────────

    async def _maybe_alert_qr(self) -> None:
        now = datetime.now()
        if self._last_qr_alert:
            if now - self._last_qr_alert < timedelta(minutes=_ALERT_COOLDOWN_MINUTES):
                return

        self._last_qr_alert = now
        msg = (
            "⚠️ *واتساب يحتاج مسح QR*\n"
            "━━━━━━━━━━━━━━\n"
            f"افتح الرابط وامسح الكود:\n"
            f"🔗 {_QR_PAGE_URL}\n\n"
            "_واتساب → الأجهزة المرتبطة → ربط جهاز_"
        )
        if self.telegram and self.owner_chat_ids:
            for chat_id in self.owner_chat_ids:
                try:
                    await self.telegram.send_message(chat_id, msg)
                except Exception as exc:
                    self._log.warning("waha.qr_alert_error", chat_id=chat_id, error=str(exc))
        self._log.info("waha.qr_alert_sent")
