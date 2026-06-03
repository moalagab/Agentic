"""
Tool definitions for the Smartfield Lead Generation Agent.
تعريفات الأدوات لوكيل توليد العملاء المحتملين - سمارت فيلد

These tool schemas are passed to the Claude API as `tools` parameter,
enabling the agent to take structured actions.
"""

from typing import Any

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "classify_lead",
        "description": (
            "Classify and score a lead based on cargo type, fleet size, budget, and urgency. "
            "Returns a structured classification with score (0-100), category, priority, "
            "qualification notes in Arabic, and recommended next actions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Full name of the lead contact",
                },
                "company": {
                    "type": "string",
                    "description": "Company or organization name (if available)",
                },
                "cargo_type": {
                    "type": "string",
                    "description": "Type of cargo requiring refrigerated transport (e.g. meat, dairy, pharma)",
                },
                "fleet_size_needed": {
                    "type": "integer",
                    "description": "Number of refrigerated trucks needed",
                },
                "budget_monthly_sar": {
                    "type": "number",
                    "description": "Estimated monthly budget in Saudi Riyals (SAR)",
                },
                "urgency": {
                    "type": "string",
                    "enum": ["immediate", "within_month", "within_3_months", "just_inquiring", "unknown"],
                    "description": "How soon the client needs the service",
                },
                "route_from": {
                    "type": "string",
                    "description": "Origin city or region for transport",
                },
                "route_to": {
                    "type": "string",
                    "description": "Destination city or region for transport",
                },
                "additional_context": {
                    "type": "string",
                    "description": "Any other relevant information about the lead",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "create_crm_lead",
        "description": (
            "Create a new lead record in the CRM system (HubSpot or Airtable). "
            "Use this after classify_lead to persist the lead with all classification data. "
            "Returns the CRM record ID."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Full name of the lead contact",
                },
                "company": {
                    "type": "string",
                    "description": "Company name",
                },
                "phone": {
                    "type": "string",
                    "description": "Phone number with country code",
                },
                "email": {
                    "type": "string",
                    "description": "Email address",
                },
                "source": {
                    "type": "string",
                    "enum": ["linkedin", "website", "whatsapp", "google_forms", "ads", "manual"],
                    "description": "Lead source channel",
                },
                "category": {
                    "type": "string",
                    "enum": [
                        "food_transport", "pharma_transport", "industrial_cold",
                        "retail_chain", "logistics_company", "individual", "other"
                    ],
                    "description": "Classified business category",
                },
                "priority": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": "Lead priority level",
                },
                "score": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                    "description": "Qualification score 0-100",
                },
                "notes": {
                    "type": "string",
                    "description": "Qualification notes and context (Arabic preferred)",
                },
                "cargo_type": {
                    "type": "string",
                    "description": "Type of cargo",
                },
                "fleet_size_needed": {
                    "type": "integer",
                    "description": "Number of trucks needed",
                },
                "budget_monthly": {
                    "type": "number",
                    "description": "Monthly budget in SAR",
                },
                "crm_target": {
                    "type": "string",
                    "enum": ["hubspot", "airtable", "both"],
                    "description": "Which CRM(s) to create the lead in",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "send_whatsapp_notification",
        "description": (
            "Send a WhatsApp notification to the sales team about a new or updated lead. "
            "Messages are formatted in Arabic with lead details and priority indicator. "
            "Sends to all configured SALES_TEAM_WHATSAPP numbers."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "lead_name": {
                    "type": "string",
                    "description": "Name of the lead contact",
                },
                "company": {
                    "type": "string",
                    "description": "Company name",
                },
                "phone": {
                    "type": "string",
                    "description": "Lead's phone number",
                },
                "category": {
                    "type": "string",
                    "description": "Lead category (e.g. food_transport)",
                },
                "priority": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": "Lead priority",
                },
                "score": {
                    "type": "integer",
                    "description": "Qualification score 0-100",
                },
                "next_action": {
                    "type": "string",
                    "description": "The most important immediate action for the sales rep",
                },
                "crm_id": {
                    "type": "string",
                    "description": "CRM record ID for quick access",
                },
            },
            "required": ["lead_name", "priority", "score"],
        },
    },
    {
        "name": "create_follow_up_task",
        "description": (
            "Schedule a follow-up task in the CRM for the sales team. "
            "Creates a timed reminder to contact the lead. "
            "HIGH priority leads get same-day tasks, MEDIUM next business day, LOW within a week."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "crm_id": {
                    "type": "string",
                    "description": "The CRM record ID of the lead",
                },
                "task_type": {
                    "type": "string",
                    "enum": ["call", "email", "whatsapp", "meeting", "proposal"],
                    "description": "Type of follow-up action",
                },
                "task_title": {
                    "type": "string",
                    "description": "Brief title for the task (in Arabic preferred)",
                },
                "task_notes": {
                    "type": "string",
                    "description": "Detailed notes for the sales rep about this follow-up",
                },
                "priority": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": "Task priority matching lead priority",
                },
                "due_hours_from_now": {
                    "type": "integer",
                    "description": "Hours from now when this task is due (1 for high priority, 24 for medium, 168 for low)",
                },
                "assigned_to": {
                    "type": "string",
                    "description": "Sales rep email to assign this task to",
                },
            },
            "required": ["crm_id", "task_type", "task_title", "priority"],
        },
    },
    {
        "name": "search_existing_lead",
        "description": (
            "Search the CRM for an existing lead by email or phone number. "
            "Use this FIRST before creating a new lead to avoid duplicates. "
            "Returns the existing lead data if found, or null if not found."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "description": "Email address to search for",
                },
                "phone": {
                    "type": "string",
                    "description": "Phone number to search for",
                },
                "company": {
                    "type": "string",
                    "description": "Company name to search for (used as additional filter)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "update_lead_status",
        "description": (
            "Update the status of an existing lead in the CRM. "
            "Use this when a duplicate is found to update their record with new information, "
            "or to change the pipeline status after a sales interaction."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "crm_id": {
                    "type": "string",
                    "description": "The CRM record ID of the lead to update",
                },
                "status": {
                    "type": "string",
                    "enum": ["new", "contacted", "qualified", "unqualified", "converted", "lost"],
                    "description": "New status for the lead",
                },
                "notes": {
                    "type": "string",
                    "description": "Update notes explaining the status change",
                },
                "score": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                    "description": "Updated qualification score",
                },
                "priority": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": "Updated priority level",
                },
            },
            "required": ["crm_id"],
        },
    },
    {
        "name": "get_lead_analytics",
        "description": (
            "Retrieve pipeline analytics and statistics from the CRM. "
            "Returns counts by status, category, source, conversion rates, and average scores. "
            "Useful for reporting and understanding the current pipeline health."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "enum": ["today", "this_week", "this_month", "last_30_days", "all_time"],
                    "description": "Time period for the analytics",
                },
                "group_by": {
                    "type": "string",
                    "enum": ["status", "category", "source", "priority", "assigned_to"],
                    "description": "Field to group analytics by",
                },
            },
            "required": [],
        },
    },
]


def get_tool_by_name(name: str) -> dict[str, Any] | None:
    """Look up a tool definition by its name."""
    return next((t for t in TOOL_DEFINITIONS if t["name"] == name), None)


__all__ = ["TOOL_DEFINITIONS", "get_tool_by_name"]
