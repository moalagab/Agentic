"""
SLA Monitor — مراقبة مستوى الخدمة

Tracks lead response times and alerts when SLA thresholds are breached.

SLA Targets:
  HIGH priority  → response within 5 minutes
  MEDIUM priority → response within 15 minutes
  LOW priority   → response within 60 minutes
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)

SLA_THRESHOLDS = {
    "high":   5,    # minutes
    "medium": 15,   # minutes
    "low":    60,   # minutes
}

SLA_ALERT_MSG = """⏰ *تنبيه SLA — تجاوز وقت الاستجابة*

العميل: {name}
الهاتف: {phone}
الأولوية: {priority_ar}
وقت الاستلام: {created_at}
الوقت المنقضي: {elapsed_min:.0f} دقيقة

⚠️ الحد المسموح: {sla_limit} دقيقة
الإجراء المطلوب: تواصل فوري مع العميل!

رابط CRM: {crm_id}"""

PRIORITY_AR = {"high": "🔴 عالية", "medium": "🟡 متوسطة", "low": "🟢 منخفضة"}


class SLAMonitor:
    """
    Monitors lead response times and fires alerts on SLA breach.
    يراقب أوقات الاستجابة للعملاء ويرسل تنبيهات عند تجاوز SLA.
    """

    def __init__(self, notifier: Any, supabase_client: Any) -> None:
        self.notifier = notifier
        self.supabase = supabase_client
        self._log = logger.bind(component="SLAMonitor")
        # {lead_id: {"created_at": datetime, "priority": str, "name": str, "phone": str, "alerted": bool}}
        self._pending: dict[str, dict] = {}

    def register_lead(
        self,
        lead_id: str,
        name: str,
        phone: str,
        priority: str,
        crm_id: str = "",
    ) -> None:
        """Register a new lead for SLA tracking."""
        priority_key = str(priority).lower().replace("leadpriority.", "")
        self._pending[lead_id] = {
            "created_at": datetime.utcnow(),
            "priority": priority_key,
            "name": name,
            "phone": phone,
            "crm_id": crm_id,
            "alerted": False,
            "responded": False,
        }
        sla = SLA_THRESHOLDS.get(priority_key, 60)
        self._log.info("Lead registered for SLA", lead_id=lead_id, priority=priority_key, sla_minutes=sla)

        # Schedule SLA check
        asyncio.create_task(self._check_after_delay(lead_id, sla))

    def mark_responded(self, lead_id: str) -> Optional[int]:
        """
        Mark lead as responded. Returns response time in minutes.
        Call this when a sales rep contacts the lead.
        """
        entry = self._pending.get(lead_id)
        if not entry:
            return None

        elapsed = (datetime.utcnow() - entry["created_at"]).total_seconds() / 60
        entry["responded"] = True
        entry["response_time_min"] = round(elapsed)

        asyncio.create_task(self._save_response_time(lead_id, round(elapsed)))
        self._log.info("Lead responded", lead_id=lead_id, response_time_min=round(elapsed))
        return round(elapsed)

    async def check_pending_slas(self) -> int:
        """
        Scan all pending leads for SLA breaches.
        Called by scheduler every 2 minutes.
        """
        breached = 0
        now = datetime.utcnow()

        for lead_id, entry in list(self._pending.items()):
            if entry.get("responded") or entry.get("alerted"):
                continue

            elapsed_min = (now - entry["created_at"]).total_seconds() / 60
            sla_limit = SLA_THRESHOLDS.get(entry["priority"], 60)

            if elapsed_min > sla_limit:
                await self._send_sla_alert(lead_id, entry, elapsed_min, sla_limit)
                entry["alerted"] = True
                breached += 1

        return breached

    async def _check_after_delay(self, lead_id: str, delay_minutes: int) -> None:
        """Wait exactly SLA limit then check if still unresponded."""
        await asyncio.sleep(delay_minutes * 60)

        entry = self._pending.get(lead_id)
        if not entry or entry.get("responded") or entry.get("alerted"):
            return

        elapsed = (datetime.utcnow() - entry["created_at"]).total_seconds() / 60
        await self._send_sla_alert(lead_id, entry, elapsed, delay_minutes)
        entry["alerted"] = True

    async def _send_sla_alert(
        self, lead_id: str, entry: dict, elapsed_min: float, sla_limit: int
    ) -> None:
        """Send SLA breach alert via Telegram/WhatsApp."""
        msg = SLA_ALERT_MSG.format(
            name=entry["name"],
            phone=entry["phone"],
            priority_ar=PRIORITY_AR.get(entry["priority"], entry["priority"]),
            created_at=entry["created_at"].strftime("%H:%M:%S"),
            elapsed_min=elapsed_min,
            sla_limit=sla_limit,
            crm_id=entry.get("crm_id", "—"),
        )
        try:
            await self.notifier.send_custom_message(None, msg)
        except Exception as exc:
            self._log.error("SLA alert send failed", error=str(exc))

    async def _save_response_time(self, lead_id: str, response_time_min: int) -> None:
        """Persist response time to Supabase."""
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                lambda: self.supabase.table("leads").update({
                    "first_response_at": datetime.utcnow().isoformat(),
                    "response_time_minutes": response_time_min,
                    "updated_at": datetime.utcnow().isoformat(),
                }).eq("id", lead_id).execute()
            )
        except Exception as exc:
            self._log.warning("Failed to save response time", error=str(exc))

    def get_sla_stats(self) -> dict:
        """Return SLA compliance statistics."""
        total = len(self._pending)
        responded = [e for e in self._pending.values() if e.get("responded")]
        breached = [e for e in self._pending.values() if e.get("alerted")]
        on_time = [
            e for e in responded
            if e.get("response_time_min", 999) <= SLA_THRESHOLDS.get(e["priority"], 60)
        ]

        return {
            "total_tracked": total,
            "responded": len(responded),
            "breached_sla": len(breached),
            "on_time": len(on_time),
            "compliance_rate": round(len(on_time) / len(responded) * 100, 1) if responded else 0,
            "avg_response_time_min": round(
                sum(e.get("response_time_min", 0) for e in responded) / len(responded), 1
            ) if responded else 0,
        }
