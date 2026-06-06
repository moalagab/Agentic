"""
Pydantic v2 models for Smartfield IROS — Revenue Operating System.
نماذج البيانات لنظام IROS — Smart Field النقل المبرد
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class LeadSource(str, Enum):
    LINKEDIN            = "linkedin"
    WEBSITE             = "website"
    WHATSAPP            = "whatsapp"
    GOOGLE_FORMS        = "google_forms"
    ADS                 = "ads"
    MANUAL              = "manual"
    SERPAPI_PROSPECTING = "serpapi_prospecting"
    REFERRAL            = "referral"


class DealStage(str, Enum):
    """Sales pipeline stages — مراحل pipeline المبيعات."""
    NEW_LEAD       = "NEW_LEAD"
    QUALIFIED      = "QUALIFIED"
    CONTACTED      = "CONTACTED"
    MEETING_BOOKED = "MEETING_BOOKED"
    PROPOSAL_SENT  = "PROPOSAL_SENT"
    NEGOTIATION    = "NEGOTIATION"
    WON            = "WON"
    LOST           = "LOST"


class LeadStatus(str, Enum):
    """Legacy status field — kept for backwards compat."""
    NEW                 = "new"
    CONTACTED           = "contacted"
    QUALIFIED           = "qualified"
    QUOTATION_REQUESTED = "quotation_requested"
    QUOTATION_SENT      = "quotation_sent"
    NEGOTIATION         = "negotiation"
    WON                 = "won"
    UNQUALIFIED         = "unqualified"
    LOST                = "lost"


class LeadPriority(str, Enum):
    HIGH   = "high"
    MEDIUM = "medium"
    LOW    = "low"


class LeadCategory(str, Enum):
    FOOD_TRANSPORT   = "food_transport"
    PHARMA_TRANSPORT = "pharma_transport"
    INDUSTRIAL_COLD  = "industrial_cold"
    RETAIL_CHAIN     = "retail_chain"
    LOGISTICS_COMPANY = "logistics_company"
    INDIVIDUAL       = "individual"
    OTHER            = "other"


class ICPSegment(str, Enum):
    """Ideal Customer Profile segments — شرائح العميل المثالي."""
    PREMIUM_FB      = "premium_fb"        # شوكولاتة، بن، مطابخ سحابية
    PHARMA_BEAUTY   = "pharma_beauty"     # أدوية، مستحضرات تجميل
    FRESH_FOOD      = "fresh_food"        # وجبات، لحوم، خضار عضوية
    HORECA          = "horeca"            # فنادق، مطاعم، مقاهي
    NOT_ICP         = "not_icp"          # خارج الـ ICP


class BuyingSignal(str, Enum):
    """Detected buying signals — إشارات الشراء."""
    HIGH_RATING         = "high_rating"          # تقييم عالٍ = عميل جودة
    MULTIPLE_BRANCHES   = "multiple_branches"    # فروع متعددة = حجم أكبر
    RECENTLY_OPENED     = "recently_opened"      # افتُتح حديثاً = يحتاج موردين
    PREMIUM_KEYWORDS    = "premium_keywords"     # كلمات فاخرة في الاسم
    PHARMA_KEYWORDS     = "pharma_keywords"      # كلمات طبية/صيدلانية
    FOOD_KEYWORDS       = "food_keywords"        # كلمات غذائية
    ACTIVE_ONLINE       = "active_online"        # نشط على الإنترنت
    HIGH_REVIEW_COUNT   = "high_review_count"    # عدد تقييمات عالٍ


class Lead(BaseModel):
    model_config = {"use_enum_values": True}

    id:      str           = Field(default_factory=lambda: str(uuid.uuid4()))
    name:    str           = Field(..., description="Full name / اسم العميل")
    company: Optional[str] = Field(None)
    phone:   Optional[str] = Field(None)
    email:   Optional[str] = Field(None)

    # Pipeline & Classification
    source:     LeadSource   = Field(default=LeadSource.MANUAL)
    status:     LeadStatus   = Field(default=LeadStatus.NEW)
    deal_stage: DealStage    = Field(default=DealStage.NEW_LEAD)
    priority:   LeadPriority = Field(default=LeadPriority.MEDIUM)
    category:   LeadCategory = Field(default=LeadCategory.OTHER)

    # ICP Engine
    icp_score:   int                = Field(default=0, ge=0, le=100, description="ICP match score 0-100")
    icp_segment: Optional[str]      = Field(None, description="ICP segment name")
    buying_signals: list[str]       = Field(default_factory=list)

    # Transport-specific
    cargo_type:         Optional[str]   = Field(None)
    route_from:         Optional[str]   = Field(None)
    route_to:           Optional[str]   = Field(None)
    fleet_size_needed:  Optional[int]   = Field(None, ge=1)
    budget_monthly:     Optional[float] = Field(None, ge=0)

    # Revenue Tracking
    expected_monthly_revenue:   float          = Field(default=0.0, ge=0)
    expected_trip_count:        int            = Field(default=0, ge=0)
    expected_close_probability: float          = Field(default=0.0, ge=0.0, le=1.0)
    actual_revenue:             float          = Field(default=0.0, ge=0)
    estimated_ltv:              float          = Field(default=0.0, ge=0)

    # CPQ
    quoted_price:       Optional[float]    = Field(None, ge=0)
    quote_valid_until:  Optional[datetime] = Field(None)
    vehicle_type:       Optional[str]      = Field(None)
    temperature_zone:   Optional[str]      = Field(None)
    frequency_per_month: Optional[int]     = Field(None, ge=1)

    # Customer Success
    won_date:               Optional[datetime] = Field(None)
    first_shipment_date:    Optional[datetime] = Field(None)
    last_shipment_date:     Optional[datetime] = Field(None)
    total_shipments:        int                = Field(default=0, ge=0)
    total_revenue_generated: float             = Field(default=0.0, ge=0)
    complaints_count:       int                = Field(default=0, ge=0)
    inactive_days:          int                = Field(default=0, ge=0)
    upsell_opportunity:     Optional[str]      = Field(None)
    cs_stage:               Optional[str]      = Field(None)  # onboarding/usage/renewal/expansion

    # Attribution
    attributed_source:  Optional[str] = Field(None)  # final attribution source

    # Meta
    score:          int            = Field(default=0, ge=0, le=100)
    notes:          Optional[str]  = Field(None)
    raw_data:       dict[str, Any] = Field(default_factory=dict)
    created_at:     datetime       = Field(default_factory=datetime.utcnow)
    updated_at:     datetime       = Field(default_factory=datetime.utcnow)
    crm_id:         Optional[str]  = Field(None)
    assigned_to:    Optional[str]  = Field(None)
    follow_up_date: Optional[datetime] = Field(None)
    referral_requested: bool       = Field(default=False)

    # Legacy compat
    probability_to_close:    float = Field(default=0.0, ge=0.0, le=1.0)
    expected_trips_per_month: int  = Field(default=0, ge=0)

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        cleaned = "".join(c for c in v if c.isdigit() or c == "+")
        return cleaned if cleaned else None

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return v.strip().lower()

    def update_timestamp(self) -> None:
        self.updated_at = datetime.utcnow()


class LeadCreate(BaseModel):
    model_config = {"use_enum_values": True}

    name:    str           = Field(..., min_length=2)
    company: Optional[str] = Field(None)
    phone:   Optional[str] = Field(None)
    email:   Optional[str] = Field(None)

    source:   LeadSource   = Field(default=LeadSource.MANUAL)
    category: LeadCategory = Field(default=LeadCategory.OTHER)
    priority: LeadPriority = Field(default=LeadPriority.MEDIUM)

    cargo_type:      Optional[str]   = Field(None)
    route_from:      Optional[str]   = Field(None)
    route_to:        Optional[str]   = Field(None)
    fleet_size_needed: Optional[int] = Field(None, ge=1)
    budget_monthly:  Optional[float] = Field(None, ge=0)
    notes:           Optional[str]   = Field(None)
    raw_data:        dict[str, Any]  = Field(default_factory=dict)

    score:                      int   = Field(default=0, ge=0, le=100)
    icp_score:                  int   = Field(default=0, ge=0, le=100)
    icp_segment:                Optional[str] = Field(None)
    buying_signals:             list[str] = Field(default_factory=list)
    expected_monthly_revenue:   float = Field(default=0.0, ge=0)
    expected_trip_count:        int   = Field(default=0, ge=0)
    expected_close_probability: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        cleaned = "".join(c for c in v if c.isdigit() or c == "+")
        return cleaned if cleaned else None

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return v.strip().lower()

    @model_validator(mode="after")
    def require_contact_info(self) -> "LeadCreate":
        if not self.phone and not self.email:
            raise ValueError("At least one of phone or email must be provided")
        return self

    def to_lead(self) -> Lead:
        return Lead(
            name=self.name, company=self.company, phone=self.phone,
            email=self.email, source=self.source, category=self.category,
            priority=self.priority, cargo_type=self.cargo_type,
            route_from=self.route_from, route_to=self.route_to,
            fleet_size_needed=self.fleet_size_needed,
            budget_monthly=self.budget_monthly, notes=self.notes,
            raw_data=self.raw_data, score=self.score,
            icp_score=self.icp_score, icp_segment=self.icp_segment,
            buying_signals=self.buying_signals,
            expected_monthly_revenue=self.expected_monthly_revenue,
            expected_trip_count=self.expected_trip_count,
            expected_close_probability=self.expected_close_probability,
        )


class ProcessedLead(BaseModel):
    model_config = {"use_enum_values": True}

    lead:                   Lead
    classification_reasoning: str        = Field(...)
    next_actions:           list[str]    = Field(default_factory=list)
    processing_time_ms:     Optional[float] = Field(None)
    agent_tool_calls:       list[str]    = Field(default_factory=list)
    crm_saved:              bool         = Field(default=False)
    notification_sent:      bool         = Field(default=False)

    @property
    def is_high_priority(self) -> bool:
        return self.lead.priority == LeadPriority.HIGH.value

    @property
    def summary_arabic(self) -> str:
        priority_labels = {
            LeadPriority.HIGH.value:   "● عالية",
            LeadPriority.MEDIUM.value: "◑ متوسطة",
            LeadPriority.LOW.value:    "○ منخفضة",
        }
        p = priority_labels.get(self.lead.priority, "—")
        rev = f"{self.lead.expected_monthly_revenue:,.0f} ريال/شهر" if self.lead.expected_monthly_revenue else "غير محدد"
        prob = f"{self.lead.expected_close_probability*100:.0f}%" if self.lead.expected_close_probability else "—"
        return (
            f"*{self.lead.name}*\n"
            f"ICP: {self.lead.icp_segment or '—'}  |  Score: {self.lead.score}/100  |  ICP: {self.lead.icp_score}/100\n"
            f"الأولوية: {p}\n"
            f"إيراد متوقع: {rev}  |  احتمال الإغلاق: {prob}"
        )
