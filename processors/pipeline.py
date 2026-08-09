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

        # Lazy import to avoid circular imports
        from processors.followup_engine import FollowUpEngine
        self.followup_engine = FollowUpEngine(config, notifier=notifier)

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

        # ── Step 1: Dedup check FIRST (skip scoring cost on known leads) ──────
        existing_crm_id: str | None = None
        try:
            existing = await self._check_duplicate(lead_create)
            if existing:
                existing_crm_id = existing.crm_id
                log.info(
                    "Duplicate lead found — skipping full pipeline",
                    existing_crm_id=existing_crm_id,
                    existing_score=existing.score,
                )
                # Return early for exact duplicates (same phone already in CRM)
                from models.lead import LeadCategory, LeadPriority
                dup_lead = existing
                return ProcessedLead(
                    lead=dup_lead,
                    classification_reasoning="مكرر — العميل موجود مسبقاً في CRM",
                    next_actions=["تحديث السجل الموجود إن لزم"],
                    crm_saved=True,
                    notification_sent=False,
                )
        except Exception as exc:
            log.warning("Duplicate check failed", error=str(exc))

        # ── Step 2: Hybrid Scoring (rule-based — fast, no API calls) ──────────
        from processors.hybrid_scorer import HybridScorer
        hybrid_scorer = HybridScorer()
        try:
            hybrid_result = hybrid_scorer.score(lead_create)
            pre_class = {
                "score": hybrid_result.rule_score,
                "category": hybrid_result.category,
                "priority": hybrid_result.priority,
                "breakdown": hybrid_result.score_summary_ar,
                "estimated_monthly_revenue": hybrid_result.estimated_monthly_revenue,
                "estimated_trips": hybrid_result.estimated_trips,
                "confidence_score": hybrid_result.confidence_score,
                "probability_to_close": hybrid_result.probability_to_close,
                "expected_deal_value": hybrid_result.expected_deal_value,
            }
            log.info(
                "Hybrid scoring complete",
                rule_score=hybrid_result.rule_score,
                category=hybrid_result.category,
                priority=hybrid_result.priority,
                est_revenue=hybrid_result.estimated_monthly_revenue,
            )
        except Exception as exc:
            log.warning("Hybrid scoring failed, using fallback", error=str(exc))
            pre_class = {"score": 30, "category": "other", "priority": "medium"}

        # ── Step 3: Run through AI agent (Claude) ────────────────────────────
        try:
            processed = await self.agent.process_lead(lead_create, pre_scored=pre_class)
            # Stamp opportunity fields from hybrid scorer onto the lead
            opp_updates = {
                "probability_to_close": pre_class.get("probability_to_close", 0.0),
                "expected_monthly_revenue": pre_class.get("estimated_monthly_revenue", 0.0),
                "expected_trips_per_month": pre_class.get("estimated_trips", 0),
                "estimated_ltv": round(
                    pre_class.get("estimated_monthly_revenue", 0.0) * 12 * 2.5
                ),  # 2.5-year avg retention
            }
            processed = processed.model_copy(
                update={"lead": processed.lead.model_copy(update=opp_updates)}
            )
            log.info(
                "Agent processing complete",
                score=processed.lead.score,
                priority=processed.lead.priority,
                category=processed.lead.category,
                prob_close=processed.lead.probability_to_close,
                est_revenue=processed.lead.expected_monthly_revenue,
                ltv=processed.lead.estimated_ltv,
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
            crm_id_before = processed.lead.crm_id
            await self._manual_crm_save(processed.lead, log)
            # If crm_id was set (or changed), the save succeeded
            if processed.lead.crm_id:
                processed = processed.model_copy(update={"crm_saved": True})

        # ── Step 4b: Dual-write to fallback CRM (e.g. HubSpot) in background ──
        if processed.crm_saved and self.fallback_crm:
            asyncio.create_task(
                self._sync_to_fallback_crm(processed.lead, log)
            )

        # ── Step 4c: Start WhatsApp follow-up sequence in background ──────────
        if processed.crm_saved and processed.lead.phone:
            asyncio.create_task(
                self.followup_engine.start_sequence(
                    lead_phone=processed.lead.phone,
                    lead_name=processed.lead.name,
                    priority=processed.lead.priority,
                )
            )

        # ── Step 5: Ensure notification if agent didn't send it ───────────────
        if not processed.notification_sent and self.config.SALES_TEAM_WHATSAPP:
            log.info("Agent did not send notification; attempting manual notification")
            try:
                await self.notifier.notify_new_lead(processed.lead, processed)
                processed = processed.model_copy(update={"notification_sent": True})
                await self._log_lead_notification(processed.lead)
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

    async def _sync_to_fallback_crm(self, lead: Lead, log: Any) -> None:
        """
        Silently push lead to fallback CRM (HubSpot) after primary save.
        يرسل العميل إلى HubSpot في الخلفية بعد حفظه في Supabase.
        """
        try:
            crm_id = await self.fallback_crm.create_lead(lead)
            log.info("Dual-write to fallback CRM successful", crm_id=crm_id)
        except Exception as exc:
            log.warning("Dual-write to fallback CRM failed", error=str(exc))

    async def _log_lead_notification(self, lead: Lead) -> None:
        """
        Record the new-lead sales alert in notifications_log. The table has
        existed since the schema was first written but stayed at 0 rows —
        every Telegram/WhatsApp alert the system sent bypassed logging.
        """
        if not lead.id:
            return
        try:
            from crm.supabase_crm import SupabaseCRM
            from notifications.telegram import TelegramNotifier
            if not isinstance(self.primary_crm, SupabaseCRM):
                return
            channel = "telegram" if isinstance(self.notifier, TelegramNotifier) else "whatsapp"
            recipients = (
                self.config.TELEGRAM_OWNER_CHAT_IDS if channel == "telegram"
                else self.config.SALES_TEAM_WHATSAPP
            )
            await self.primary_crm.log_notification(
                lead_id=lead.id,
                channel=channel,
                recipient=",".join(recipients) if recipients else "",
                status="sent",
                payload={"type": "new_lead", "score": lead.score},
            )
        except Exception as exc:
            self._log.debug("log_notification skipped", error=str(exc))

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

        # Attach financial estimates
        for field in ("estimated_monthly_revenue", "estimated_trips",
                      "confidence_score", "probability_to_close", "expected_deal_value"):
            if field in pre_class:
                lead.__dict__[field] = pre_class[field]

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
    from crm.supabase_crm import SupabaseCRM
    from notifications.telegram import TelegramNotifier
    from notifications.whatsapp import WhatsAppNotifier
    from processors.classifier import LeadClassifier

    # ── Create CRM adapters ───────────────────────────────────────────────────
    supabase_crm: BaseCRM | None = None
    hubspot_crm: BaseCRM | None = None
    airtable_crm: BaseCRM | None = None

    if config.is_supabase_configured():
        supabase_crm = SupabaseCRM(url=config.SUPABASE_URL, key=config.SUPABASE_KEY)
        logger.info("Supabase CRM initialized — guaranteed persistence layer active")

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
    # Supabase is always preferred as primary (guaranteed persistence)
    if config.PRIMARY_CRM == "supabase" and supabase_crm:
        primary_crm = supabase_crm
        fallback_crm = hubspot_crm or airtable_crm
    elif config.PRIMARY_CRM == "hubspot" and hubspot_crm:
        primary_crm = hubspot_crm
        fallback_crm = supabase_crm or airtable_crm
    elif config.PRIMARY_CRM == "airtable" and airtable_crm:
        primary_crm = airtable_crm
        fallback_crm = supabase_crm or hubspot_crm
    elif supabase_crm:
        primary_crm = supabase_crm
        fallback_crm = hubspot_crm or airtable_crm
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

    # ── Create notifier (Telegram takes priority over WhatsApp) ──────────────
    if config.has_telegram_owners():
        notifier = TelegramNotifier(config)
    else:
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
