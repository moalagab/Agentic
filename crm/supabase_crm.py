"""
Supabase CRM integration for Smartfield Lead Generation System.
تكامل Supabase كطبقة تخزين موثوقة للعملاء المحتملين

Supabase is used as the primary persistent storage — every lead is saved here
first, regardless of HubSpot/Airtable status. This ensures no lead is ever lost.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Optional

import structlog
from supabase import create_client, Client

from crm.base import BaseCRM
from models.lead import Lead, LeadCategory, LeadCreate, LeadPriority, LeadSource, LeadStatus

logger = structlog.get_logger(__name__)


def _lead_to_row(lead: Lead) -> dict[str, Any]:
    """Map Lead model to Supabase table row."""
    row: dict[str, Any] = {
        "id": lead.id,
        "name": lead.name,
        "company": lead.company,
        "phone": lead.phone,
        "email": lead.email,
        "source": lead.source.value if hasattr(lead.source, "value") else str(lead.source),
        "status": lead.status.value if hasattr(lead.status, "value") else str(lead.status),
        "priority": lead.priority.value if hasattr(lead.priority, "value") else str(lead.priority),
        "category": lead.category.value if hasattr(lead.category, "value") else str(lead.category),
        "cargo_type": lead.cargo_type,
        "route_from": lead.route_from,
        "route_to": lead.route_to,
        "fleet_size_needed": lead.fleet_size_needed,
        "budget_monthly": float(lead.budget_monthly) if lead.budget_monthly else None,
        "notes": lead.notes,
        "raw_data": lead.raw_data or {},
        "crm_id": lead.crm_id,
        "assigned_to": lead.assigned_to,
        "score": lead.score,
        "follow_up_date": lead.follow_up_date.isoformat() if lead.follow_up_date else None,
        "created_at": lead.created_at.isoformat(),
        "updated_at": lead.updated_at.isoformat(),
        # Hybrid scoring fields
        "deal_stage": getattr(lead, "deal_stage", "lead"),
        "estimated_monthly_revenue": float(getattr(lead, "estimated_monthly_revenue", 0) or 0),
        "estimated_trips": int(getattr(lead, "estimated_trips", 0) or 0),
        "confidence_score": int(getattr(lead, "confidence_score", 0) or 0),
        "probability_to_close": float(getattr(lead, "probability_to_close", 0) or 0),
        "expected_deal_value": float(getattr(lead, "expected_deal_value", 0) or 0),
    }
    return row


def _row_to_lead(row: dict[str, Any]) -> Lead:
    """Map Supabase row back to Lead model."""
    lead = Lead(
        id=row["id"],
        name=row["name"],
        company=row.get("company"),
        phone=row.get("phone"),
        email=row.get("email"),
        source=row.get("source", "manual"),
        status=row.get("status", "new"),
        priority=row.get("priority", "medium"),
        category=row.get("category", "other"),
        cargo_type=row.get("cargo_type"),
        route_from=row.get("route_from"),
        route_to=row.get("route_to"),
        fleet_size_needed=row.get("fleet_size_needed"),
        budget_monthly=row.get("budget_monthly"),
        notes=row.get("notes"),
        raw_data=row.get("raw_data") or {},
        crm_id=row.get("crm_id"),
        assigned_to=row.get("assigned_to"),
        score=row.get("score", 0),
        follow_up_date=datetime.fromisoformat(row["follow_up_date"]) if row.get("follow_up_date") else None,
        created_at=datetime.fromisoformat(row["created_at"]) if row.get("created_at") else datetime.utcnow(),
        updated_at=datetime.fromisoformat(row["updated_at"]) if row.get("updated_at") else datetime.utcnow(),
    )
    # Attach hybrid scoring extras as dynamic attributes
    lead.__dict__["deal_stage"] = row.get("deal_stage", "lead")
    lead.__dict__["estimated_monthly_revenue"] = row.get("estimated_monthly_revenue", 0)
    lead.__dict__["estimated_trips"] = row.get("estimated_trips", 0)
    lead.__dict__["confidence_score"] = row.get("confidence_score", 0)
    lead.__dict__["probability_to_close"] = row.get("probability_to_close", 0)
    lead.__dict__["expected_deal_value"] = row.get("expected_deal_value", 0)
    return lead


class SupabaseCRM(BaseCRM):
    """
    Supabase adapter implementing BaseCRM.
    Acts as the guaranteed persistence layer — saves every lead to Postgres.
    """

    def __init__(self, url: str, key: str) -> None:
        self.client: Client = create_client(url, key)
        self._log = logger.bind(crm="supabase")

    def _run(self, coro):
        """Run sync supabase calls in thread pool to not block event loop."""
        loop = asyncio.get_running_loop()
        return loop.run_in_executor(None, coro)

    # Extra fields added in the schema migration — excluded if columns don't exist yet
    _MIGRATION_FIELDS = frozenset({
        "deal_stage", "deal_stage_updated_at", "first_response_at",
        "response_time_minutes", "estimated_monthly_revenue", "estimated_trips",
        "confidence_score", "probability_to_close", "expected_deal_value",
    })

    async def create_lead(self, lead: "Lead | LeadCreate") -> str:
        """Insert lead into Supabase leads table. Accepts Lead or LeadCreate."""
        if isinstance(lead, LeadCreate):
            lead = Lead(**lead.model_dump())
        log = self._log.bind(lead_id=lead.id, lead_name=lead.name)

        # Dedup guard: leads.phone has a UNIQUE constraint (added 2026-08-09,
        # after finding 1,931 duplicate rows — serpapi_prospecting had been
        # re-inserting the same ~330 real phone numbers up to 15x each,
        # which also caused some contacts to receive duplicate outreach
        # messages). Check first so a re-scrape returns the existing lead's
        # id instead of hitting a constraint violation on every insert.
        if lead.phone:
            existing = await self.search_lead(phone=lead.phone)
            if existing:
                log.debug(
                    "create_lead: phone already exists, skipping duplicate insert",
                    existing_id=existing.id,
                )
                return existing.id

        row = _lead_to_row(lead)

        try:
            result = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: self.client.table("leads").insert(row).execute()
            )

            if result.data:
                log.info("Lead saved to Supabase", lead_id=lead.id)
                await self._log_event(lead.id, "created", {
                    "score": lead.score,
                    "priority": str(lead.priority),
                    "category": str(lead.category),
                    "source": str(lead.source),
                })
                return lead.id
            else:
                raise Exception(f"Supabase insert returned no data: {result}")

        except Exception as exc:
            err_str = str(exc).lower()
            # If failure is due to missing migration columns, retry without them
            if any(f in err_str for f in ("column", "schema", "does not exist", "no column")):
                log.warning("Migration columns missing — retrying with base fields only", error=str(exc))
                base_row = {k: v for k, v in row.items() if k not in self._MIGRATION_FIELDS}
                try:
                    result2 = await asyncio.get_running_loop().run_in_executor(
                        None,
                        lambda: self.client.table("leads").insert(base_row).execute()
                    )
                    if result2.data:
                        log.info("Lead saved to Supabase (base fields only)", lead_id=lead.id)
                        return lead.id
                except Exception as exc2:
                    log.error("Base-fields retry also failed", error=str(exc2))
                    raise exc2

            log.error("Failed to save lead to Supabase", error=str(exc))
            raise

    async def update_lead(self, crm_id: str, data: dict) -> bool:
        """Update lead fields by lead ID."""
        log = self._log.bind(lead_id=crm_id)

        try:
            # Always update the timestamp
            data["updated_at"] = datetime.utcnow().isoformat()

            result = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: self.client.table("leads").update(data).eq("id", crm_id).execute()
            )

            if result.data:
                log.info("Lead updated in Supabase", fields=list(data.keys()))
                await self._log_event(crm_id, "updated", {"fields": list(data.keys())})
                return True
            return False

        except Exception as exc:
            log.error("Failed to update lead in Supabase", error=str(exc))
            return False

    async def search_lead(
        self,
        email: Optional[str] = None,
        phone: Optional[str] = None,
    ) -> Optional[Lead]:
        """Find existing lead by phone or email (deduplication)."""
        try:
            if phone:
                result = await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: self.client.table("leads")
                        .select("*")
                        .eq("phone", phone)
                        .order("created_at", desc=True)
                        .limit(1)
                        .execute()
                )
                if result.data:
                    return _row_to_lead(result.data[0])

            if email:
                result = await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: self.client.table("leads")
                        .select("*")
                        .eq("email", email)
                        .order("created_at", desc=True)
                        .limit(1)
                        .execute()
                )
                if result.data:
                    return _row_to_lead(result.data[0])

        except Exception as exc:
            self._log.warning("Supabase search_lead failed", error=str(exc))

        return None

    async def create_task(self, crm_id: str, task: dict) -> str:
        """Log a follow-up task as a lead event."""
        try:
            await self._log_event(crm_id, "task_created", task)
            return f"task-{crm_id[:8]}"
        except Exception as exc:
            self._log.warning("Supabase create_task failed", error=str(exc))
            return ""

    async def get_pipeline_stats(self) -> dict:
        """Fetch aggregated pipeline stats from Supabase."""
        try:
            # Total leads
            total_result = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: self.client.table("leads").select("id", count="exact").execute()
            )
            total = total_result.count or 0

            # By status
            status_result = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: self.client.table("leads").select("status").execute()
            )
            status_counts: dict[str, int] = {}
            for row in (status_result.data or []):
                s = row.get("status", "unknown")
                # Normalize "LeadStatus.NEW" → "new"
                if "." in s:
                    s = s.split(".")[-1].lower()
                status_counts[s] = status_counts.get(s, 0) + 1

            # By priority
            priority_result = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: self.client.table("leads").select("priority").execute()
            )
            priority_counts: dict[str, int] = {}
            for row in (priority_result.data or []):
                p = row.get("priority", "unknown")
                # Normalize "LeadPriority.HIGH" → "high"
                if "." in p:
                    p = p.split(".")[-1].lower()
                priority_counts[p] = priority_counts.get(p, 0) + 1

            # Avg score
            score_result = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: self.client.table("leads").select("score").execute()
            )
            scores = [r["score"] for r in (score_result.data or []) if r.get("score") is not None]
            avg_score = round(sum(scores) / len(scores), 1) if scores else 0

            # Today's leads
            today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
            today_result = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: self.client.table("leads").select("id", count="exact").gte("created_at", today).execute()
            )
            today_count = today_result.count or 0

            return {
                "crm": "supabase",
                "total_leads": total,
                "today_leads": today_count,
                "by_status": status_counts,
                "by_priority": priority_counts,
                "avg_score": avg_score,
            }

        except Exception as exc:
            self._log.error("Supabase get_pipeline_stats failed", error=str(exc))
            return {"crm": "supabase", "error": str(exc), "total_leads": 0}

    async def update_deal_stage(
        self,
        lead_id: str,
        new_stage: str,
        changed_by: str = "system",
        notes: str = "",
    ) -> bool:
        """
        Move a lead to a new deal pipeline stage.
        Updates leads.deal_stage and inserts a row into deal_stage_history.
        """
        try:
            now = datetime.utcnow().isoformat()

            # Get current stage for history
            cur = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: self.client.table("leads")
                    .select("deal_stage")
                    .eq("id", lead_id)
                    .single()
                    .execute()
            )
            from_stage = cur.data.get("deal_stage", "lead") if cur.data else "lead"

            # deal_stage is the source of truth for pipeline position (it has
            # a full audit trail via deal_stage_history; status never did).
            # status is kept as a coarser mirror so old code/dashboards that
            # still read it stay consistent — this is the one place both are
            # written together (92 of 329 leads had disagreed before this).
            status_for_stage = {
                "NEW_LEAD": "new",
                "WON": "won",
                "LOST": "lost",
            }.get(new_stage, "contacted")

            # Update leads table
            await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: self.client.table("leads").update({
                    "deal_stage": new_stage,
                    "status": status_for_stage,
                    "deal_stage_updated_at": now,
                    "updated_at": now,
                }).eq("id", lead_id).execute()
            )

            # Insert history row
            await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: self.client.table("deal_stage_history").insert({
                    "lead_id": lead_id,
                    "from_stage": from_stage,
                    "to_stage": new_stage,
                    "changed_by": changed_by,
                    "notes": notes,
                    "changed_at": now,
                }).execute()
            )

            # lead_events previously only ever captured 'created'/'task_created'
            # (2,260 of 2,268 rows were 'created') — stage transitions were
            # invisible to the audit-log/timeline view even though
            # deal_stage_history recorded them separately.
            await self._log_event(lead_id, "stage_changed", {
                "from_stage": from_stage,
                "to_stage": new_stage,
                "changed_by": changed_by,
            })

            self._log.info("Deal stage updated", lead_id=lead_id, from_stage=from_stage, to_stage=new_stage)
            return True

        except Exception as exc:
            self._log.error("update_deal_stage failed", lead_id=lead_id, error=str(exc))
            return False

    async def log_notification(
        self,
        lead_id: str,
        channel: str,
        recipient: str,
        status: str = "sent",
        payload: dict | None = None,
    ) -> None:
        """Record a notification in the notifications_log table."""
        try:
            await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: self.client.table("notifications_log").insert({
                    "lead_id": lead_id,
                    "channel": channel,
                    "recipient": recipient,
                    "status": status,
                    "payload": payload or {},
                }).execute()
            )
        except Exception as exc:
            self._log.warning("Failed to log notification", error=str(exc))

    async def log_conversation(self, lead_id: str, message: str, role: str) -> None:
        """Insert a message into the conversations table."""
        try:
            await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: self.client.table("conversations").insert({
                    "lead_id": lead_id,
                    "message": message,
                    "role": role,
                }).execute()
            )
        except Exception as exc:
            self._log.warning("conversations.insert failed", error=str(exc))

    async def log_activity(self, lead_id: str, activity_type: str, status: str = "done", notes: str = "") -> None:
        """Insert a row into activities (call, follow_up, quote, message, meeting)."""
        try:
            await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: self.client.table("activities").insert({
                    "lead_id": lead_id,
                    "type": activity_type,
                    "status": status,
                    "notes": notes,
                }).execute()
            )
        except Exception as exc:
            self._log.warning("activities.insert failed", error=str(exc))

    async def upsert_deal(self, lead_id: str, value: float, stage: str, probability: float) -> None:
        """Create or update a deal row for a lead."""
        try:
            now = datetime.utcnow().isoformat()
            existing = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: self.client.table("deals").select("id").eq("lead_id", lead_id).limit(1).execute()
            )
            if existing.data:
                await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: self.client.table("deals").update({
                        "value": value, "stage": stage,
                        "probability": probability, "updated_at": now,
                    }).eq("lead_id", lead_id).execute()
                )
            else:
                await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: self.client.table("deals").insert({
                        "lead_id": lead_id, "value": value,
                        "stage": stage, "probability": probability,
                    }).execute()
                )
        except Exception as exc:
            self._log.warning("deals.upsert failed", error=str(exc))

    async def add_outbound_lead(self, data: dict) -> str:
        """Insert a prospect into outbound_leads table."""
        try:
            row = {
                "company_name": data.get("company_name", ""),
                "industry": data.get("industry"),
                "contact": data.get("contact"),
                "phone": data.get("phone"),
                "email": data.get("email"),
                "city": data.get("city"),
                "score": data.get("score", 0),
                "source": data.get("source", "prospecting"),
                "outreach_message": data.get("outreach_message"),
                "raw_data": data.get("raw_data", {}),
            }
            result = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: self.client.table("outbound_leads").insert(row).execute()
            )
            if result.data:
                return result.data[0]["id"]
            return ""
        except Exception as exc:
            self._log.warning("outbound_leads.insert failed", error=str(exc))
            return ""

    async def _log_event(self, lead_id: str, event_type: str, data: dict) -> None:
        """Insert a row into lead_events for audit trail."""
        try:
            await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: self.client.table("lead_events").insert({
                    "lead_id": lead_id,
                    "event_type": event_type,
                    "data": data,
                }).execute()
            )
        except Exception as exc:
            self._log.warning("Failed to log lead event", event_type=event_type, error=str(exc))
