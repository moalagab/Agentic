"""
Airtable CRM integration for Smartfield Lead Generation System.
تكامل Airtable لنظام إدارة العملاء المحتملين

Uses the Airtable REST API v0 for creating and managing lead records.
"""

from __future__ import annotations

import asyncio
import urllib.parse
from datetime import datetime, timedelta
from typing import Any, Optional

import httpx
import structlog

from crm.base import BaseCRM
from models.lead import Lead, LeadCategory, LeadPriority, LeadSource, LeadStatus

logger = structlog.get_logger(__name__)

AIRTABLE_BASE_URL = "https://api.airtable.com/v0"
TASKS_TABLE_NAME = "Tasks"


def _lead_to_airtable_fields(lead: Lead) -> dict[str, Any]:
    """Map Lead model fields to Airtable column names."""
    fields: dict[str, Any] = {
        "Name": lead.name,
        "Status": lead.status.title() if lead.status else "New",
        "Source": lead.source.title() if lead.source else "Manual",
        "Priority": lead.priority.title() if lead.priority else "Medium",
        "Score": lead.score or 0,
        "Created At": lead.created_at.isoformat(),
    }

    if lead.company:
        fields["Company"] = lead.company
    if lead.phone:
        fields["Phone"] = lead.phone
    if lead.email:
        fields["Email"] = lead.email
    if lead.category:
        fields["Category"] = lead.category.replace("_", " ").title()
    if lead.cargo_type:
        fields["Cargo Type"] = lead.cargo_type
    if lead.route_from:
        fields["Route From"] = lead.route_from
    if lead.route_to:
        fields["Route To"] = lead.route_to
    if lead.fleet_size_needed:
        fields["Fleet Size Needed"] = lead.fleet_size_needed
    if lead.budget_monthly:
        fields["Budget Monthly SAR"] = lead.budget_monthly
    if lead.notes:
        fields["Notes"] = lead.notes
    if lead.assigned_to:
        fields["Assigned To"] = lead.assigned_to

    return fields


def _airtable_record_to_lead(record: dict[str, Any]) -> Lead:
    """Convert an Airtable record to a Lead model."""
    fields = record.get("fields", {})
    record_id = record.get("id", "")

    name = fields.get("Name", "Unknown")
    company = fields.get("Company")
    phone = fields.get("Phone")
    email = fields.get("Email")

    try:
        status = LeadStatus(fields.get("Status", "new").lower())
    except ValueError:
        status = LeadStatus.NEW

    try:
        priority = LeadPriority(fields.get("Priority", "medium").lower())
    except ValueError:
        priority = LeadPriority.MEDIUM

    try:
        category_raw = fields.get("Category", "other").lower().replace(" ", "_")
        category = LeadCategory(category_raw)
    except ValueError:
        category = LeadCategory.OTHER

    try:
        source = LeadSource(fields.get("Source", "manual").lower().replace(" ", "_"))
    except ValueError:
        source = LeadSource.MANUAL

    try:
        score = int(fields.get("Score", 0))
    except (ValueError, TypeError):
        score = 0

    fleet = fields.get("Fleet Size Needed")
    budget = fields.get("Budget Monthly SAR")

    return Lead(
        name=name,
        company=company,
        phone=phone,
        email=email,
        source=source,
        status=status,
        priority=priority,
        category=category,
        score=score,
        cargo_type=fields.get("Cargo Type"),
        route_from=fields.get("Route From"),
        route_to=fields.get("Route To"),
        fleet_size_needed=int(fleet) if fleet else None,
        budget_monthly=float(budget) if budget else None,
        notes=fields.get("Notes"),
        assigned_to=fields.get("Assigned To"),
        crm_id=record_id,
    )


class AirtableCRM(BaseCRM):
    """
    Airtable CRM adapter using async httpx.
    Stores leads as records in an Airtable base/table.
    """

    def __init__(
        self,
        api_key: str,
        base_id: str,
        table_name: str = "Leads",
    ) -> None:
        self.api_key = api_key
        self.base_id = base_id
        self.table_name = table_name
        self._log = logger.bind(crm="Airtable", base_id=base_id, table=table_name)
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def _base_url(self, table: str | None = None) -> str:
        t = urllib.parse.quote(table or self.table_name)
        return f"{AIRTABLE_BASE_URL}/{self.base_id}/{t}"

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers=self._headers,
            timeout=30.0,
        )

    async def create_lead(self, lead: Lead) -> str:
        """Create a new record in the Airtable Leads table. Returns the record ID."""
        fields = _lead_to_airtable_fields(lead)
        payload = {"fields": fields}

        async with self._client() as client:
            resp = await client.post(self._base_url(), json=payload)

        if resp.status_code in (200, 201):
            record_id = resp.json()["id"]
            self._log.info("Airtable record created", record_id=record_id)
            return record_id

        self._log.error(
            "Airtable create_lead failed",
            status=resp.status_code,
            body=resp.text[:500],
        )
        resp.raise_for_status()
        return ""

    async def update_lead(self, crm_id: str, data: dict) -> bool:
        """PATCH an Airtable record with updated fields."""
        # Map our internal field names to Airtable column names
        field_map = {
            "status": "Status",
            "priority": "Priority",
            "score": "Score",
            "notes": "Notes",
            "category": "Category",
            "assigned_to": "Assigned To",
        }

        fields: dict[str, Any] = {}
        for key, value in data.items():
            at_key = field_map.get(key, key)
            if key == "status" and value:
                fields[at_key] = str(value).title()
            elif key == "priority" and value:
                fields[at_key] = str(value).title()
            elif key == "category" and value:
                fields[at_key] = str(value).replace("_", " ").title()
            else:
                fields[at_key] = value

        if not fields:
            return True

        async with self._client() as client:
            resp = await client.patch(
                f"{self._base_url()}/{crm_id}",
                json={"fields": fields},
            )

        if resp.status_code in (200, 201):
            self._log.info("Airtable record updated", record_id=crm_id, fields=list(fields.keys()))
            return True

        self._log.error(
            "Airtable update_lead failed",
            record_id=crm_id,
            status=resp.status_code,
            body=resp.text[:500],
        )
        return False

    async def search_lead(
        self,
        email: Optional[str] = None,
        phone: Optional[str] = None,
    ) -> Optional[Lead]:
        """
        Search Airtable for an existing lead using filterByFormula.
        Checks email first, then phone.
        """
        if not email and not phone:
            return None

        # Build formula: match by email OR phone
        conditions = []
        if email:
            safe_email = email.replace("'", "\\'")
            conditions.append(f"LOWER({{Email}}) = LOWER('{safe_email}')")
        if phone:
            # Try both with and without spaces/dashes
            safe_phone = phone.replace("'", "\\'")
            conditions.append(f"SUBSTITUTE(SUBSTITUTE({{Phone}}, ' ', ''), '-', '') = '{safe_phone}'")

        formula = f"OR({', '.join(conditions)})" if len(conditions) > 1 else conditions[0]

        params = {
            "filterByFormula": formula,
            "maxRecords": 1,
        }

        async with self._client() as client:
            resp = await client.get(self._base_url(), params=params)

        if resp.status_code != 200:
            self._log.warning(
                "Airtable search failed",
                status=resp.status_code,
                body=resp.text[:300],
            )
            return None

        records = resp.json().get("records", [])
        if not records:
            return None

        return _airtable_record_to_lead(records[0])

    async def create_task(self, crm_id: str, task: dict) -> str:
        """
        Create a follow-up task record in a separate 'Tasks' Airtable table.
        The task is linked to the lead via the Lead Record ID field.
        """
        due_hours = task.get("due_hours", 24)
        due_date = (datetime.utcnow() + timedelta(hours=due_hours)).isoformat()

        fields: dict[str, Any] = {
            "Title": task.get("title", "Follow Up"),
            "Type": task.get("type", "call").title(),
            "Notes": task.get("notes", ""),
            "Priority": str(task.get("priority", "medium")).title(),
            "Due Date": due_date,
            "Status": "Not Started",
            "Lead Record ID": crm_id,
        }

        if assigned_to := task.get("assigned_to"):
            fields["Assigned To"] = assigned_to

        async with self._client() as client:
            resp = await client.post(
                self._base_url(TASKS_TABLE_NAME),
                json={"fields": fields},
            )

        if resp.status_code in (200, 201):
            task_id = resp.json()["id"]
            self._log.info("Airtable task created", task_id=task_id, lead_crm_id=crm_id)
            return task_id

        self._log.error(
            "Airtable create_task failed",
            status=resp.status_code,
            body=resp.text[:500],
        )
        # Don't raise — tasks are non-critical
        return ""

    async def get_pipeline_stats(self) -> dict:
        """
        Aggregate pipeline statistics from Airtable lead records.
        Fetches up to 100 records and computes distribution data.
        """
        stats: dict[str, Any] = {
            "crm": "airtable",
            "total_leads": 0,
            "by_status": {},
            "by_priority": {},
            "by_category": {},
            "average_score": 0,
        }

        params = {
            "fields[]": ["Status", "Priority", "Score", "Category"],
            "maxRecords": 100,
        }

        async with self._client() as client:
            resp = await client.get(self._base_url(), params=params)

        if resp.status_code != 200:
            self._log.warning("Airtable get_pipeline_stats failed", status=resp.status_code)
            return stats

        records = resp.json().get("records", [])
        stats["total_leads"] = len(records)

        scores = []
        for record in records:
            fields = record.get("fields", {})

            status = fields.get("Status", "Unknown")
            stats["by_status"][status] = stats["by_status"].get(status, 0) + 1

            priority = fields.get("Priority", "Unknown")
            stats["by_priority"][priority] = stats["by_priority"].get(priority, 0) + 1

            category = fields.get("Category", "Unknown")
            stats["by_category"][category] = stats["by_category"].get(category, 0) + 1

            try:
                score = float(fields.get("Score", 0) or 0)
                if score:
                    scores.append(score)
            except (ValueError, TypeError):
                pass

        if scores:
            stats["average_score"] = round(sum(scores) / len(scores), 1)

        return stats
