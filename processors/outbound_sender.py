"""
Outbound Sender — محرك إرسال الـ Outreach
Layer 2 of RevOS v6

يقرأ من outbound_leads (status='new' + phone موجود)
يولّد رسالة مخصصة بـ Claude (صناعة + مدينة + حجم)
يرسل عبر WAHA
يحدّث الحالة إلى 'contacted'
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Any

import structlog

from agent.cold_outreach import generate_claude_message

if TYPE_CHECKING:
    from crm.supabase_crm import SupabaseCRM
    from notifications.whatsapp import WhatsAppNotifier

logger = structlog.get_logger(__name__)


class OutboundSender:
    """
    Reads outbound_leads with phone numbers and sends personalized
    WhatsApp messages via WAHA. Updates status after sending.
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
        Sends to outbound_leads where phone is set and status = 'new'.
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

            try:
                # Build prospect dict from outbound_leads row
                prospect = {
                    "company": lead.get("company_name", ""),
                    "city": lead.get("city", ""),
                    "activity": lead.get("industry", ""),
                    "cold_need": lead.get("raw_data", {}).get("cold_need", ""),
                    "fleet_est": lead.get("raw_data", {}).get("fleet_est", 3),
                    "budget_sar": lead.get("score", 0) * 400,  # rough estimate
                }
                segment_id = lead.get("raw_data", {}).get("segment_id", "generic")

                # Claude writes the message (industry + city + size)
                message = await generate_claude_message(prospect, segment_id, self.api_key)

                # Send via WAHA
                sent = await self.notifier.send_custom_message(phone, message)

                if sent:
                    # Update status → contacted
                    await self._mark_contacted(lead["id"], message)
                    results["sent"] += 1
                    self._log.info(
                        "outbound_sender.sent",
                        company=lead.get("company_name"),
                        city=lead.get("city"),
                        phone=phone,
                    )
                else:
                    results["failed"] += 1

                # Throttle: 3 seconds between messages
                await asyncio.sleep(3)

            except Exception as exc:
                results["failed"] += 1
                results["errors"].append(f"{lead.get('company_name')}: {str(exc)[:80]}")
                self._log.error("outbound_sender.error", company=lead.get("company_name"), error=str(exc))

        self._log.info("outbound_sender.complete", **{k: v for k, v in results.items() if k != "errors"})
        return results

    async def _fetch_pending(self, limit: int) -> list[dict]:
        """Read outbound_leads with phone set and status='new'."""
        try:
            result = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: self.crm.client.table("outbound_leads")
                    .select("*")
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
        """Update outbound_lead status to contacted."""
        try:
            now = datetime.utcnow().isoformat()
            await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: self.crm.client.table("outbound_leads")
                    .update({
                        "status": "contacted",
                        "outreach_message": message,
                        "updated_at": now,
                    })
                    .eq("id", lead_id)
                    .execute()
            )
        except Exception as exc:
            self._log.warning("outbound_sender.mark_failed", lead_id=lead_id, error=str(exc))
