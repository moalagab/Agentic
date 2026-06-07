"""
Stage Tracker — تتبع مرحلة الصفقة تلقائياً
يحرك deal_stage بناءً على المحادثة والأفعال، بدون تدخل بشري.

Rules:
  first outbound message sent   → CONTACTED
  lead replies to us            → QUALIFIED (if score >= 40) else CONTACTED
  "اجتماع" / "موعد" keywords   → MEETING_BOOKED
  proposal generated/sent       → PROPOSAL_SENT
  price negotiation keywords    → NEGOTIATION
  win keywords                  → WON
  loss keywords                 → LOST
"""

from __future__ import annotations

import re
import logging
from typing import Optional

from models.lead import DealStage

logger = logging.getLogger(__name__)

# ── Keyword rules (Arabic + English) ──────────────────────────────────────────

_WIN_KEYWORDS = [
    "اتفقنا", "موافق", "تمام نبدأ", "نبدأ متى", "وين نوقع", "ابدا",
    "ابدأ", "تعاقدنا", "تم الاتفاق", "ارسل العقد", "confirmed",
    "let's go", "deal", "agreed", "نكمل", "جاهزين",
]

_LOSS_KEYWORDS = [
    "لا شكرا", "مش مهتم", "وجدنا بديل", "تعاقدنا مع غيركم", "لا نحتاج",
    "not interested", "found another", "no thanks", "cancel",
    "موقوف", "تم الإلغاء", "لا مجال الحين",
]

_MEETING_KEYWORDS = [
    "اجتماع", "موعد", "نلتقي", "زيارة", "نجي عندكم", "تجي عندنا",
    "meeting", "visit", "appointment", "schedule", "نحدد وقت",
    "اتصل فيني", "نتصل", "call",
]

_NEGOTIATION_KEYWORDS = [
    "السعر", "تخفيض", "خصم", "مرتفع", "ممكن أقل", "price",
    "discount", "reduce", "high price", "تفاوض", "عرض أفضل",
    "counter", "quote high", "غالي",
]

_PROPOSAL_KEYWORDS = [
    "عرض سعر", "offer", "proposal", "quotation", "عرض الأسعار",
    "كم تكلفة", "أرسل لي", "send me",
]


def _matches(text: str, keywords: list[str]) -> bool:
    t = text.lower()
    return any(kw.lower() in t for kw in keywords)


def infer_stage_from_message(
    message: str,
    role: str,           # "agent" | "lead"
    current_stage: str,
    lead_score: int = 0,
) -> Optional[str]:
    """
    Given a new message, return the new DealStage value if a transition is warranted,
    or None if the stage should stay the same.

    role="agent"  → message sent by us to the lead
    role="lead"   → message received from the lead
    """
    msg = message.strip()
    cs = current_stage.upper() if current_stage else "NEW_LEAD"

    # ── Win / Loss (highest priority — regardless of role) ─────────────────
    if _matches(msg, _WIN_KEYWORDS) and cs not in (DealStage.WON.value, DealStage.LOST.value):
        return DealStage.WON.value

    if _matches(msg, _LOSS_KEYWORDS) and cs not in (DealStage.WON.value, DealStage.LOST.value):
        return DealStage.LOST.value

    # ── Lead replied → at least CONTACTED ──────────────────────────────────
    if role == "lead":
        if cs in (DealStage.NEW_LEAD.value, "NEW_LEAD", "lead", "new"):
            if lead_score >= 50:
                return DealStage.QUALIFIED.value
            return DealStage.CONTACTED.value

        # Meeting mentioned by lead → MEETING_BOOKED
        if _matches(msg, _MEETING_KEYWORDS) and cs not in (
            DealStage.MEETING_BOOKED.value, DealStage.PROPOSAL_SENT.value,
            DealStage.NEGOTIATION.value
        ):
            return DealStage.MEETING_BOOKED.value

        # Negotiation signals
        if _matches(msg, _NEGOTIATION_KEYWORDS) and cs in (
            DealStage.PROPOSAL_SENT.value, DealStage.MEETING_BOOKED.value
        ):
            return DealStage.NEGOTIATION.value

    # ── Agent actions ───────────────────────────────────────────────────────
    if role == "agent":
        # Agent sent first message → CONTACTED
        if cs in (DealStage.NEW_LEAD.value, "NEW_LEAD", "lead", "new"):
            return DealStage.CONTACTED.value

        # Agent sent a proposal
        if _matches(msg, _PROPOSAL_KEYWORDS) and cs in (
            DealStage.CONTACTED.value, DealStage.QUALIFIED.value,
            DealStage.MEETING_BOOKED.value
        ):
            return DealStage.PROPOSAL_SENT.value

        # Agent booked meeting
        if _matches(msg, _MEETING_KEYWORDS) and cs in (
            DealStage.CONTACTED.value, DealStage.QUALIFIED.value
        ):
            return DealStage.MEETING_BOOKED.value

    return None  # no change


async def track_stage_on_message(
    lead_id: str,
    message: str,
    role: str,
    lead_score: int,
    current_stage: str,
    crm,
) -> Optional[str]:
    """
    Called whenever a message is sent or received for a lead.
    Updates deal_stage in CRM if a transition is detected.
    Returns the new stage string, or None if unchanged.
    """
    new_stage = infer_stage_from_message(message, role, current_stage, lead_score)
    if not new_stage or new_stage == current_stage:
        return None

    try:
        from crm.supabase_crm import SupabaseCRM
        if isinstance(crm, SupabaseCRM):
            ok = await crm.update_deal_stage(
                lead_id=lead_id,
                new_stage=new_stage,
                changed_by="auto_stage_tracker",
                notes=f"auto: {role} message → {new_stage}",
            )
            if ok:
                logger.info(
                    f"stage_tracker: {current_stage} → {new_stage} "
                    f"[lead={lead_id[:8]}]"
                )
                return new_stage
    except Exception as exc:
        logger.warning(f"stage_tracker.crm_update_failed: {exc}")

    return None


def stage_transition_label(from_stage: str, to_stage: str) -> str:
    """Human-readable Arabic label for a stage transition."""
    LABELS = {
        DealStage.NEW_LEAD.value:       "جديد",
        DealStage.QUALIFIED.value:      "مؤهَّل",
        DealStage.CONTACTED.value:      "تم التواصل",
        DealStage.MEETING_BOOKED.value: "اجتماع محدد",
        DealStage.PROPOSAL_SENT.value:  "عرض مرسل",
        DealStage.NEGOTIATION.value:    "تفاوض",
        DealStage.WON.value:            "✅ فاز",
        DealStage.LOST.value:           "❌ خسارة",
    }
    fr = LABELS.get(from_stage.upper(), from_stage)
    to = LABELS.get(to_stage.upper(), to_stage)
    return f"{fr} ← {to}"
