"""
HubSpot CRM integration for Smartfield Lead Generation System.
تكامل نظام HubSpot لإدارة العملاء المحتملين

Uses HubSpot Contacts API v3 for CRUD operations.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Optional

import httpx
import structlog

from crm.base import BaseCRM
from models.lead import Lead, LeadCategory, LeadPriority, LeadSource, LeadStatus

logger = structlog.get_logger(__name__)

HUBSPOT_BASE_URL = "https://api.hubapi.com"
CONTACTS_ENDPOINT = "/crm/v3/objects/contacts"
TASKS_ENDPOINT = "/crm/v3/objects/tasks"
DEALS_ENDPOINT = "/crm/v3/objects/deals"


def _split_name(name: str) -> tuple[str, str]:
    """Split a full name into first/last."""
    parts = name.strip().split(None, 1)
    return parts[0], parts[1] if len(parts) > 1 else ""


def _lead_to_hubspot_properties(lead: Lead) -> dict[str, Any]:
    """Map Lead model fields to HubSpot contact property names."""
    first, last = _split_name(lead.name)
    props: dict[str, Any] = {
        "firstname": first,
        "lastname": last,
        "company": lead.company or "",
        "phone": lead.phone or "",
        "email": lead.email or "",
        "hs_lead_status": _map_status(lead.status),
        "lead_source_category": lead.source,
        "lifecyclestage": "lead",
    }

    # Custom properties (these must be created in HubSpot portal first)
    if lead.priority:
        props["smartfield_priority"] = lead.priority
    if lead.category:
        props["smartfield_category"] = lead.category
    if lead.score is not None:
        props["smartfield_score"] = str(lead.score)
    if lead.cargo_type:
        props["smartfield_cargo_type"] = lead.cargo_type
    if lead.fleet_size_needed:
        props["smartfield_fleet_size"] = str(lead.fleet_size_needed)
    if lead.budget_monthly:
        props["smartfield_budget_monthly"] = str(lead.budget_monthly)
    if lead.route_from:
        props["smartfield_route_from"] = lead.route_from
    if lead.route_to:
        props["smartfield_route_to"] = lead.route_to
    if lead.notes:
        props["hs_note_body"] = lead.notes

    return props


def _map_status(status: str) -> str:
    """Map Smartfield status to HubSpot lead status values."""
    mapping = {
        "new": "NEW",
        "contacted": "OPEN",
        "qualified": "IN_PROGRESS",
        "unqualified": "UNQUALIFIED",
        "converted": "CONNECTED",
        "lost": "BAD_TIMING",
    }
    return mapping.get(str(status).lower(), "NEW")


def _hubspot_contact_to_lead(contact: dict[str, Any]) -> Lead:
    """Convert a HubSpot contact API response to a Lead model."""
    props = contact.get("properties", {})
    first = props.get("firstname", "")
    last = props.get("lastname", "")
    name = f"{first} {last}".strip() or "Unknown"

    try:
        score = int(props.get("smartfield_score", 0) or 0)
    except (ValueError, TypeError):
        score = 0

    try:
        fleet_size = int(props.get("smartfield_fleet_size", 0) or 0) or None
    except (ValueError, TypeError):
        fleet_size = None

    try:
        budget = float(props.get("smartfield_budget_monthly", 0) or 0) or None
    except (ValueError, TypeError):
        budget = None

    try:
        category = LeadCategory(props.get("smartfield_category", "other").lower())
    except ValueError:
        category = LeadCategory.OTHER

    try:
        priority = LeadPriority(props.get("smartfield_priority", "medium").lower())
    except ValueError:
        priority = LeadPriority.MEDIUM

    return Lead(
        name=name,
        company=props.get("company"),
        phone=props.get("phone"),
        email=props.get("email"),
        source=LeadSource.MANUAL,
        status=LeadStatus.NEW,
        priority=priority,
        category=category,
        score=score,
        fleet_size_needed=fleet_size,
        budget_monthly=budget,
        cargo_type=props.get("smartfield_cargo_type"),
        route_from=props.get("smartfield_route_from"),
        route_to=props.get("smartfield_route_to"),
        notes=props.get("hs_note_body"),
        crm_id=contact.get("id"),
    )


class HubSpotCRM(BaseCRM):
    """
    HubSpot CRM adapter using async httpx client.
    Implements full CRUD for leads (HubSpot contacts) and tasks (engagements).
    """

    def __init__(self, api_key: str, portal_id: str = "") -> None:
        self.api_key = api_key
        self.portal_id = portal_id
        self._log = logger.bind(crm="HubSpot")
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=HUBSPOT_BASE_URL,
            headers=self._headers,
            timeout=30.0,
        )

    async def create_lead(self, lead: Lead) -> str:
        """
        Create a HubSpot contact from a Lead.
        Returns the HubSpot contact ID.
        Handles 409 Conflict (duplicate) and 400 (missing custom properties) gracefully.
        """
        properties = _lead_to_hubspot_properties(lead)

        async with self._client() as client:
            resp = await client.post(
                CONTACTS_ENDPOINT,
                json={"properties": properties},
            )

            # Retry with only guaranteed standard HubSpot properties
            if resp.status_code == 400 and "PROPERTY_DOESNT_EXIST" in resp.text:
                self._log.warning(
                    "HubSpot custom properties missing — retrying with standard fields only",
                    body=resp.text[:200],
                )
                standard_props = {k: v for k, v in properties.items()
                                  if k in ("firstname", "lastname", "company",
                                           "phone", "email", "lifecyclestage",
                                           "hs_lead_status")}
                resp = await client.post(
                    CONTACTS_ENDPOINT,
                    json={"properties": standard_props},
                )

            if resp.status_code == 409:
                # Duplicate: extract existing ID from error
                self._log.info("Duplicate contact in HubSpot, fetching existing ID")
                vid = resp.json().get("identityProfile", {}).get("vid") or resp.json().get("error", "")
                if vid:
                    return str(vid)
                if lead.email:
                    existing = await self.search_lead(email=lead.email)
                    if existing and existing.crm_id:
                        return existing.crm_id
                raise ValueError(f"HubSpot 409 conflict but could not resolve existing ID: {resp.text}")

            if resp.status_code not in (200, 201):
                self._log.error(
                    "HubSpot create_lead failed",
                    status=resp.status_code,
                    body=resp.text[:500],
                )
                resp.raise_for_status()

            contact_id = resp.json()["id"]
            self._log.info("HubSpot contact created", contact_id=contact_id)
            return contact_id

    async def update_lead(self, crm_id: str, data: dict) -> bool:
        """PATCH a HubSpot contact with updated fields."""
        # Map our field names to HubSpot property names
        field_map = {
            "status": "hs_lead_status",
            "priority": "smartfield_priority",
            "score": "smartfield_score",
            "notes": "hs_note_body",
            "category": "smartfield_category",
        }

        properties = {}
        for key, value in data.items():
            hubspot_key = field_map.get(key, key)
            if key == "status":
                properties[hubspot_key] = _map_status(str(value))
            else:
                properties[hubspot_key] = str(value) if value is not None else ""

        async with self._client() as client:
            resp = await client.patch(
                f"{CONTACTS_ENDPOINT}/{crm_id}",
                json={"properties": properties},
            )

        if resp.status_code in (200, 204):
            self._log.info("HubSpot contact updated", crm_id=crm_id, fields=list(properties.keys()))
            return True

        self._log.error(
            "HubSpot update_lead failed",
            crm_id=crm_id,
            status=resp.status_code,
            body=resp.text[:500],
        )
        return False

    async def search_lead(
        self,
        email: Optional[str] = None,
        phone: Optional[str] = None,
    ) -> Optional[Lead]:
        """Search HubSpot contacts by email or phone using the v3 Search API."""
        filters = []
        if email:
            filters.append({
                "propertyName": "email",
                "operator": "EQ",
                "value": email.lower(),
            })
        if phone:
            filters.append({
                "propertyName": "phone",
                "operator": "EQ",
                "value": phone,
            })

        if not filters:
            return None

        properties = [
            "firstname", "lastname", "company", "phone", "email",
            "hs_lead_status", "smartfield_priority", "smartfield_category",
            "smartfield_score", "smartfield_cargo_type", "smartfield_fleet_size",
            "smartfield_budget_monthly", "smartfield_route_from", "smartfield_route_to",
            "hs_note_body",
        ]

        search_payload = {
            "filterGroups": [{"filters": filters[:1]}],  # HubSpot uses OR between groups
            "properties": properties,
            "limit": 1,
        }

        async with self._client() as client:
            resp = await client.post(
                f"{CONTACTS_ENDPOINT}/search",
                json=search_payload,
            )

        if resp.status_code != 200:
            self._log.warning(
                "HubSpot search failed",
                status=resp.status_code,
                body=resp.text[:300],
            )
            return None

        results = resp.json().get("results", [])
        if not results:
            return None

        return _hubspot_contact_to_lead(results[0])

    async def create_task(self, crm_id: str, task: dict) -> str:
        """Create a HubSpot task (engagement) and associate it with a contact."""
        due_hours = task.get("due_hours", 24)
        due_date = datetime.utcnow() + timedelta(hours=due_hours)
        due_ms = int(due_date.timestamp() * 1000)

        task_type_map = {
            "call": "CALL",
            "email": "EMAIL",
            "meeting": "MEETING",
            "whatsapp": "CALL",
            "proposal": "EMAIL",
        }
        hs_task_type = task_type_map.get(task.get("type", "call"), "CALL")

        priority_map = {"high": "HIGH", "medium": "MEDIUM", "low": "LOW"}
        hs_priority = priority_map.get(str(task.get("priority", "medium")).lower(), "MEDIUM")

        properties = {
            "hs_task_subject": task.get("title", "متابعة عميل محتمل"),
            "hs_task_body": task.get("notes", ""),
            "hs_task_type": hs_task_type,
            "hs_task_priority": hs_priority,
            "hs_timestamp": str(due_ms),
            "hs_task_status": "NOT_STARTED",
        }

        payload = {
            "properties": properties,
            "associations": [
                {
                    "to": {"id": crm_id},
                    "types": [
                        {
                            "associationCategory": "HUBSPOT_DEFINED",
                            "associationTypeId": 204,  # task-to-contact
                        }
                    ],
                }
            ],
        }

        async with self._client() as client:
            resp = await client.post(TASKS_ENDPOINT, json=payload)

        if resp.status_code in (200, 201):
            task_id = resp.json()["id"]
            self._log.info("HubSpot task created", task_id=task_id, contact_id=crm_id)
            return task_id

        self._log.error(
            "HubSpot create_task failed",
            status=resp.status_code,
            body=resp.text[:500],
        )
        resp.raise_for_status()
        return ""

    async def get_pipeline_stats(self) -> dict:
        """
        Get aggregated pipeline statistics by querying HubSpot contacts.
        Returns counts, averages, and distribution data.
        """
        stats: dict[str, Any] = {
            "crm": "hubspot",
            "total_leads": 0,
            "by_status": {},
            "by_priority": {},
            "average_score": 0,
        }

        search_payload = {
            "filterGroups": [],
            "properties": ["hs_lead_status", "smartfield_priority", "smartfield_score"],
            "limit": 100,
        }

        async with self._client() as client:
            resp = await client.post(f"{CONTACTS_ENDPOINT}/search", json=search_payload)

        if resp.status_code != 200:
            self._log.warning("HubSpot get_pipeline_stats failed", status=resp.status_code)
            return stats

        data = resp.json()
        results = data.get("results", [])
        stats["total_leads"] = data.get("total", len(results))

        scores = []
        for contact in results:
            props = contact.get("properties", {})

            status = props.get("hs_lead_status", "UNKNOWN")
            stats["by_status"][status] = stats["by_status"].get(status, 0) + 1

            priority = props.get("smartfield_priority", "unknown")
            stats["by_priority"][priority] = stats["by_priority"].get(priority, 0) + 1

            try:
                score = float(props.get("smartfield_score", 0) or 0)
                if score:
                    scores.append(score)
            except (ValueError, TypeError):
                pass

        if scores:
            stats["average_score"] = round(sum(scores) / len(scores), 1)

        return stats
