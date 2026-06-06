"""
Outbound Sender — محرك إرسال الـ Outreach
Layer 2 of RevOS v6

يقرأ من leads (source='serpapi_prospecting', status='new', phone موجود)
يستخدم draft_message المحفوظ من google_maps_engine مباشرةً
يرسل عبر WAHA
يحدّث الحالة إلى 'contacted'
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING

import structlog

from agent.cold_outreach import build_outreach_for_prospect

if TYPE_CHECKING:
    from crm.supabase_crm import SupabaseCRM
    from notifications.whatsapp import WhatsAppNotifier

logger = structlog.get_logger(__name__)


class OutboundSender:
    """
    Reads serpapi prospects from the leads table and sends personalized
    WhatsApp messages via WAHA. Updates status to contacted after sending.
    """

    def __init__(
        self,
        crm: "SupabaseCRM",
        notifier: "WhatsAppNotifier",
        anthropic_api_key: str,
        daily_limit: int = 30,
    ) -> None:
        self.crm = crm
        self.notifier = notifier
        self.api_key = anthropic_api_key
        self.daily_limit = daily_limit
        self._log = logger.bind(component="OutboundSender")

    async def send_pending_outreach(self) -> dict:
        """
        Main entry point — called daily after morning prospecting.
        Sends to leads where source='serpapi_prospecting', phone is set, status='new'.
        """
        results = {
            "sent": 0,
            "skipped_no_phone": 0,
            "failed": 0,
            "errors": [],
            "date": datetime.utcnow().strftime("%Y-%m-%d"),
        }

        leads = await self._fetch_pending(limit=self.daily_limit)
        if not leads:
            self._log.info("outbound_sender.no_pending_leads")
            return results

        self._log.info("outbound_sender.starting", total=len(leads))

        for lead in leads:
            phone = (lead.get("phone") or "").strip()
            if not phone or phone == "+966500000000":
                results["skipped_no_phone"] += 1
                continue

            company = lead.get("name", "")
            raw = lead.get("raw_data") or {}
            if isinstance(raw, str):
                import json
                try:
                    raw = json.loads(raw)
                except Exception:
                    raw = {}

            try:
                # Use draft_message pre-written by google_maps_engine (Gemini)
                message = raw.get("draft_message", "").strip()
                if not message:
                    # Fallback: build from template
                    prospect = {"company": company, "city": "", "activity": ""}
                    message = build_outreach_for_prospect(prospect, "generic")

                # Send via WAHA
                sent = await self.notifier.send_custom_message(phone, message)

                if sent:
                    await self._mark_contacted(lead["id"], message)
                    results["sent"] += 1
                    self._log.info(
                        "outbound_sender.sent",
                        company=company,
                        phone=phone,
                    )
                else:
                    results["failed"] += 1

                # Throttle: 3 seconds between messages
                await asyncio.sleep(3)

            except Exception as exc:
                results["failed"] += 1
                results["errors"].append(f"{company}: {str(exc)[:80]}")
                self._log.error("outbound_sender.error", company=company, error=str(exc))

        self._log.info("outbound_sender.complete", **{k: v for k, v in results.items() if k != "errors"})
        return results

    async def _fetch_pending(self, limit: int) -> list[dict]:
        """Read serpapi prospects from leads table with phone set and status='new'."""
        try:
            result = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: self.crm.client.table("leads")
                    .select("*")
                    .eq("source", "serpapi_prospecting")
                    .eq("status", "new")
                    .not_.is_("phone", "null")
                    .neq("phone", "")
                    .order("score", desc=True)
                    .limit(limit)
                    .execute()
            )
            return result.data or []
        except Exception as exc:
            self._log.error("outbound_sender.fetch_failed", error=str(exc))
            return []

    async def _mark_contacted(self, lead_id: str, message: str) -> None:
        """Update lead status to contacted."""
        try:
            now = datetime.utcnow().isoformat()
            await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: self.crm.client.table("leads")
                    .update({
                        "status": "contacted",
                        "notes": f"[outbound] {message[:200]}",
                        "updated_at": now,
                    })
                    .eq("id", lead_id)
                    .execute()
            )
        except Exception as exc:
            self._log.warning("outbound_sender.mark_failed", lead_id=lead_id, error=str(exc))
