"""
FastAPI webhook server for Smartfield Lead Generation System.
خادم webhook لنظام توليد العملاء المحتملين - سمارت فيلد

Exposes HTTP endpoints for all inbound lead channels:
- WhatsApp Business Cloud API
- LinkedIn Lead Gen Forms
- Google Forms (Apps Script)
- Website contact form
- Meta/Google Ads lead forms
- Direct API submission
"""

from __future__ import annotations

import asyncio
import json
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, Optional

import structlog
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from channels.google_forms import GoogleFormsHandler
from channels.linkedin import LinkedInChannelHandler
from channels.telegram import TelegramHandler
from channels.website import WebsiteChannelHandler
from channels.whatsapp import WhatsAppChannelHandler
from config import Settings, get_settings
from employee.autonomous_agent import AutonomousEmployee
from employee.memory import init_db
from employee.scheduler import SmartfieldScheduler
from models.lead import LeadCreate, LeadSource, ProcessedLead
from notifications.whatsapp import WhatsAppNotifier
from processors.pipeline import LeadPipeline, create_pipeline_from_config
from processors.followup_engine import FollowUpEngine
from processors.proposal_generator import create_and_save_proposal, generate_proposal_text
from processors.meeting_booking import MeetingBookingManager
from processors.sla_monitor import SLAMonitor
from processors.cpq_engine import CPQEngine
from processors.content_engine import ContentEngine
from processors.customer_success import CustomerSuccessEngine
from processors.learning_loop import LearningLoop
from processors.outbound_sender import OutboundSender
from dashboard.revenue_dashboard import get_dashboard_data, render_dashboard_html

logger = structlog.get_logger(__name__)

# ── Application state (initialized in lifespan) ────────────────────────────────
_pipeline: Optional[LeadPipeline] = None
_wa_handler: Optional[WhatsAppChannelHandler] = None
_wa_notifier = None  # WhatsAppNotifier — used to reply back to senders
_proposal_manager = None  # ProposalApprovalManager
_li_handler: Optional[LinkedInChannelHandler] = None
_gf_handler: Optional[GoogleFormsHandler] = None
_ws_handler: Optional[WebsiteChannelHandler] = None
_tg_handler: Optional[TelegramHandler] = None
_employee: Optional[AutonomousEmployee] = None
_scheduler: Optional[SmartfieldScheduler] = None
_followup_engine: Optional[FollowUpEngine] = None
_booking_manager: Optional[MeetingBookingManager] = None
_sla_monitor: Optional[SLAMonitor] = None
_cpq_engine: Optional[CPQEngine] = None
_content_engine: Optional[ContentEngine] = None
_cs_engine: Optional[CustomerSuccessEngine] = None
_learning_loop: Optional[LearningLoop] = None
_outbound_sender: Optional[OutboundSender] = None

# Deduplication: bounded OrderedDict — O(1) insert + O(1) eviction of oldest
_processed_wa_ids: OrderedDict[str, None] = OrderedDict()
_WA_DEDUP_MAX = 500

# In-memory lead cache with timestamps for TTL eviction (24h)
_processed_leads: dict[str, tuple[ProcessedLead, datetime]] = {}
_LEAD_CACHE_TTL_H = 24


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize all components on startup."""
    global _pipeline, _wa_handler, _wa_notifier, _li_handler, _gf_handler, _ws_handler, _tg_handler, _employee, _scheduler, _followup_engine, _booking_manager, _sla_monitor, _proposal_manager, _cpq_engine, _content_engine, _cs_engine, _learning_loop, _outbound_sender

    settings = get_settings()

    log = logger.bind(component="lifespan")
    log.info("Initializing Smartfield Lead Agent system")

    # Initialize persistent memory DB
    init_db()

    _pipeline = create_pipeline_from_config(settings)

    _wa_handler = WhatsAppChannelHandler()
    from notifications.whatsapp import WhatsAppNotifier
    _wa_notifier = WhatsAppNotifier(settings)
    _li_handler = LinkedInChannelHandler(
        client_id=settings.LINKEDIN_CLIENT_ID,
        client_secret=settings.LINKEDIN_CLIENT_SECRET,
    )
    _gf_handler = GoogleFormsHandler()
    _ws_handler = WebsiteChannelHandler()

    # Initialize Telegram handler
    if settings.is_telegram_configured():
        _tg_handler = TelegramHandler(settings.TELEGRAM_BOT_TOKEN)
        log.info("Telegram bot initialized")
    else:
        log.info("Telegram not configured (TELEGRAM_BOT_TOKEN missing)")

    # Initialize autonomous employee — use same notifier as pipeline
    _employee = AutonomousEmployee(
        config=settings,
        pipeline=_pipeline,
        crm=_pipeline.primary_crm,
        notifier=_pipeline.notifier,
        telegram=_tg_handler,
    )

    # Initialize follow-up engine and meeting booking manager
    _followup_engine = FollowUpEngine(settings)
    _booking_manager = MeetingBookingManager(settings, _pipeline.notifier)

    # Initialize SLA monitor (uses Supabase for persistence if available)
    _supabase_client = None
    if settings.is_supabase_configured():
        try:
            from supabase import create_client
            _supabase_client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        except Exception:
            pass
    _sla_monitor = SLAMonitor(notifier=_pipeline.notifier, supabase_client=_supabase_client)
    log.info("SLA monitor initialized")

    # Initialize Proposal Approval Manager (needs owner chat IDs to send approvals)
    if settings.has_telegram_owners() and _tg_handler:
        from processors.proposal_generator import ProposalApprovalManager
        _proposal_manager = ProposalApprovalManager(
            anthropic_key=settings.ANTHROPIC_API_KEY,
            telegram_handler=_tg_handler,
            wa_notifier=_wa_notifier,
            owner_chat_ids=[str(c) for c in (settings.TELEGRAM_OWNER_CHAT_IDS or [])],
        )
        log.info("Proposal approval manager initialized")

    # Initialize RevOS v6 engines
    _cpq_engine = CPQEngine()
    log.info("CPQ engine initialized")

    _content_engine = ContentEngine(anthropic_api_key=settings.ANTHROPIC_API_KEY)
    log.info("Content engine initialized")

    if settings.is_supabase_configured():
        try:
            from supabase import create_client as _create_sb
            _sb = _create_sb(settings.SUPABASE_URL, settings.SUPABASE_KEY)
            _cs_engine = CustomerSuccessEngine(supabase_client=_sb, notifier=_pipeline.notifier)
            _learning_loop = LearningLoop(
                supabase_client=_sb,
                anthropic_api_key=settings.ANTHROPIC_API_KEY,
                notifier=_pipeline.notifier,
            )
            _outbound_sender = OutboundSender(
                crm=_pipeline.primary_crm,
                notifier=_pipeline.notifier,
                anthropic_api_key=settings.ANTHROPIC_API_KEY,
            )
            log.info("Customer Success + Learning Loop + Outbound Sender initialized")
        except Exception as exc:
            log.warning("RevOS v6 engines init partial", error=str(exc))

    # Start the autonomous scheduler (daily reports, follow-ups, etc.)
    _scheduler = SmartfieldScheduler(
        _employee,
        pipeline=_pipeline,
        sla_monitor=_sla_monitor,
        cs_engine=_cs_engine,
        learning_loop=_learning_loop,
        content_engine=_content_engine,
        outbound_sender=_outbound_sender,
    )
    _scheduler.start()

    log.info(
        "System initialized",
        primary_crm=settings.PRIMARY_CRM,
        sales_team_count=len(settings.SALES_TEAM_WHATSAPP),
        twilio_configured=settings.is_twilio_configured(),
        autonomous_employee="active",
    )

    yield

    if _scheduler:
        _scheduler.stop()
    log.info("Shutting down Smartfield Lead Agent system")


# ── FastAPI App ────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Smartfield Lead Generation API",
    description="AI-powered lead generation agent for Smartfield refrigerated transport",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Dependency injectors ───────────────────────────────────────────────────────

def get_pipeline() -> LeadPipeline:
    if _pipeline is None:
        raise HTTPException(status_code=503, detail="Pipeline not initialized")
    return _pipeline


def get_settings_dep() -> Settings:
    return get_settings()


# ── Pydantic request/response models for the direct API ───────────────────────

class DirectLeadRequest(BaseModel):
    """Request body for the direct API lead submission endpoint."""
    name: str = Field(..., min_length=2)
    company: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    cargo_type: Optional[str] = None
    route_from: Optional[str] = None
    route_to: Optional[str] = None
    fleet_size_needed: Optional[int] = Field(None, ge=1)
    budget_monthly: Optional[float] = Field(None, ge=0)
    notes: Optional[str] = None
    source: str = "manual"


class LeadResponse(BaseModel):
    """Standard response for processed leads."""
    lead_id: str
    crm_id: Optional[str]
    name: str
    score: int
    priority: str
    category: str
    status: str
    crm_saved: bool
    notification_sent: bool
    next_actions: list[str]
    classification_reasoning: str
    processing_time_ms: Optional[float]


def _processed_to_response(processed: ProcessedLead) -> LeadResponse:
    """Convert ProcessedLead to API response."""
    lead = processed.lead
    return LeadResponse(
        lead_id=lead.id,
        crm_id=lead.crm_id,
        name=lead.name,
        score=lead.score,
        priority=str(lead.priority),
        category=str(lead.category),
        status=str(lead.status),
        crm_saved=processed.crm_saved,
        notification_sent=processed.notification_sent,
        next_actions=processed.next_actions,
        classification_reasoning=processed.classification_reasoning,
        processing_time_ms=processed.processing_time_ms,
    )


async def _run_pipeline(lead_create: LeadCreate, pipeline: LeadPipeline) -> ProcessedLead:
    """Execute pipeline and store result for later retrieval."""
    processed = await pipeline.process(lead_create)
    # Store with timestamp for TTL eviction; clean stale entries while here
    now = datetime.utcnow()
    cutoff = now - timedelta(hours=_LEAD_CACHE_TTL_H)
    stale = [k for k, (_, ts) in _processed_leads.items() if ts < cutoff]
    for k in stale:
        del _processed_leads[k]
    _processed_leads[str(processed.lead.id)] = (processed, now)

    # Register with SLA monitor so response time is tracked
    if _sla_monitor:
        _sla_monitor.register_lead(
            lead_id=processed.lead.id,
            name=processed.lead.name,
            phone=processed.lead.phone or "",
            priority=str(processed.lead.priority),
            crm_id=processed.lead.crm_id or "",
        )

    return processed


# ── Health check ───────────────────────────────────────────────────────────────

@app.get("/health", tags=["System"])
async def health_check(settings: Settings = Depends(get_settings_dep)) -> dict:
    """System health check endpoint."""
    return {
        "status": "healthy",
        "service": "Smartfield Lead Generation Agent",
        "version": "1.0.0",
        "crm": settings.PRIMARY_CRM,
        "twilio_configured": settings.is_twilio_configured(),
        "hubspot_configured": settings.is_hubspot_configured(),
        "airtable_configured": settings.is_airtable_configured(),
    }


# ── Direct API endpoints ────────────────────────────────────────────────────────

@app.post("/api/lead", tags=["Leads"], response_model=LeadResponse)
async def submit_lead_api(
    body: DirectLeadRequest,
    pipeline: LeadPipeline = Depends(get_pipeline),
) -> LeadResponse:
    """
    Submit a lead directly via JSON API.
    إرسال عميل محتمل مباشرةً عبر API.
    """
    log = logger.bind(endpoint="/api/lead", name=body.name)

    try:
        source = LeadSource(body.source.lower())
    except ValueError:
        source = LeadSource.MANUAL

    try:
        lead_create = LeadCreate(
            name=body.name,
            company=body.company,
            phone=body.phone,
            email=body.email,
            source=source,
            cargo_type=body.cargo_type,
            route_from=body.route_from,
            route_to=body.route_to,
            fleet_size_needed=body.fleet_size_needed,
            budget_monthly=body.budget_monthly,
            notes=body.notes,
            raw_data={"api_submission": True},
        )
    except Exception as exc:
        log.warning("Invalid lead data", error=str(exc))
        raise HTTPException(status_code=422, detail=str(exc))

    log.info("Processing direct API lead")
    processed = await _run_pipeline(lead_create, pipeline)
    return _processed_to_response(processed)


@app.get("/api/lead/{lead_id}", tags=["Leads"])
async def get_lead_status(lead_id: str) -> dict:
    """
    Retrieve the processing status of a previously submitted lead.
    استرداد حالة معالجة عميل تم إرساله مسبقاً.
    """
    if lead_id not in _processed_leads:
        raise HTTPException(status_code=404, detail="Lead not found")

    processed, _ = _processed_leads[lead_id]
    return _processed_to_response(processed).model_dump()


@app.get("/api/analytics", tags=["Analytics"])
async def get_analytics(
    pipeline: LeadPipeline = Depends(get_pipeline),
) -> dict:
    """Get pipeline analytics from the configured CRM."""
    try:
        stats = await pipeline.primary_crm.get_pipeline_stats()
        return {"success": True, "stats": stats}
    except Exception as exc:
        logger.error("Failed to get analytics", error=str(exc))
        return {"success": False, "error": str(exc), "stats": {}}


@app.get("/dashboard", tags=["Dashboard"], response_class=Response)
async def revenue_dashboard() -> Response:
    """
    Live Revenue Dashboard — لوحة الإيرادات الحية.
    Auto-refreshes every 2 minutes.
    """
    try:
        from crm.supabase_crm import SupabaseCRM
        settings = get_settings()
        if settings.is_supabase_configured():
            from supabase import create_client
            supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
            data = await get_dashboard_data(supabase)
        else:
            data = {
                "total": 0, "today": 0, "week": 0, "month": 0,
                "avg_score": 0, "avg_budget": 0, "pipeline_value": 0,
                "by_status": {}, "by_priority": {}, "by_source": {},
                "by_category": {}, "top_leads": [],
                "generated_at": datetime.utcnow().isoformat(),
            }
        html = render_dashboard_html(data)
        return Response(content=html, media_type="text/html; charset=utf-8")
    except Exception as exc:
        logger.error("Dashboard render failed", error=str(exc))
        return Response(content=f"<h1>Dashboard Error</h1><pre>{exc}</pre>", media_type="text/html")


class ProposalRequest(BaseModel):
    lead_id: Optional[str] = None
    name: str
    company: Optional[str] = None
    phone: Optional[str] = None
    cargo_type: Optional[str] = None
    fleet_size_needed: Optional[int] = None
    budget_monthly: Optional[float] = None
    route_from: Optional[str] = None
    route_to: Optional[str] = None
    score: int = 50


@app.post("/api/proposal", tags=["Sales"])
async def generate_proposal(
    req: ProposalRequest,
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    """
    Generate a proposal draft and send to owner for Telegram approval.
    If no Telegram configured, returns the text directly.
    """
    try:
        lead_dict = req.model_dump()

        # Use approval flow if available
        if _proposal_manager:
            proposal_id = await _proposal_manager.request_approval(lead_dict)
            return {
                "success": True,
                "status": "pending_approval",
                "proposal_id": proposal_id,
                "message": "العرض أُرسل للمالك عبر تيليغرام للموافقة",
                "lead_name": req.name,
            }
        else:
            # Direct generation (no approval flow)
            proposal_text = await generate_proposal_text(lead_dict, settings.ANTHROPIC_API_KEY)
            file_path = await create_and_save_proposal(lead_dict, settings.ANTHROPIC_API_KEY)
            return {
                "success": True,
                "status": "generated",
                "proposal_text": proposal_text,
                "pdf_path": file_path,
                "lead_name": req.name,
            }
    except Exception as exc:
        logger.error("Proposal generation failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


class BookingRequest(BaseModel):
    lead_phone: str
    lead_name: str


@app.post("/api/book-meeting", tags=["Sales"])
async def book_meeting(req: BookingRequest) -> dict:
    """
    Initiate meeting booking for a lead via WhatsApp.
    يبدأ عملية حجز اجتماع للعميل عبر واتساب.
    """
    if not _booking_manager:
        raise HTTPException(status_code=503, detail="Booking manager not initialized")
    success = await _booking_manager.initiate_booking(req.lead_phone, req.lead_name)
    return {"success": success, "message": "Booking invitation sent" if success else "Failed to send"}


@app.post("/api/lead/{lead_id}/responded", tags=["Leads"])
async def mark_lead_responded(lead_id: str) -> dict:
    """
    Mark a lead as responded (resets SLA timer).
    يُعلّم العميل باعتباره تم التواصل معه — يوقف مؤقت SLA.
    """
    if not _sla_monitor:
        raise HTTPException(status_code=503, detail="SLA monitor not initialized")
    response_time = _sla_monitor.mark_responded(lead_id)
    if response_time is None:
        raise HTTPException(status_code=404, detail="Lead not tracked by SLA monitor")
    return {"lead_id": lead_id, "response_time_minutes": response_time}


@app.get("/api/sla/stats", tags=["Analytics"])
async def get_sla_stats() -> dict:
    """Return SLA compliance statistics."""
    if not _sla_monitor:
        return {"error": "SLA monitor not initialized"}
    return _sla_monitor.get_sla_stats()


@app.post("/api/lead/{lead_id}/stage", tags=["Leads"])
async def update_lead_stage(lead_id: str, body: dict) -> dict:
    """
    Move a lead to a new deal pipeline stage.
    ينقل العميل إلى مرحلة جديدة في خط الصفقات.
    """
    new_stage = body.get("stage")
    if not new_stage:
        raise HTTPException(status_code=400, detail="stage field required")

    pipeline = _pipeline
    if not pipeline:
        raise HTTPException(status_code=503, detail="Pipeline not initialized")

    # Only SupabaseCRM has update_deal_stage
    from crm.supabase_crm import SupabaseCRM
    if isinstance(pipeline.primary_crm, SupabaseCRM):
        ok = await pipeline.primary_crm.update_deal_stage(
            lead_id=lead_id,
            new_stage=new_stage,
            changed_by=body.get("changed_by", "api"),
            notes=body.get("notes", ""),
        )
        return {"success": ok, "lead_id": lead_id, "new_stage": new_stage}

    return {"success": False, "error": "Primary CRM does not support deal stages"}


class FollowUpRequest(BaseModel):
    lead_phone: str
    lead_name: str
    priority: str = "medium"


# ── CPQ Engine Endpoints (Layer 6) ────────────────────────────────────────────

class CPQRequest(BaseModel):
    route_from: str = Field(..., description="Origin city")
    route_to: str = Field(..., description="Destination city")
    vehicle_type: str = Field(default="medium_truck", description="small_van/medium_truck/large_truck/reefer_trailer")
    temperature_zone: str = Field(default="chilled", description="chilled/frozen/pharma")
    frequency_per_month: int = Field(default=1, ge=1, description="Trips per month")
    urgency: str = Field(default="normal", description="normal/express/urgent")
    distance_km: Optional[int] = Field(None, description="Override distance in km")
    weight_kg: Optional[float] = Field(None, description="Cargo weight in kg")
    volume_m3: Optional[float] = Field(None, description="Cargo volume in m3")


@app.post("/api/cpq/quote", tags=["CPQ"])
async def generate_cpq_quote(req: CPQRequest) -> dict:
    """
    Generate an instant CPQ quote for a transport request.
    توليد عرض سعر فوري ودقيق.
    """
    if not _cpq_engine:
        raise HTTPException(status_code=503, detail="CPQ engine not initialized")
    try:
        quote = _cpq_engine.calculate(
            route_from=req.route_from,
            route_to=req.route_to,
            vehicle_type=req.vehicle_type,
            temperature_zone=req.temperature_zone,
            frequency_per_month=req.frequency_per_month,
            urgency=req.urgency,
            distance_km=req.distance_km,
            weight_kg=req.weight_kg,
            volume_m3=req.volume_m3,
        )
        return {"success": True, "quote": _cpq_engine.to_dict(quote)}
    except Exception as exc:
        logger.error("CPQ quote failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/cpq/vehicles", tags=["CPQ"])
async def list_vehicles() -> dict:
    """List available vehicle types and their specs."""
    from processors.cpq_engine import VEHICLE_CONFIG, TEMP_PREMIUMS
    return {"vehicles": VEHICLE_CONFIG, "temperature_zones": TEMP_PREMIUMS}


# ── Content Engine Endpoints (Layer 7) ────────────────────────────────────────

class ContentRequest(BaseModel):
    content_type: str = Field(default="insight", description="insight/win/objection/stats/cold_chain")
    data: Optional[dict] = None


@app.post("/api/content/generate", tags=["Content"])
async def generate_content(req: ContentRequest) -> dict:
    """
    Generate marketing content mapped to revenue impact.
    توليد محتوى تسويقي مرتبط بأهداف الإيراد.
    """
    if not _content_engine:
        raise HTTPException(status_code=503, detail="Content engine not initialized")
    try:
        post = await _content_engine.generate_linkedin_post(
            content_type=req.content_type, data=req.data
        )
        return {
            "success": True,
            "content_type": req.content_type,
            "post": post,
            "platform": "linkedin",
            "generated_at": datetime.utcnow().isoformat(),
        }
    except Exception as exc:
        logger.error("Content generation failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/content/weekly-plan", tags=["Content"])
async def generate_weekly_content_plan(pipeline: LeadPipeline = Depends(get_pipeline)) -> dict:
    """Generate a full 5-post weekly LinkedIn content calendar."""
    if not _content_engine:
        raise HTTPException(status_code=503, detail="Content engine not initialized")
    try:
        stats = await pipeline.primary_crm.get_pipeline_stats()
        plan = await _content_engine.generate_weekly_content_plan(pipeline_data=stats)
        return {"success": True, "week_plan": plan, "total_posts": len(plan)}
    except Exception as exc:
        logger.error("Weekly plan generation failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/content/objection", tags=["Content"])
async def handle_objection(body: dict) -> dict:
    """Generate a sales script for handling a specific objection."""
    if not _content_engine:
        raise HTTPException(status_code=503, detail="Content engine not initialized")
    objection = body.get("objection", "")
    if not objection:
        raise HTTPException(status_code=400, detail="objection field required")
    script = await _content_engine.generate_objection_script(objection)
    return {"success": True, "script": script}


# ── Customer Success Endpoints (Layer 8) ──────────────────────────────────────

@app.get("/api/customer-success/opportunities", tags=["Customer Success"])
async def get_cs_opportunities() -> dict:
    """Get all customer success opportunities: renewals, upsells, churn risks, referrals."""
    if not _cs_engine:
        return {"success": False, "message": "Customer Success engine not initialized (Supabase required)"}
    try:
        opportunities = await _cs_engine.get_cs_opportunities()
        return {"success": True, "opportunities": opportunities}
    except Exception as exc:
        logger.error("CS opportunities fetch failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/customer-success/run-check", tags=["Customer Success"])
async def run_cs_check() -> dict:
    """Manually trigger the customer success daily check."""
    if not _cs_engine:
        raise HTTPException(status_code=503, detail="Customer Success engine not initialized")
    results = await _cs_engine.run_daily_check()
    return {"success": True, "results": results}


# ── Learning Loop Endpoints (Layer 10) ────────────────────────────────────────

@app.post("/api/insights/run-analysis", tags=["Intelligence"])
async def run_win_loss_analysis() -> dict:
    """
    Trigger a manual win/loss analysis and learning loop.
    تشغيل تحليل Win/Loss يدوياً.
    """
    if not _learning_loop:
        raise HTTPException(status_code=503, detail="Learning Loop not initialized (Supabase required)")
    try:
        report = await _learning_loop.run_weekly_analysis()
        return {"success": True, "report": report}
    except Exception as exc:
        logger.error("Win/loss analysis failed", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/insights/icp", tags=["Intelligence"])
async def get_icp_profile() -> dict:
    """Get the current Ideal Customer Profile derived from won deals."""
    if not _learning_loop:
        raise HTTPException(status_code=503, detail="Learning Loop not initialized")
    try:
        won_deals = await _learning_loop._fetch_leads_by_status("won")
        icp = _learning_loop._analyze_icp(won_deals)
        return {"success": True, "icp": icp, "based_on_deals": len(won_deals)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/followup/start", tags=["Sales"])
async def start_followup_sequence(req: FollowUpRequest) -> dict:
    """
    Start the WhatsApp follow-up sequence for a lead.
    يبدأ سلسلة المتابعة عبر واتساب.
    """
    if not _followup_engine:
        raise HTTPException(status_code=503, detail="Follow-up engine not initialized")
    await _followup_engine.start_sequence(req.lead_phone, req.lead_name, req.priority)
    return {"success": True, "message": f"Follow-up sequence started for {req.lead_name}"}


# ── WhatsApp Webhook ───────────────────────────────────────────────────────────

@app.get("/webhook/whatsapp", tags=["Webhooks"])
async def whatsapp_verify(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    settings: Settings = Depends(get_settings_dep),
) -> Response:
    """WhatsApp webhook verification endpoint (GET)."""
    if not _wa_handler:
        raise HTTPException(status_code=503, detail="WhatsApp handler not initialized")

    challenge = _wa_handler.verify_webhook(
        mode=hub_mode or "",
        token=hub_verify_token or "",
        challenge=hub_challenge or "",
        verify_token=settings.WHATSAPP_VERIFY_TOKEN,
    )

    if challenge:
        return Response(content=challenge, media_type="text/plain")

    raise HTTPException(status_code=403, detail="Webhook verification failed")


@app.post("/webhook/whatsapp", tags=["Webhooks"])
async def whatsapp_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    pipeline: LeadPipeline = Depends(get_pipeline),
) -> dict:
    """
    Receive WhatsApp Business Cloud API webhook events.
    - Conversational messages → routed to AutonomousEmployee (responds intelligently)
    - Structured form-like messages → routed to Lead pipeline
    استقبال أحداث واتساب: محادثات للوكيل المستقل، نماذج لخط المعالجة.
    """
    if not _wa_handler:
        raise HTTPException(status_code=503, detail="WhatsApp handler not initialized")

    payload = await request.json()
    log = logger.bind(endpoint="/webhook/whatsapp")

    # Extract raw message info for autonomous agent routing
    raw_message = _wa_handler.extract_raw_message(payload)

    if raw_message and _employee:
        phone = raw_message.get("phone", "")
        text = raw_message.get("text", "")
        chat_id = raw_message.get("waha_chat_id", phone)

        # Deduplicate: WAHA fires both "message" and "message.any" for the same message
        msg_id = (
            payload.get("payload", {}).get("id", "")
            or payload.get("id", "")
            or f"{phone}:{text[:40]}"
        )
        if msg_id and msg_id in _processed_wa_ids:
            log.debug("WhatsApp duplicate message ignored", msg_id=msg_id)
            return {"status": "duplicate"}
        if msg_id:
            _processed_wa_ids[msg_id] = None
            if len(_processed_wa_ids) > _WA_DEDUP_MAX:
                _processed_wa_ids.popitem(last=False)  # evict oldest, O(1)

        log.info("WhatsApp message received", phone=phone, length=len(text))
        background_tasks.add_task(_handle_whatsapp_conversation, phone, text, chat_id)
    else:
        log.debug("WhatsApp webhook received but no message extracted")

    return {"status": "received"}


async def _handle_whatsapp_conversation(phone: str, text: str, chat_id: str = None):
    """Route a WhatsApp message through the autonomous employee then reply via WhatsApp."""
    if not _employee:
        return
    reply_to = chat_id or phone
    try:
        response = await _employee.handle_incoming_whatsapp(phone, text)
        logger.info("employee.responded", phone=phone, preview=response[:60])
        if response and _wa_notifier:
            await _wa_notifier.send_custom_message(reply_to, response)
    except Exception as exc:
        logger.error("employee.conversation_failed", phone=phone, error=str(exc))


# ── Telegram Webhook ────────────────────────────────────────────────────────────

@app.post("/webhook/telegram", tags=["Webhooks"])
async def telegram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Receive Telegram Bot webhook updates.
    كل رسالة تيليغرام تأتي هنا - الوكيل يرد تلقائياً.
    """
    if not _tg_handler or not _employee:
        raise HTTPException(status_code=503, detail="Telegram not configured")

    payload = await request.json()
    msg = _tg_handler.extract_message(payload)

    if msg:
        background_tasks.add_task(
            _handle_telegram_message,
            msg["chat_id"],
            msg["name"],
            msg["text"],
        )

    return {"ok": True}


async def _handle_telegram_message(chat_id: str, name: str, text: str):
    """Process a Telegram message through the autonomous employee and reply."""
    if not _tg_handler or not _employee:
        return
    try:
        # Check if this is a proposal approval command first
        if _proposal_manager and any(
            text.strip().startswith(cmd)
            for cmd in ("موافق", "تعديل", "رفض")
        ):
            result = await _proposal_manager.handle_owner_reply(text.strip())
            if result:
                await _tg_handler.send_message(chat_id, result)
                return

        await _tg_handler.send_typing(chat_id)
        response = await _employee.handle_telegram_message(chat_id, name, text)
        await _tg_handler.send_message(chat_id, response)
        logger.info("employee.telegram_responded", chat_id=chat_id, preview=response[:60])
    except Exception as exc:
        logger.error("employee.telegram_failed", chat_id=chat_id, error=str(exc))
        await _tg_handler.send_message(chat_id, "عذراً، حدث خطأ. سنعود إليك قريباً. 🙏")


@app.post("/setup/telegram-webhook", tags=["System"])
async def setup_telegram_webhook(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    """
    Auto-register Telegram webhook URL with Telegram servers.
    استخدم هذا بعد النشر لربط البوت تلقائياً.
    POST مع body: {"base_url": "https://yourdomain.com"}
    """
    if not _tg_handler:
        raise HTTPException(status_code=503, detail="Telegram not configured")

    body = await request.json()
    base_url = body.get("base_url", "").rstrip("/")
    if not base_url:
        raise HTTPException(status_code=400, detail="base_url required")

    webhook_url = f"{base_url}/webhook/telegram"
    ok = await _tg_handler.set_webhook(webhook_url)
    return {"ok": ok, "webhook_url": webhook_url}


# ── LinkedIn Webhook ────────────────────────────────────────────────────────────

@app.post("/webhook/linkedin", tags=["Webhooks"])
async def linkedin_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    pipeline: LeadPipeline = Depends(get_pipeline),
) -> dict:
    """
    Receive LinkedIn Lead Gen Forms webhook events.
    استقبال أحداث webhook نماذج LinkedIn.
    """
    if not _li_handler:
        raise HTTPException(status_code=503, detail="LinkedIn handler not initialized")

    body = await request.body()
    signature = request.headers.get("x-li-signature", "")

    # Verify signature
    if not _li_handler.verify_signature(body, signature):
        logger.warning("LinkedIn webhook signature verification failed")
        raise HTTPException(status_code=403, detail="Invalid signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}")

    lead_create = _li_handler.parse_webhook(payload)
    if lead_create:
        logger.info("LinkedIn lead received", name=lead_create.name, company=lead_create.company)
        background_tasks.add_task(_run_pipeline, lead_create, pipeline)
    else:
        logger.debug("LinkedIn webhook received but no lead extracted")

    return {"status": "received"}


# ── Google Forms Webhook ────────────────────────────────────────────────────────

@app.post("/webhook/google-forms", tags=["Webhooks"])
async def google_forms_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    pipeline: LeadPipeline = Depends(get_pipeline),
) -> dict:
    """
    Receive Google Forms / Apps Script webhook events.
    استقبال أحداث webhook نماذج Google.
    """
    if not _gf_handler:
        raise HTTPException(status_code=503, detail="Google Forms handler not initialized")

    try:
        payload = await request.json()
    except Exception:
        # Try form-encoded
        form_data = await request.form()
        payload = dict(form_data)

    lead_create = _gf_handler.parse_webhook(payload)
    if lead_create:
        logger.info("Google Forms lead received", name=lead_create.name)
        background_tasks.add_task(_run_pipeline, lead_create, pipeline)
    else:
        logger.debug("Google Forms webhook received but no lead extracted")

    return {"status": "received"}


# ── Website Webhook ─────────────────────────────────────────────────────────────

@app.post("/webhook/website", tags=["Webhooks"])
async def website_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    pipeline: LeadPipeline = Depends(get_pipeline),
) -> dict:
    """
    Receive website contact form submissions.
    استقبال طلبات نموذج التواصل من الموقع الإلكتروني.
    """
    if not _ws_handler:
        raise HTTPException(status_code=503, detail="Website handler not initialized")

    content_type = request.headers.get("content-type", "")
    lead_create: Optional[LeadCreate] = None

    if "application/json" in content_type:
        payload = await request.json()
        lead_create = _ws_handler.parse_submission(payload)
    else:
        # Assume form-encoded
        form_data = await request.form()
        lead_create = _ws_handler.parse_form_encoded(dict(form_data))

    if lead_create:
        logger.info("Website form lead received", name=lead_create.name)
        background_tasks.add_task(_run_pipeline, lead_create, pipeline)
    else:
        logger.debug("Website webhook received but no lead extracted")

    return {"status": "received"}


# ── Meta/Google Ads Lead Forms Webhook ─────────────────────────────────────────

@app.post("/webhook/ads", tags=["Webhooks"])
async def ads_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    pipeline: LeadPipeline = Depends(get_pipeline),
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    """
    Receive Meta (Facebook/Instagram) or Google Ads lead form webhooks.
    استقبال عملاء من إعلانات Meta أو Google.
    """
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}")

    log = logger.bind(endpoint="/webhook/ads")

    # Detect Meta vs Google Ads format
    lead_create: Optional[LeadCreate] = None

    # Meta Ads lead forms format
    if "entry" in payload or "leadgen_id" in payload:
        lead_create = _parse_meta_ads_lead(payload)
    # Google Ads lead forms format
    elif "user_column_data" in payload or "google_key" in payload:
        lead_create = _parse_google_ads_lead(payload)
    else:
        # Generic ads format — try website parser
        if _ws_handler:
            lead_create = _ws_handler.parse_submission(payload)

    if lead_create:
        lead_create = lead_create.model_copy(update={"source": LeadSource.ADS})
        log.info("Ads lead received", name=lead_create.name)
        background_tasks.add_task(_run_pipeline, lead_create, pipeline)
    else:
        log.debug("Ads webhook received but no lead extracted")

    return {"status": "received"}


def _parse_meta_ads_lead(payload: dict[str, Any]) -> Optional[LeadCreate]:
    """Parse Meta Ads lead form webhook payload."""
    try:
        # Handle test event
        if payload.get("object") == "page" and "entry" in payload:
            entries = payload.get("entry", [])
            for entry in entries:
                changes = entry.get("changes", [])
                for change in changes:
                    value = change.get("value", {})
                    if value.get("ad_id"):
                        # Real lead form - typically need to fetch field data separately
                        field_data = value.get("field_data", [])
                        return _map_meta_field_data(field_data, value)

        # Direct payload with field_data
        if "field_data" in payload:
            return _map_meta_field_data(payload["field_data"], payload)

    except Exception as exc:
        logger.error("Failed to parse Meta Ads lead", error=str(exc))
    return None


def _map_meta_field_data(
    field_data: list[dict[str, Any]],
    meta: dict[str, Any],
) -> Optional[LeadCreate]:
    """Map Meta Ads field_data array to LeadCreate."""
    fields: dict[str, str] = {}
    for field in field_data:
        key = field.get("name", "").lower().replace(" ", "_")
        values = field.get("values", [])
        if values:
            fields[key] = str(values[0])

    name = fields.get("full_name") or fields.get("name") or fields.get("first_name", "")
    last = fields.get("last_name", "")
    if last and not name.endswith(last):
        name = f"{name} {last}".strip()

    email = fields.get("email")
    phone = fields.get("phone_number") or fields.get("phone")

    if not name or not (email or phone):
        return None

    return LeadCreate(
        name=name,
        company=fields.get("company_name") or fields.get("company"),
        phone=phone,
        email=email,
        source=LeadSource.ADS,
        cargo_type=fields.get("cargo_type"),
        notes=fields.get("message") or fields.get("notes"),
        raw_data={"meta_ads": meta, "field_data": field_data},
    )


def _parse_google_ads_lead(payload: dict[str, Any]) -> Optional[LeadCreate]:
    """Parse Google Ads lead form extension webhook payload."""
    try:
        columns = payload.get("user_column_data", [])
        fields: dict[str, str] = {}
        for col in columns:
            col_id = col.get("column_id", "").lower()
            value = col.get("string_value", "")
            if value:
                fields[col_id] = value

        name = fields.get("full_name") or fields.get("name", "Google Ads Lead")
        email = fields.get("email")
        phone = fields.get("phone_number") or fields.get("phone")

        if not (email or phone):
            return None

        return LeadCreate(
            name=name,
            company=fields.get("company_name"),
            phone=phone,
            email=email,
            source=LeadSource.ADS,
            raw_data={"google_ads": payload},
        )
    except Exception as exc:
        logger.error("Failed to parse Google Ads lead", error=str(exc))
    return None
