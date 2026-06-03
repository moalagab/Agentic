"""
LinkedIn Lead Gen Forms channel handler for Smartfield.
معالج قناة نماذج LinkedIn للعملاء المحتملين

Parses LinkedIn Lead Gen Form webhook payloads and maps
them to structured LeadCreate objects.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Optional

import structlog

from models.lead import LeadCreate, LeadSource

logger = structlog.get_logger(__name__)

# LinkedIn Lead Gen Form field name mappings
LINKEDIN_FIELD_MAP = {
    # Standard LinkedIn fields → our field names
    "firstName": "first_name",
    "lastName": "last_name",
    "emailAddress": "email",
    "phoneNumber": "phone",
    "company": "company",
    "title": "job_title",
    "linkedInUrl": "linkedin_url",
    # Custom form fields (adjust to match your LinkedIn form's custom fields)
    "cargo_type": "cargo_type",
    "نوع_البضاعة": "cargo_type",
    "fleet_size": "fleet_size_needed",
    "عدد_الشاحنات": "fleet_size_needed",
    "monthly_budget": "budget_monthly",
    "الميزانية_الشهرية": "budget_monthly",
    "route_from": "route_from",
    "route_to": "route_to",
    "من": "route_from",
    "إلى": "route_to",
}


def _map_linkedin_fields(form_data: list[dict[str, Any]]) -> dict[str, Any]:
    """Map LinkedIn form field responses to our internal field names."""
    result: dict[str, Any] = {}

    for field in form_data:
        name = field.get("name", "")
        value = field.get("value", "")

        if not name or not value:
            continue

        mapped_name = LINKEDIN_FIELD_MAP.get(name, name.lower())
        result[mapped_name] = value

    return result


def _verify_linkedin_signature(
    body: bytes,
    signature: str,
    client_secret: str,
) -> bool:
    """
    Verify LinkedIn webhook signature using HMAC-SHA256.
    يتحقق من توقيع webhook LinkedIn.
    """
    if not signature or not client_secret:
        return False

    expected = hmac.new(
        client_secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(f"sha256={expected}", signature)


class LinkedInChannelHandler:
    """
    Parses LinkedIn Lead Gen Forms webhook payloads.
    Handles both the newer real-time delivery format and the legacy format.
    """

    def __init__(self, client_id: str = "", client_secret: str = "") -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self._log = logger.bind(channel="LinkedIn")

    def parse_webhook(self, payload: dict[str, Any]) -> Optional[LeadCreate]:
        """
        Parse a LinkedIn Lead Gen Forms webhook payload into a LeadCreate.
        يحلل حمولة webhook نماذج LinkedIn ويحولها إلى LeadCreate.

        LinkedIn sends lead data in this structure:
        {
          "leadGenFormResponse": {
            "owner": "...",
            "leadGenForm": "...",
            "firstName": "...",
            "lastName": "...",
            "emailAddress": "...",
            ...
            "customQuestions": [...],
            "formFieldResponses": [...]
          }
        }

        Returns None if payload cannot be parsed.
        """
        try:
            # Handle array payload (LinkedIn sends array of events)
            if isinstance(payload, list):
                if not payload:
                    return None
                payload = payload[0]

            # Extract lead form response
            lead_form_response = (
                payload.get("leadGenFormResponse")
                or payload.get("lead_gen_form_response")
                or payload
            )

            # Top-level standard fields
            first_name = lead_form_response.get("firstName", "")
            last_name = lead_form_response.get("lastName", "")
            email = lead_form_response.get("emailAddress", "")
            phone = lead_form_response.get("phoneNumber", "")
            company = lead_form_response.get("companyName", "") or lead_form_response.get("company", "")
            job_title = lead_form_response.get("title", "") or lead_form_response.get("jobTitle", "")

            # Parse custom form field responses
            form_responses = (
                lead_form_response.get("formFieldResponses", [])
                or lead_form_response.get("customQuestions", [])
            )
            custom_fields = _map_linkedin_fields(form_responses)

            # Merge custom fields
            if not email and "email" in custom_fields:
                email = custom_fields["email"]
            if not phone and "phone" in custom_fields:
                phone = custom_fields["phone"]
            if not company and "company" in custom_fields:
                company = custom_fields["company"]

            name = f"{first_name} {last_name}".strip() or custom_fields.get("name", "LinkedIn Lead")

            # Ensure we have at minimum phone or email
            if not email and not phone:
                self._log.warning("LinkedIn lead missing contact info", name=name)
                if not name or name == "LinkedIn Lead":
                    return None

            # Parse budget
            budget = None
            budget_raw = custom_fields.get("budget_monthly")
            if budget_raw:
                try:
                    budget = float(str(budget_raw).replace(",", "").replace("ريال", "").strip())
                except ValueError:
                    pass

            # Parse fleet size
            fleet = None
            fleet_raw = custom_fields.get("fleet_size_needed")
            if fleet_raw:
                try:
                    fleet = int(str(fleet_raw).strip())
                except ValueError:
                    pass

            notes_parts = []
            if job_title:
                notes_parts.append(f"المسمى الوظيفي: {job_title}")
            if linkedin_url := lead_form_response.get("linkedInUrl", ""):
                notes_parts.append(f"LinkedIn: {linkedin_url}")

            self._log.info(
                "LinkedIn lead parsed",
                name=name,
                company=company,
                has_email=bool(email),
                has_phone=bool(phone),
            )

            return LeadCreate(
                name=name,
                company=company or None,
                phone=phone or None,
                email=email or None,
                source=LeadSource.LINKEDIN,
                cargo_type=custom_fields.get("cargo_type"),
                route_from=custom_fields.get("route_from"),
                route_to=custom_fields.get("route_to"),
                fleet_size_needed=fleet,
                budget_monthly=budget,
                notes="\n".join(notes_parts) if notes_parts else None,
                raw_data={"linkedin_raw": lead_form_response},
            )

        except Exception as exc:
            self._log.error("Failed to parse LinkedIn webhook", error=str(exc))
            return None

    def verify_signature(self, body: bytes, signature: str) -> bool:
        """Verify LinkedIn webhook signature."""
        if not self.client_secret:
            self._log.warning("LinkedIn client secret not configured; skipping verification")
            return True  # Allow through if not configured

        result = _verify_linkedin_signature(body, signature, self.client_secret)
        if not result:
            self._log.warning("LinkedIn signature verification failed")
        return result
