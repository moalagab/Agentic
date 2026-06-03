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
from contextlib import asynccontextmanager
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

logger = structlog.get_logger(__name__)

# ── Application state (initialized in lifespan) ────────────────────────────────
_pipeline: Optional[LeadPipeline] = None
_wa_handler: Optional[WhatsAppChannelHandler] = None
_li_handler: Optional[LinkedInChannelHandler] = None
_gf_handler: Optional[GoogleFormsHandler] = None
_ws_handler: Optional[WebsiteChannelHandler] = None
_tg_handler: Optional[TelegramHandler] = None
_employee: Optional[AutonomousEmployee] = None
_scheduler: Optional[SmartfieldScheduler] = None

# In-memory store for lead lookups by internal ID (replace with DB in production)
_processed_leads: dict[str, ProcessedLead] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize all components on startup."""
    global _pipeline, _wa_handler, _li_handler, _gf_handler, _ws_handler, _tg_handler, _employee, _scheduler

    settings = get_settings()

    log = logger.bind(component="lifespan")
    log.info("Initializing Smartfield Lead Agent system")

    # Initialize persistent memory DB
    init_db()

    _pipeline = create_pipeline_from_config(settings)

    _wa_handler = WhatsAppChannelHandler()
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

    # Initialize autonomous employee
    notifier = WhatsAppNotifier(settings)
    _employee = AutonomousEmployee(
        config=settings,
        pipeline=_pipeline,
        crm=_pipeline.primary_crm,
        notifier=notifier,
        telegram=_tg_handler,
    )

    # Start the autonomous scheduler (daily reports, follow-ups, etc.)
    _scheduler = SmartfieldScheduler(_employee)
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
    _processed_leads[processed.lead.id] = processed
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

    processed = _processed_leads[lead_id]
    return _processed_to_response(processed).model_dump()


@app.get("/api/analytics", tags=["Analytics"])
async def get_analytics(
    pipeline: LeadPipeline = Depends(get_pipeline),
) -> dict:
    """
    Get pipeline analytics from the configured CRM.
    استرداد تحليلات خط المعالجة من CRM.
    """
    try:
        stats = await pipeline.primary_crm.get_pipeline_stats()
        return {"success": True, "stats": stats}
    except Exception as exc:
        logger.error("Failed to get analytics", error=str(exc))
        return {"success": False, "error": str(exc), "stats": {}}


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
        log.info("WhatsApp message received", phone=phone, length=len(text))
        # Route ALL messages through the autonomous employee
        # It decides: owner command vs lead conversation vs structured form
        background_tasks.add_task(_handle_whatsapp_conversation, phone, text)
    else:
        log.debug("WhatsApp webhook received but no message extracted")

    return {"status": "received"}


async def _handle_whatsapp_conversation(phone: str, text: str):
    """Route a WhatsApp message through the autonomous employee."""
    if not _employee:
        return
    try:
        response = await _employee.handle_incoming_whatsapp(phone, text)
        logger.info("employee.responded", phone=phone, preview=response[:60])
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
