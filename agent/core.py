"""
Core Smartfield Lead Generation Agent powered by Claude.
وكيل توليد العملاء المحتملين الأساسي المدعوم بكلود

Uses Claude's tool_use capability in an agentic loop to fully process leads:
classify, deduplicate, save to CRM, notify sales team, and schedule follow-ups.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

import anthropic
import structlog

from agent.prompts import SYSTEM_PROMPT_AR, TOOL_RESULT_PROMPT
from agent.tools import TOOL_DEFINITIONS
from models.lead import Lead, LeadCategory, LeadCreate, LeadPriority, LeadStatus, ProcessedLead

if TYPE_CHECKING:
    from config import Settings
    from crm.base import BaseCRM
    from notifications.whatsapp import WhatsAppNotifier

logger = structlog.get_logger(__name__)

# Maximum number of agentic loop iterations to prevent infinite loops
MAX_ITERATIONS = 10
CLAUDE_MODEL = "claude-sonnet-4-6"


class SmartfieldLeadAgent:
    """
    AI-powered agent for lead qualification and CRM management.
    Orchestrates Claude tool calls to classify, store, and act on incoming leads.
    """

    def __init__(
        self,
        config: "Settings",
        crm_client: "BaseCRM",
        notifier: "WhatsAppNotifier",
        fallback_crm: "BaseCRM | None" = None,
    ) -> None:
        self.config = config
        self.crm_client = crm_client
        self.fallback_crm = fallback_crm
        self.notifier = notifier
        self.client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
        self._log = logger.bind(component="SmartfieldLeadAgent")

    async def process_lead(self, lead_create: LeadCreate) -> ProcessedLead:
        """
        Main entry point: fully process a lead using the Claude agentic loop.

        Steps performed by the agent (Claude decides the order):
          1. search_existing_lead  - deduplicate
          2. classify_lead         - score and categorize
          3. create_crm_lead       - persist to CRM
          4. send_whatsapp_notification - alert sales team
          5. create_follow_up_task - schedule follow-up

        Returns a ProcessedLead with full classification data.
        """
        start_ts = time.monotonic()
        lead = lead_create.to_lead()
        tool_calls_made: list[str] = []

        log = self._log.bind(
            lead_name=lead.name,
            lead_phone=lead.phone,
            lead_email=lead.email,
            lead_source=lead.source,
        )
        log.info("Starting lead processing")

        # ── Build the initial user message with lead data ──────────────────────
        user_message = self._build_user_message(lead_create)

        messages: list[dict[str, Any]] = [
            {"role": "user", "content": user_message},
        ]

        # Accumulate state from tool results throughout the loop
        classification_result: dict[str, Any] = {}
        crm_id: str | None = None
        notification_sent = False
        crm_saved = False
        final_text: str = ""

        iteration = 0

        # ── Agentic loop: keep calling Claude until it stops using tools ──────
        while iteration < MAX_ITERATIONS:
            iteration += 1
            log.debug("Agent iteration", iteration=iteration)

            try:
                response = await self.client.messages.create(
                    model=CLAUDE_MODEL,
                    max_tokens=4096,
                    system=[
                        {
                            "type": "text",
                            "text": SYSTEM_PROMPT_AR,
                            "cache_control": {"type": "ephemeral"},  # prompt caching
                        }
                    ],
                    tools=TOOL_DEFINITIONS,
                    messages=messages,
                )
            except anthropic.APIError as exc:
                log.error("Claude API error", error=str(exc), iteration=iteration)
                break

            log.debug(
                "Claude response received",
                stop_reason=response.stop_reason,
                content_blocks=len(response.content),
            )

            # Collect text from this response
            for block in response.content:
                if hasattr(block, "text"):
                    final_text = block.text

            # If Claude is done (no more tool calls), exit the loop
            if response.stop_reason == "end_turn":
                log.info("Agent completed processing (end_turn)")
                break

            # Gather all tool_use blocks
            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
            if not tool_use_blocks:
                log.info("No tool calls in response; stopping loop")
                break

            # Append Claude's response to message history
            messages.append({"role": "assistant", "content": response.content})

            # ── Execute each tool and collect results ──────────────────────────
            tool_results: list[dict[str, Any]] = []
            for tool_block in tool_use_blocks:
                tool_name = tool_block.name
                tool_input = tool_block.input
                tool_calls_made.append(tool_name)

                log.info("Executing tool", tool=tool_name, input_keys=list(tool_input.keys()))

                try:
                    result = await self._execute_tool(
                        tool_name=tool_name,
                        tool_input=tool_input,
                        lead=lead,
                        classification_result=classification_result,
                        crm_id=crm_id,
                    )

                    # Update agent state from tool results
                    if tool_name == "classify_lead":
                        classification_result = result
                        # Apply classification to lead object
                        lead = self._apply_classification(lead, result)

                    elif tool_name == "create_crm_lead":
                        crm_id = result.get("crm_id")
                        if crm_id:
                            lead.crm_id = crm_id
                            crm_saved = True

                    elif tool_name == "send_whatsapp_notification":
                        notification_sent = result.get("success", False)

                    elif tool_name == "search_existing_lead":
                        existing = result.get("existing_lead")
                        if existing:
                            # Use existing CRM ID if found
                            crm_id = existing.get("crm_id")
                            log.info("Duplicate lead found", existing_crm_id=crm_id)

                    log.debug("Tool result", tool=tool_name, result_keys=list(result.keys()))

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_block.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    })

                except Exception as exc:
                    log.error("Tool execution failed", tool=tool_name, error=str(exc))
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_block.id,
                        "content": json.dumps(
                            {"error": str(exc), "tool": tool_name},
                            ensure_ascii=False,
                        ),
                        "is_error": True,
                    })

            # Feed tool results back to Claude for the next iteration
            messages.append({"role": "user", "content": tool_results})

        # ── Build final ProcessedLead ──────────────────────────────────────────
        elapsed_ms = (time.monotonic() - start_ts) * 1000

        # Ensure score and priority are set even if classification failed
        if lead.score == 0 and not classification_result:
            lead.score = 30  # default score for unclassified leads
        if not classification_result:
            classification_result = {
                "qualification_notes": "التصنيف لم يكتمل - تحتاج مراجعة يدوية",
                "next_actions": ["مراجعة العميل يدوياً", "التواصل عبر الهاتف"],
            }

        processed = ProcessedLead(
            lead=lead,
            classification_reasoning=classification_result.get(
                "qualification_notes",
                "No classification reasoning available.",
            ),
            next_actions=classification_result.get("next_actions", []),
            processing_time_ms=round(elapsed_ms, 2),
            agent_tool_calls=tool_calls_made,
            crm_saved=crm_saved,
            notification_sent=notification_sent,
        )

        log.info(
            "Lead processing complete",
            score=lead.score,
            priority=lead.priority,
            category=lead.category,
            crm_saved=crm_saved,
            notification_sent=notification_sent,
            elapsed_ms=round(elapsed_ms, 2),
            tool_calls=tool_calls_made,
        )

        return processed

    # ──────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _build_user_message(self, lead_create: LeadCreate) -> str:
        """Format the incoming lead data as a structured message for Claude."""
        parts = [
            "## عميل محتمل جديد - معالجة مطلوبة",
            "",
            f"**الاسم:** {lead_create.name}",
        ]
        if lead_create.company:
            parts.append(f"**الشركة:** {lead_create.company}")
        if lead_create.phone:
            parts.append(f"**الهاتف:** {lead_create.phone}")
        if lead_create.email:
            parts.append(f"**البريد الإلكتروني:** {lead_create.email}")
        parts.append(f"**المصدر:** {lead_create.source}")
        if lead_create.cargo_type:
            parts.append(f"**نوع البضاعة:** {lead_create.cargo_type}")
        if lead_create.route_from or lead_create.route_to:
            route = f"{lead_create.route_from or '؟'} → {lead_create.route_to or '؟'}"
            parts.append(f"**المسار:** {route}")
        if lead_create.fleet_size_needed:
            parts.append(f"**عدد الشاحنات المطلوبة:** {lead_create.fleet_size_needed}")
        if lead_create.budget_monthly:
            parts.append(f"**الميزانية الشهرية:** {lead_create.budget_monthly:,.0f} ريال")
        if lead_create.notes:
            parts.append(f"**ملاحظات:** {lead_create.notes}")
        if lead_create.raw_data:
            parts.append(f"**البيانات الخام:** {json.dumps(lead_create.raw_data, ensure_ascii=False)}")

        parts.extend([
            "",
            "يرجى معالجة هذا العميل باتباع الخطوات التالية:",
            "1. البحث عن العميل في قاعدة البيانات (search_existing_lead)",
            "2. تصنيفه وتقييمه (classify_lead)",
            "3. حفظه في نظام CRM (create_crm_lead)",
            "4. إشعار فريق المبيعات (send_whatsapp_notification)",
            "5. إنشاء مهمة المتابعة (create_follow_up_task)",
        ])

        return "\n".join(parts)

    def _apply_classification(self, lead: Lead, classification: dict[str, Any]) -> Lead:
        """Update lead fields from classification result."""
        try:
            if cat_str := classification.get("category"):
                try:
                    lead.category = LeadCategory(cat_str.lower())
                except ValueError:
                    lead.category = LeadCategory.OTHER

            if pri_str := classification.get("priority"):
                try:
                    lead.priority = LeadPriority(pri_str.lower())
                except ValueError:
                    lead.priority = LeadPriority.MEDIUM

            if score := classification.get("score"):
                lead.score = max(0, min(100, int(score)))

            if notes := classification.get("qualification_notes"):
                lead.notes = notes

            lead.update_timestamp()
        except Exception as exc:
            self._log.warning("Failed to apply classification to lead", error=str(exc))

        return lead

    # ──────────────────────────────────────────────────────────────────────────
    # Tool dispatcher
    # ──────────────────────────────────────────────────────────────────────────

    async def _execute_tool(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        lead: Lead,
        classification_result: dict[str, Any],
        crm_id: str | None,
    ) -> dict[str, Any]:
        """Dispatch tool calls to their implementations."""

        if tool_name == "search_existing_lead":
            return await self._tool_search_existing_lead(tool_input)

        elif tool_name == "classify_lead":
            return await self._tool_classify_lead(tool_input, lead)

        elif tool_name == "create_crm_lead":
            return await self._tool_create_crm_lead(tool_input, lead)

        elif tool_name == "send_whatsapp_notification":
            return await self._tool_send_whatsapp_notification(tool_input, lead)

        elif tool_name == "create_follow_up_task":
            return await self._tool_create_follow_up_task(tool_input, lead, crm_id)

        elif tool_name == "update_lead_status":
            return await self._tool_update_lead_status(tool_input)

        elif tool_name == "get_lead_analytics":
            return await self._tool_get_lead_analytics(tool_input)

        else:
            self._log.warning("Unknown tool called", tool=tool_name)
            return {"error": f"Unknown tool: {tool_name}"}

    # ──────────────────────────────────────────────────────────────────────────
    # Individual tool implementations
    # ──────────────────────────────────────────────────────────────────────────

    async def _tool_search_existing_lead(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        """Search CRM for duplicate lead."""
        email = tool_input.get("email")
        phone = tool_input.get("phone")

        if not email and not phone:
            return {"found": False, "existing_lead": None, "message": "No search criteria provided"}

        try:
            existing = await self.crm_client.search_lead(email=email, phone=phone)
            if existing:
                return {
                    "found": True,
                    "existing_lead": {
                        "crm_id": existing.crm_id,
                        "name": existing.name,
                        "company": existing.company,
                        "status": existing.status,
                        "score": existing.score,
                        "created_at": existing.created_at.isoformat(),
                    },
                    "message": f"Lead already exists with CRM ID: {existing.crm_id}",
                }
            return {"found": False, "existing_lead": None, "message": "No existing lead found"}
        except Exception as exc:
            self._log.error("search_existing_lead failed", error=str(exc))
            return {"found": False, "existing_lead": None, "error": str(exc)}

    async def _tool_classify_lead(
        self, tool_input: dict[str, Any], lead: Lead
    ) -> dict[str, Any]:
        """
        Execute rule-based classification and return structured result.
        The score calculated here is the definitive one; Claude also provides
        qualification notes and next_actions in its response text.
        """
        from processors.classifier import LeadClassifier

        classifier = LeadClassifier()
        # Build a temporary LeadCreate from the tool input merged with existing lead data
        from models.lead import LeadCreate as LC

        try:
            temp_create = LC(
                name=tool_input.get("name", lead.name),
                company=tool_input.get("company", lead.company),
                phone=lead.phone,
                email=lead.email,
                source=lead.source,
                cargo_type=tool_input.get("cargo_type", lead.cargo_type),
                fleet_size_needed=tool_input.get("fleet_size_needed", lead.fleet_size_needed),
                budget_monthly=tool_input.get("budget_monthly_sar", lead.budget_monthly),
                notes=lead.notes,
                raw_data=lead.raw_data,
            )
        except Exception:
            temp_create = None

        if temp_create:
            pre_class = classifier.pre_classify(temp_create)
        else:
            pre_class = {"score": 30, "category": "other"}

        # Map urgency to score contribution
        urgency_map = {
            "immediate": 20,
            "within_month": 15,
            "within_3_months": 8,
            "just_inquiring": 3,
            "unknown": 5,
        }
        urgency = tool_input.get("urgency", "unknown")
        urgency_score = urgency_map.get(urgency, 5)

        # Refine score with the urgency from Claude's input
        refined_score = min(100, pre_class["score"] + urgency_score)

        # Determine priority
        if refined_score >= 70:
            priority = "high"
        elif refined_score >= 40:
            priority = "medium"
        else:
            priority = "low"

        # Build qualification notes
        cargo = tool_input.get("cargo_type", lead.cargo_type or "غير محدد")
        fleet = tool_input.get("fleet_size_needed", lead.fleet_size_needed)
        budget = tool_input.get("budget_monthly_sar", lead.budget_monthly)

        notes = (
            f"تقييم العميل: {refined_score}/100\n"
            f"نوع البضاعة: {cargo}\n"
            f"عدد الشاحنات: {fleet or 'غير محدد'}\n"
            f"الميزانية: {budget or 'غير محدد'} ريال شهرياً\n"
            f"الإلحاح: {urgency}\n"
            f"التصنيف المقترح: {pre_class.get('category', 'other')}"
        )

        # Next actions based on priority
        if priority == "high":
            next_actions = [
                "الاتصال بالعميل خلال ساعة",
                "إعداد عرض أسعار مخصص وإرساله فوراً",
                "جدولة اجتماع أو زيارة ميدانية",
            ]
        elif priority == "medium":
            next_actions = [
                "الاتصال بالعميل خلال يوم عمل",
                "إرسال كتيب الخدمات والأسعار",
                "إضافة العميل لقائمة المتابعة الأسبوعية",
            ]
        else:
            next_actions = [
                "إرسال بريد إلكتروني تعريفي",
                "إضافة لقائمة التواصل الشهري",
                "مراجعة البيانات للتأكد من اكتمالها",
            ]

        return {
            "category": pre_class.get("category", "other"),
            "priority": priority,
            "score": refined_score,
            "qualification_notes": notes,
            "next_actions": next_actions,
        }

    async def _tool_create_crm_lead(
        self, tool_input: dict[str, Any], lead: Lead
    ) -> dict[str, Any]:
        """Create lead in the configured CRM."""
        # Apply any overrides from tool_input to the lead
        merged_lead = lead.model_copy(
            update={
                k: v
                for k, v in {
                    "notes": tool_input.get("notes", lead.notes),
                    "score": tool_input.get("score", lead.score),
                }.items()
                if v is not None
            }
        )

        results: dict[str, Any] = {"success": False, "crm_id": None, "errors": []}

        try:
            crm_id = await self.crm_client.create_lead(merged_lead)
            results["crm_id"] = crm_id
            results["success"] = True
            results["crm"] = self.config.PRIMARY_CRM
            self._log.info("Lead saved to primary CRM", crm=self.config.PRIMARY_CRM, crm_id=crm_id)
        except Exception as exc:
            results["errors"].append(f"Primary CRM ({self.config.PRIMARY_CRM}) failed: {exc}")
            self._log.error("Primary CRM save failed", error=str(exc))

            # Try fallback CRM if available
            if self.fallback_crm:
                try:
                    crm_id = await self.fallback_crm.create_lead(merged_lead)
                    results["crm_id"] = crm_id
                    results["success"] = True
                    results["crm"] = "fallback"
                    self._log.info("Lead saved to fallback CRM", crm_id=crm_id)
                except Exception as fallback_exc:
                    results["errors"].append(f"Fallback CRM failed: {fallback_exc}")
                    self._log.error("Fallback CRM save also failed", error=str(fallback_exc))

        return results

    async def _tool_send_whatsapp_notification(
        self, tool_input: dict[str, Any], lead: Lead
    ) -> dict[str, Any]:
        """Send WhatsApp notification to sales team."""
        if not self.config.SALES_TEAM_WHATSAPP:
            return {"success": False, "message": "No sales team WhatsApp numbers configured"}

        try:
            # Build a temporary ProcessedLead for the notifier
            temp_processed = ProcessedLead(
                lead=lead,
                classification_reasoning=lead.notes or "No reasoning",
                next_actions=[tool_input.get("next_action", "Follow up with lead")],
            )
            await self.notifier.notify_new_lead(lead=lead, processed=temp_processed)
            return {"success": True, "recipients": len(self.config.SALES_TEAM_WHATSAPP)}
        except Exception as exc:
            self._log.error("WhatsApp notification failed", error=str(exc))
            return {"success": False, "error": str(exc)}

    async def _tool_create_follow_up_task(
        self,
        tool_input: dict[str, Any],
        lead: Lead,
        crm_id: str | None,
    ) -> dict[str, Any]:
        """Schedule a follow-up task in CRM."""
        effective_crm_id = tool_input.get("crm_id") or crm_id or lead.crm_id

        if not effective_crm_id:
            return {"success": False, "message": "No CRM ID available to attach task"}

        # Default due hours by priority
        priority = tool_input.get("priority", lead.priority)
        default_hours = {"high": 1, "medium": 24, "low": 168}.get(str(priority).lower(), 24)
        due_hours = tool_input.get("due_hours_from_now", default_hours)

        task = {
            "title": tool_input.get("task_title", f"متابعة العميل: {lead.name}"),
            "type": tool_input.get("task_type", "call"),
            "notes": tool_input.get(
                "task_notes",
                f"العميل: {lead.name} - الشركة: {lead.company or 'غير محدد'} - التقييم: {lead.score}/100",
            ),
            "due_hours": due_hours,
            "priority": priority,
            "assigned_to": tool_input.get("assigned_to"),
        }

        try:
            task_id = await self.crm_client.create_task(effective_crm_id, task)
            return {"success": True, "task_id": task_id, "due_hours": due_hours}
        except Exception as exc:
            self._log.error("create_follow_up_task failed", error=str(exc))
            return {"success": False, "error": str(exc)}

    async def _tool_update_lead_status(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        """Update lead status in CRM."""
        crm_id = tool_input.get("crm_id")
        if not crm_id:
            return {"success": False, "message": "crm_id is required"}

        update_data = {k: v for k, v in {
            "status": tool_input.get("status"),
            "notes": tool_input.get("notes"),
            "score": tool_input.get("score"),
            "priority": tool_input.get("priority"),
        }.items() if v is not None}

        try:
            success = await self.crm_client.update_lead(crm_id, update_data)
            return {"success": success, "crm_id": crm_id, "updated_fields": list(update_data.keys())}
        except Exception as exc:
            self._log.error("update_lead_status failed", error=str(exc))
            return {"success": False, "error": str(exc)}

    async def _tool_get_lead_analytics(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        """Retrieve pipeline analytics from CRM."""
        try:
            stats = await self.crm_client.get_pipeline_stats()
            return {"success": True, "stats": stats, "period": tool_input.get("period", "all_time")}
        except Exception as exc:
            self._log.error("get_lead_analytics failed", error=str(exc))
            return {"success": False, "error": str(exc)}
