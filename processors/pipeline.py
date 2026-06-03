"""
Full lead processing pipeline for Smartfield Lead Generation System.
خط معالجة العملاء المحتملين الكامل

Orchestrates: deduplication → pre-classification → AI classification
              → CRM save → notification → follow-up task
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

import structlog

from models.lead import Lead, LeadCreate, ProcessedLead

if TYPE_CHECKING:
    from agent.core import SmartfieldLeadAgent
    from config import Settings
    from crm.base import BaseCRM
    from notifications.whatsapp import WhatsAppNotifier
    from processors.classifier import LeadClassifier

logger = structlog.get_logger(__name__)


class LeadPipeline:
    """
    Orchestrates the complete lead processing flow from any channel.
    يُنسق عملية معالجة العملاء المحتملين من أي قناة.

    Flow:
      1. Receive LeadCreate from webhook/channel handler
      2. Deduplicate check via CRM search
      3. Pre-classify with LeadClassifier (fast, no API calls)
      4. Run through SmartfieldLeadAgent (Claude AI classification)
      5. Save to primary CRM (with fallback to secondary)
      6. Send WhatsApp notification to sales team
      7. Create follow-up task in CRM
      8. Return ProcessedLead
    """

    def __init__(
        self,
        config: "Settings",
        agent: "SmartfieldLeadAgent",
        primary_crm: "BaseCRM",
        notifier: "WhatsAppNotifier",
        classifier: "LeadClassifier",
        fallback_crm: "BaseCRM | None" = None,
    ) -> None:
        self.config = config
        self.agent = agent
        self.primary_crm = primary_crm
        self.notifier = notifier
        self.classifier = classifier
        self.fallback_crm = fallback_crm
        self._log = logger.bind(component="LeadPipeline")

    async def process(self, lead_create: LeadCreate) -> ProcessedLead:
        """
        Main pipeline entry point.
        نقطة الدخول الرئيسية لخط المعالجة.

        Args:
            lead_create: Incoming lead data from any channel.

        Returns:
            ProcessedLead: Fully processed lead with AI classification,
                           CRM ID, and notification status.
        """
        start_ts = time.monotonic()
        log = self._log.bind(
            lead_name=lead_create.name,
            lead_source=lead_create.source,
        )

        log.info("Pipeline started")

        # ── Step 1: Pre-classify (fast, no API) ──────────────────────────────
        try:
            pre_class = self.classifier.pre_classify(lead_create)
            log.info(
                "Pre-classification complete",
                score=pre_class["score"],
                category=pre_class["category"],
            )
        except Exception as exc:
            log.warning("Pre-classification failed", error=str(exc))
            pre_class = {"score": 30, "category": "other", "priority": "medium"}

        # ── Step 2: Check for duplicates ──────────────────────────────────────
        existing_crm_id: str | None = None
        try:
            existing = await self._check_duplicate(lead_create)
            if existing:
                existing_crm_id = existing.crm_id
                log.info(
                    "Duplicate lead found",
                    existing_crm_id=existing_crm_id,
                    existing_score=existing.score,
                )
        except Exception as exc:
            log.warning("Duplicate check failed", error=str(exc))

        # ── Step 3: Run through AI agent (Claude) ────────────────────────────
        try:
            processed = await self.agent.process_lead(lead_create)
            log.info(
                "Agent processing complete",
                score=processed.lead.score,
                priority=processed.lead.priority,
                category=processed.lead.category,
                crm_saved=processed.crm_saved,
                tool_calls=processed.agent_tool_calls,
            )
        except Exception as exc:
            log.error("Agent processing failed", error=str(exc))
            # Create a minimal ProcessedLead from pre-classification
            processed = self._build_fallback_processed(lead_create, pre_class, str(exc))

        # ── Step 4: Ensure CRM save if agent didn't handle it ─────────────────
        if not processed.crm_saved:
            log.info("Agent did not save to CRM; attempting manual save")
            await self._manual_crm_save(processed.lead, log)

        # ── Step 5: Ensure notification if agent didn't send it ───────────────
        if not processed.notification_sent and self.config.SALES_TEAM_WHATSAPP:
            log.info("Agent did not send notification; attempting manual notification")
            try:
                await self.notifier.notify_new_lead(processed.lead, processed)
                processed = processed.model_copy(update={"notification_sent": True})
            except Exception as exc:
                log.warning("Manual notification failed", error=str(exc))

        elapsed_ms = (time.monotonic() - start_ts) * 1000
        log.info(
            "Pipeline complete",
            elapsed_ms=round(elapsed_ms, 2),
            crm_saved=processed.crm_saved,
            notification_sent=processed.notification_sent,
        )

        return processed

    async def _check_duplicate(self, lead_create: LeadCreate) -> Lead | None:
        """Search primary CRM for an existing lead."""
        return await self.primary_crm.search_lead(
            email=lead_create.email,
            phone=lead_create.phone,
        )

    async def _manual_crm_save(self, lead: Lead, log: Any) -> None:
        """
        Fallback CRM save when the agent's tool_use didn't complete it.
        يحفظ العميل في CRM يدوياً إذا لم يكمل الوكيل الحفظ.
        """
        # Try primary CRM
        try:
            crm_id = await self.primary_crm.create_lead(lead)
            lead.crm_id = crm_id
            log.info("Manual CRM save successful", crm=self.config.PRIMARY_CRM, crm_id=crm_id)
            return
        except Exception as exc:
            log.warning("Primary CRM manual save failed", error=str(exc))

        # Try fallback CRM
        if self.fallback_crm:
            try:
                crm_id = await self.fallback_crm.create_lead(lead)
                lead.crm_id = crm_id
                log.info("Fallback CRM save successful", crm_id=crm_id)
            except Exception as exc:
                log.error("Fallback CRM manual save also failed", error=str(exc))

    def _build_fallback_processed(
        self,
        lead_create: LeadCreate,
        pre_class: dict[str, Any],
        error_msg: str,
    ) -> ProcessedLead:
        """
        Build a minimal ProcessedLead when the agent crashes.
        Applies pre-classification results to the lead.
        """
        from models.lead import LeadCategory, LeadPriority

        lead = lead_create.to_lead()

        try:
            lead.category = LeadCategory(pre_class.get("category", "other"))
        except ValueError:
            lead.category = LeadCategory.OTHER

        try:
            lead.priority = LeadPriority(pre_class.get("priority", "medium"))
        except ValueError:
            lead.priority = LeadPriority.MEDIUM

        lead.score = pre_class.get("score", 30)
        lead.notes = f"[تصنيف أولي - فشل الوكيل: {error_msg[:200]}]"

        return ProcessedLead(
            lead=lead,
            classification_reasoning=f"Fallback classification due to agent error: {error_msg[:200]}",
            next_actions=["مراجعة العميل يدوياً", "الاتصال هاتفياً في أقرب وقت"],
            crm_saved=False,
            notification_sent=False,
        )


def create_pipeline_from_config(config: "Settings") -> LeadPipeline:
    """
    Factory function to create a fully wired LeadPipeline from settings.
    دالة المصنع لإنشاء خط معالجة كامل من الإعدادات.

    Creates the correct CRM adapters, agent, classifier, and notifier
    based on configuration and wires them together.
    """
    from agent.core import SmartfieldLeadAgent
    from crm.airtable import AirtableCRM
    from crm.hubspot import HubSpotCRM
    from notifications.whatsapp import WhatsAppNotifier
    from processors.classifier import LeadClassifier

    # ── Create CRM adapters ───────────────────────────────────────────────────
    hubspot_crm: BaseCRM | None = None
    airtable_crm: BaseCRM | None = None

    if config.is_hubspot_configured():
        hubspot_crm = HubSpotCRM(
            api_key=config.HUBSPOT_API_KEY,
            portal_id=config.HUBSPOT_PORTAL_ID,
        )

    if config.is_airtable_configured():
        airtable_crm = AirtableCRM(
            api_key=config.AIRTABLE_API_KEY,
            base_id=config.AIRTABLE_BASE_ID,
            table_name=config.AIRTABLE_TABLE_NAME,
        )

    # Select primary and fallback CRM
    if config.PRIMARY_CRM == "hubspot" and hubspot_crm:
        primary_crm = hubspot_crm
        fallback_crm = airtable_crm
    elif config.PRIMARY_CRM == "airtable" and airtable_crm:
        primary_crm = airtable_crm
        fallback_crm = hubspot_crm
    elif hubspot_crm:
        primary_crm = hubspot_crm
        fallback_crm = airtable_crm
    elif airtable_crm:
        primary_crm = airtable_crm
        fallback_crm = None
    else:
        # No CRM configured — use a no-op stub so the system doesn't crash
        logger.warning("No CRM configured; using NullCRM stub")
        primary_crm = _NullCRM()
        fallback_crm = None

    # ── Create notifier ───────────────────────────────────────────────────────
    notifier = WhatsAppNotifier(config)

    # ── Create agent ──────────────────────────────────────────────────────────
    agent = SmartfieldLeadAgent(
        config=config,
        crm_client=primary_crm,
        notifier=notifier,
        fallback_crm=fallback_crm,
    )

    # ── Create classifier ─────────────────────────────────────────────────────
    classifier = LeadClassifier()

    return LeadPipeline(
        config=config,
        agent=agent,
        primary_crm=primary_crm,
        notifier=notifier,
        classifier=classifier,
        fallback_crm=fallback_crm,
    )


# ──────────────────────────────────────────────────────────────────────────────
# NullCRM stub for when no CRM is configured
# ──────────────────────────────────────────────────────────────────────────────

from crm.base import BaseCRM
from models.lead import Lead


class _NullCRM(BaseCRM):
    """No-op CRM that logs and returns fake IDs. Used when no CRM is configured."""

    _log = logger.bind(crm="NullCRM")

    async def create_lead(self, lead: Lead) -> str:
        self._log.warning("NullCRM.create_lead called; lead not saved", lead_name=lead.name)
        return f"null-{lead.id[:8]}"

    async def update_lead(self, crm_id: str, data: dict) -> bool:
        self._log.warning("NullCRM.update_lead called", crm_id=crm_id)
        return False

    async def search_lead(
        self,
        email: str | None = None,
        phone: str | None = None,
    ) -> Lead | None:
        return None

    async def create_task(self, crm_id: str, task: dict) -> str:
        self._log.warning("NullCRM.create_task called", crm_id=crm_id)
        return ""

    async def get_pipeline_stats(self) -> dict:
        return {"crm": "null", "message": "No CRM configured", "total_leads": 0}
