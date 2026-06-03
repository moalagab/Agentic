"""
Website contact form channel handler for Smartfield.
معالج قناة نموذج التواصل في الموقع الإلكتروني

Parses both JSON and form-encoded contact form submissions
and normalizes them into LeadCreate objects.
"""

from __future__ import annotations

from typing import Any, Optional

import structlog

from models.lead import LeadCreate, LeadSource

logger = structlog.get_logger(__name__)

# Common website form field name variations
FIELD_ALIASES: dict[str, list[str]] = {
    "name": [
        "name", "full_name", "fullname", "contact_name", "your_name",
        "الاسم", "الاسم_الكامل", "اسم",
        "first_name",  # will be combined with last_name
    ],
    "company": [
        "company", "company_name", "organization", "org", "business",
        "الشركة", "اسم_الشركة",
    ],
    "phone": [
        "phone", "phone_number", "mobile", "cell", "contact_phone", "tel",
        "رقم_الهاتف", "رقم_الجوال", "جوال",
    ],
    "email": [
        "email", "email_address", "contact_email", "your_email",
        "البريد_الإلكتروني", "ايميل", "إيميل",
    ],
    "cargo_type": [
        "cargo_type", "cargo", "goods", "product_type", "shipment_type",
        "نوع_البضاعة", "نوع_المنتج",
    ],
    "route_from": [
        "route_from", "from", "from_city", "origin", "pickup",
        "من", "المدينة_من",
    ],
    "route_to": [
        "route_to", "to", "to_city", "destination", "delivery",
        "إلى", "المدينة_إلى",
    ],
    "fleet_size_needed": [
        "fleet_size", "fleet_size_needed", "trucks", "truck_count", "num_trucks",
        "عدد_الشاحنات",
    ],
    "budget_monthly": [
        "budget", "budget_monthly", "monthly_budget", "price_range",
        "الميزانية", "الميزانية_الشهرية",
    ],
    "message": [
        "message", "msg", "notes", "comment", "details", "inquiry",
        "رسالة", "ملاحظات", "تفاصيل",
    ],
}


def _normalize_key(key: str) -> str:
    """Normalize a form field key to lowercase with underscores."""
    return key.strip().lower().replace(" ", "_").replace("-", "_")


def _extract_field(data: dict[str, Any], field: str) -> Any:
    """
    Extract a field from form data, trying all known aliases.
    يستخرج حقلاً من بيانات النموذج بتجربة جميع الأسماء المعروفة.
    """
    aliases = FIELD_ALIASES.get(field, [field])
    for alias in aliases:
        normalized = _normalize_key(alias)
        # Try exact match first
        if normalized in data:
            return data[normalized]
        # Try original key
        if alias in data:
            return data[alias]
    return None


def _safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").replace("ريال", "").replace("SAR", "").strip())
    except (ValueError, TypeError):
        return None


class WebsiteChannelHandler:
    """
    Handles website contact form submissions for lead generation.
    Supports both JSON body and application/x-www-form-urlencoded data.
    """

    def __init__(self) -> None:
        self._log = logger.bind(channel="Website")

    def parse_submission(self, data: dict[str, Any]) -> Optional[LeadCreate]:
        """
        Parse a website contact form submission into a LeadCreate.
        يحلل طلب نموذج تواصل الموقع الإلكتروني.

        Handles various form designs with flexible field name matching.
        Supports both Arabic and English field names.

        Returns None if required fields are missing.
        """
        try:
            # Normalize all keys
            normalized_data = {_normalize_key(k): v for k, v in data.items()}

            # Extract name
            name = _extract_field(normalized_data, "name")

            # Handle split first/last name
            if not name:
                first = normalized_data.get("first_name", "")
                last = normalized_data.get("last_name", "")
                if first or last:
                    name = f"{first} {last}".strip()

            if not name:
                self._log.warning("Website form missing name field")
                return None

            name = str(name).strip()
            if len(name) < 2:
                self._log.warning("Website form name too short", name=name)
                return None

            # Extract contact info
            email_raw = _extract_field(normalized_data, "email")
            phone_raw = _extract_field(normalized_data, "phone")

            email = str(email_raw).strip().lower() if email_raw else None
            phone = str(phone_raw).strip() if phone_raw else None

            if not email and not phone:
                self._log.warning("Website form missing contact info", name=name)
                return None

            # Extract transport-specific fields
            company = _extract_field(normalized_data, "company")
            cargo_type = _extract_field(normalized_data, "cargo_type")
            route_from = _extract_field(normalized_data, "route_from")
            route_to = _extract_field(normalized_data, "route_to")
            fleet_raw = _extract_field(normalized_data, "fleet_size_needed")
            budget_raw = _extract_field(normalized_data, "budget_monthly")
            message = _extract_field(normalized_data, "message")

            fleet = _safe_int(fleet_raw)
            budget = _safe_float(budget_raw)

            # Use message as notes
            notes = str(message).strip()[:1000] if message else None

            # Detect source URL if present
            referral_url = normalized_data.get("referral_url") or normalized_data.get("page_url", "")

            self._log.info(
                "Website form parsed",
                name=name,
                company=company,
                has_email=bool(email),
                has_phone=bool(phone),
                has_cargo=bool(cargo_type),
            )

            return LeadCreate(
                name=name,
                company=str(company).strip() if company else None,
                phone=phone,
                email=email,
                source=LeadSource.WEBSITE,
                cargo_type=str(cargo_type).strip() if cargo_type else None,
                route_from=str(route_from).strip() if route_from else None,
                route_to=str(route_to).strip() if route_to else None,
                fleet_size_needed=fleet,
                budget_monthly=budget,
                notes=notes,
                raw_data={
                    "form_data": data,
                    "referral_url": referral_url,
                },
            )

        except Exception as exc:
            self._log.error("Failed to parse website form submission", error=str(exc))
            return None

    def parse_form_encoded(self, form_data: dict[str, str]) -> Optional[LeadCreate]:
        """
        Parse URL-encoded form data (application/x-www-form-urlencoded).
        يحلل بيانات النموذج المشفرة بـ URL.
        """
        # Convert all values to strings (form-encoded values are always strings)
        cleaned: dict[str, Any] = {}
        for k, v in form_data.items():
            if v:  # Skip empty strings
                cleaned[k] = v
        return self.parse_submission(cleaned)
