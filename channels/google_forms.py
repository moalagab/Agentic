"""
Google Forms / Apps Script channel handler for Smartfield.
معالج قناة نماذج Google للعملاء المحتملين

Parses webhook payloads sent by a Google Apps Script onFormSubmit
trigger and maps form fields to LeadCreate objects.
"""

from __future__ import annotations

from typing import Any, Optional

import structlog

from models.lead import LeadCreate, LeadSource

logger = structlog.get_logger(__name__)

# Mapping from Google Form question titles to our internal field names.
# Keys are lowercase, stripped versions of the question title.
# Adjust these to match the actual Arabic/English question titles in your form.
FORM_FIELD_MAP: dict[str, str] = {
    # Arabic question titles
    "الاسم الكامل": "name",
    "الاسم": "name",
    "اسمك": "name",
    "الشركة": "company",
    "اسم الشركة": "company",
    "رقم الجوال": "phone",
    "رقم الهاتف": "phone",
    "الهاتف": "phone",
    "البريد الإلكتروني": "email",
    "الإيميل": "email",
    "نوع البضاعة": "cargo_type",
    "نوع المنتج": "cargo_type",
    "من أين": "route_from",
    "نقطة الانطلاق": "route_from",
    "المدينة الأصل": "route_from",
    "إلى أين": "route_to",
    "الوجهة": "route_to",
    "المدينة الهدف": "route_to",
    "عدد الشاحنات": "fleet_size_needed",
    "عدد السيارات المطلوبة": "fleet_size_needed",
    "الميزانية الشهرية": "budget_monthly",
    "الميزانية": "budget_monthly",
    "ملاحظات إضافية": "notes",
    "تفاصيل أخرى": "notes",
    # English question titles
    "full name": "name",
    "name": "name",
    "company name": "company",
    "company": "company",
    "phone number": "phone",
    "phone": "phone",
    "email address": "email",
    "email": "email",
    "cargo type": "cargo_type",
    "type of goods": "cargo_type",
    "from city": "route_from",
    "origin": "route_from",
    "to city": "route_to",
    "destination": "route_to",
    "number of trucks": "fleet_size_needed",
    "trucks needed": "fleet_size_needed",
    "monthly budget": "budget_monthly",
    "budget sar": "budget_monthly",
    "notes": "notes",
    "additional notes": "notes",
}


def _map_form_responses(responses: dict[str, Any]) -> dict[str, Any]:
    """
    Map Google Form responses to our internal field names.
    يحول إجابات نموذج Google إلى حقولنا الداخلية.

    The Google Apps Script payload typically looks like:
    {
      "responses": {
        "Question Title 1": "Answer 1",
        "Question Title 2": "Answer 2",
        ...
      }
    }
    """
    mapped: dict[str, Any] = {}
    for question, answer in responses.items():
        if not answer:
            continue
        normalized_question = question.strip().lower()
        field_name = FORM_FIELD_MAP.get(normalized_question)
        if field_name:
            mapped[field_name] = answer
        else:
            # Store unmapped fields in a prefix for raw_data
            mapped[f"form_{normalized_question.replace(' ', '_')}"] = answer
    return mapped


def _safe_int(value: Any) -> Optional[int]:
    """Safely parse an integer from various input types."""
    try:
        return int(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _safe_float(value: Any) -> Optional[float]:
    """Safely parse a float from various input types."""
    try:
        return float(str(value).replace(",", "").replace("ريال", "").strip())
    except (ValueError, TypeError):
        return None


class GoogleFormsHandler:
    """
    Handles incoming webhooks from Google Apps Script form submissions.

    Expected Google Apps Script webhook sender:
    ```javascript
    function onFormSubmit(e) {
      var responses = {};
      e.response.getItemResponses().forEach(function(item) {
        responses[item.getItem().getTitle()] = item.getResponse();
      });
      var payload = JSON.stringify({
        "formId": e.source.getId(),
        "responseId": e.response.getId(),
        "timestamp": e.response.getTimestamp().toISOString(),
        "responses": responses
      });
      UrlFetchApp.fetch("YOUR_WEBHOOK_URL/webhook/google-forms", {
        method: "post",
        contentType: "application/json",
        payload: payload
      });
    }
    ```
    """

    def __init__(self) -> None:
        self._log = logger.bind(channel="GoogleForms")

    def parse_webhook(self, payload: dict[str, Any]) -> Optional[LeadCreate]:
        """
        Parse a Google Apps Script form submission webhook into a LeadCreate.
        يحلل حمولة webhook نموذج Google ويحولها إلى LeadCreate.

        Supports both the Apps Script format and a flat key-value format.

        Returns None if required fields (name + phone/email) are missing.
        """
        try:
            # Handle both nested {"responses": {...}} and flat formats
            if "responses" in payload:
                raw_responses = payload["responses"]
            else:
                # Treat the entire payload as flat key-value responses
                raw_responses = {k: v for k, v in payload.items()
                                 if k not in ("formId", "responseId", "timestamp")}

            if not raw_responses:
                self._log.warning("Empty Google Form responses payload")
                return None

            mapped = _map_form_responses(raw_responses)

            name = mapped.get("name", "").strip()
            if not name:
                self._log.warning("Google Form submission missing name field")
                # Try to compose from first+last name if present
                first = mapped.get("form_first_name", "")
                last = mapped.get("form_last_name", "")
                name = f"{first} {last}".strip()
                if not name:
                    return None

            email = mapped.get("email", "").strip() or None
            phone = mapped.get("phone", "").strip() or None

            if not email and not phone:
                self._log.warning("Google Form submission missing contact info", name=name)
                # Still process if we have a name — better than dropping
                phone = mapped.get("form_phone", "") or None
                email = mapped.get("form_email", "") or None

            fleet = _safe_int(mapped.get("fleet_size_needed"))
            budget = _safe_float(mapped.get("budget_monthly"))

            # Build raw_data preserving all form fields
            raw_data: dict[str, Any] = {
                "form_id": payload.get("formId", ""),
                "response_id": payload.get("responseId", ""),
                "submission_timestamp": payload.get("timestamp", ""),
                "all_responses": raw_responses,
            }

            self._log.info(
                "Google Form submission parsed",
                name=name,
                has_email=bool(email),
                has_phone=bool(phone),
                mapped_fields=list(mapped.keys()),
            )

            return LeadCreate(
                name=name,
                company=mapped.get("company") or None,
                phone=phone,
                email=email,
                source=LeadSource.GOOGLE_FORMS,
                cargo_type=mapped.get("cargo_type") or None,
                route_from=mapped.get("route_from") or None,
                route_to=mapped.get("route_to") or None,
                fleet_size_needed=fleet,
                budget_monthly=budget,
                notes=mapped.get("notes") or None,
                raw_data=raw_data,
            )

        except Exception as exc:
            self._log.error("Failed to parse Google Forms webhook", error=str(exc))
            return None
