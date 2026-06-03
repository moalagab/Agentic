"""
Pydantic v2 models for Smartfield Lead Generation System.
نماذج البيانات لنظام توليد العملاء المحتملين - سمارت فيلد
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class LeadSource(str, Enum):
    """Source channel where the lead originated."""
    LINKEDIN = "linkedin"
    WEBSITE = "website"
    WHATSAPP = "whatsapp"
    GOOGLE_FORMS = "google_forms"
    ADS = "ads"
    MANUAL = "manual"


class LeadStatus(str, Enum):
    """Current status of the lead in the sales pipeline."""
    NEW = "new"
    CONTACTED = "contacted"
    QUALIFIED = "qualified"
    UNQUALIFIED = "unqualified"
    CONVERTED = "converted"
    LOST = "lost"


class LeadPriority(str, Enum):
    """Priority level for sales team follow-up."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class LeadCategory(str, Enum):
    """Business category of the lead."""
    FOOD_TRANSPORT = "food_transport"
    PHARMA_TRANSPORT = "pharma_transport"
    INDUSTRIAL_COLD = "industrial_cold"
    RETAIL_CHAIN = "retail_chain"
    LOGISTICS_COMPANY = "logistics_company"
    INDIVIDUAL = "individual"
    OTHER = "other"


class Lead(BaseModel):
    """
    Full lead model with all fields for CRM storage and processing.
    نموذج كامل للعميل المحتمل مع جميع الحقول للتخزين والمعالجة.
    """
    model_config = {"use_enum_values": True}

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = Field(..., description="Full name of the lead contact")
    company: Optional[str] = Field(None, description="Company or organization name")
    phone: Optional[str] = Field(None, description="Phone number with country code")
    email: Optional[str] = Field(None, description="Email address")

    source: LeadSource = Field(default=LeadSource.MANUAL, description="Channel where lead came from")
    status: LeadStatus = Field(default=LeadStatus.NEW, description="Current pipeline status")
    priority: LeadPriority = Field(default=LeadPriority.MEDIUM, description="Sales priority level")
    category: LeadCategory = Field(default=LeadCategory.OTHER, description="Business category")

    # Transport-specific fields
    cargo_type: Optional[str] = Field(None, description="Type of cargo requiring refrigeration")
    route_from: Optional[str] = Field(None, description="Origin city/region")
    route_to: Optional[str] = Field(None, description="Destination city/region")
    fleet_size_needed: Optional[int] = Field(None, ge=1, description="Number of trucks needed")
    budget_monthly: Optional[float] = Field(None, ge=0, description="Monthly budget in SAR")

    # Meta fields
    notes: Optional[str] = Field(None, description="Additional notes from agent or sales rep")
    raw_data: dict[str, Any] = Field(default_factory=dict, description="Original raw payload from channel")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # CRM & Assignment
    crm_id: Optional[str] = Field(None, description="ID assigned by the CRM system")
    assigned_to: Optional[str] = Field(None, description="Sales rep email or ID")

    # AI Scoring
    score: int = Field(default=0, ge=0, le=100, description="AI qualification score 0-100")
    follow_up_date: Optional[datetime] = Field(None, description="Scheduled follow-up date/time")

    # Opportunity Prediction
    probability_to_close: float = Field(default=0.0, ge=0.0, le=1.0, description="Estimated close probability 0-1")
    expected_monthly_revenue: float = Field(default=0.0, ge=0, description="Estimated monthly revenue in SAR")
    expected_trips_per_month: int = Field(default=0, ge=0, description="Estimated trips per month")
    estimated_ltv: float = Field(default=0.0, ge=0, description="Estimated lifetime value in SAR (3-year)")

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        # Remove spaces and dashes for consistency
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
    """
    Input model for creating a new lead from any channel.
    نموذج الإدخال لإنشاء عميل محتمل جديد من أي قناة.
    """
    model_config = {"use_enum_values": True}

    name: str = Field(..., min_length=2, description="Contact full name")
    company: Optional[str] = Field(None, description="Company name")
    phone: Optional[str] = Field(None, description="Phone number")
    email: Optional[str] = Field(None, description="Email address")

    source: LeadSource = Field(default=LeadSource.MANUAL)
    cargo_type: Optional[str] = Field(None, description="Type of goods to transport")
    route_from: Optional[str] = Field(None, description="Pickup location")
    route_to: Optional[str] = Field(None, description="Delivery location")
    fleet_size_needed: Optional[int] = Field(None, ge=1)
    budget_monthly: Optional[float] = Field(None, ge=0)
    notes: Optional[str] = Field(None)
    raw_data: dict[str, Any] = Field(default_factory=dict)

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
        """Convert LeadCreate to a full Lead instance."""
        return Lead(
            name=self.name,
            company=self.company,
            phone=self.phone,
            email=self.email,
            source=self.source,
            cargo_type=self.cargo_type,
            route_from=self.route_from,
            route_to=self.route_to,
            fleet_size_needed=self.fleet_size_needed,
            budget_monthly=self.budget_monthly,
            notes=self.notes,
            raw_data=self.raw_data,
        )


class ProcessedLead(BaseModel):
    """
    A fully processed lead with AI classification results and next actions.
    عميل محتمل تمت معالجته بالكامل مع نتائج التصنيف الذكي والإجراءات التالية.
    """
    model_config = {"use_enum_values": True}

    lead: Lead
    classification_reasoning: str = Field(
        ...,
        description="AI explanation of why this lead received the given score and category"
    )
    next_actions: list[str] = Field(
        default_factory=list,
        description="Ordered list of recommended actions for the sales team"
    )
    processing_time_ms: Optional[float] = Field(None, description="Total processing time in milliseconds")
    agent_tool_calls: list[str] = Field(
        default_factory=list,
        description="Names of tools called by the agent during processing"
    )
    crm_saved: bool = Field(default=False, description="Whether lead was successfully saved to CRM")
    notification_sent: bool = Field(default=False, description="Whether sales team was notified")

    @property
    def is_high_priority(self) -> bool:
        return self.lead.priority == LeadPriority.HIGH.value

    @property
    def summary_arabic(self) -> str:
        """Returns a short Arabic summary of the processed lead."""
        priority_labels = {
            LeadPriority.HIGH.value: "● عالية",
            LeadPriority.MEDIUM.value: "◑ متوسطة",
            LeadPriority.LOW.value: "○ منخفضة",
        }
        priority_str = priority_labels.get(self.lead.priority, "غير معروفة")
        revenue = f"{self.lead.expected_monthly_revenue:,.0f} ريال/شهر" if self.lead.expected_monthly_revenue else "غير محدد"
        prob = f"{self.lead.probability_to_close*100:.0f}%" if self.lead.probability_to_close else "—"
        return (
            f"*{self.lead.name}*"
            f"\nالشركة: {self.lead.company or 'غير محدد'}"
            f"\nالأولوية: {priority_str}"
            f"\nالتقييم: {self.lead.score}/100"
            f"\nإيراد متوقع: {revenue}  |  احتمال الإغلاق: {prob}"
        )
