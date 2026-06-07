"""
ICP Engine — Ideal Customer Profile Scoring
يصنف كل عميل على 4 شرائح ICP ويعطيه نقاط 0-100
"""

from __future__ import annotations

import re
from typing import Any

from models.lead import BuyingSignal, ICPSegment, Lead, LeadCategory


# Keywords per segment
_PREMIUM_FB_KEYWORDS = [
    "شوكولاتة", "chocolate", "كافيه", "cafe", "coffee", "قهوة",
    "specialty", "سبيشلتي", "cloud kitchen", "مطبخ سحابي",
    "catering", "كيتيرينج", "confectionery", "patisserie", "بيتسري",
    "حلويات فاخرة", "gourmet", "artisan", "premium", "فاخر",
    "dessert", "ice cream", "ايس كريم",
]

_PHARMA_BEAUTY_KEYWORDS = [
    "صيدلية", "pharmacy", "pharma", "دواء", "أدوية", "medicine",
    "مستشفى", "hospital", "clinic", "عيادة", "lab", "مختبر",
    "cosmetics", "مستحضرات", "beauty", "جمال", "skincare",
    "supplement", "مكملات", "medical", "طبي", "healthcare",
    "vaccine", "لقاح", "biologics",
]

_FRESH_FOOD_KEYWORDS = [
    "عضوي", "organic", "طازج", "fresh", "لحوم", "meat", "دجاج", "chicken",
    "seafood", "مأكولات بحرية", "سمك", "fish", "خضار", "vegetables",
    "fruits", "فواكه", "meal prep", "وجبات", "meal kit",
    "subscription", "اشتراك", "توصيل", "delivery", "بقالة", "grocery",
    "produce", "dairy", "ألبان",
]

_HORECA_KEYWORDS = [
    "فندق", "hotel", "مطعم", "restaurant", "مقهى", "café",
    "catering supplier", "distributor", "موزع", "supplier", "مورد",
    "hospitality", "ضيافة", "resort", "منتجع", "banquet", "بنكيت",
    "food service", "canteen", "كانتين",
]


def _text_hits(text: str, keywords: list[str]) -> int:
    text_lower = text.lower()
    return sum(1 for kw in keywords if kw.lower() in text_lower)


def _score_premium_fb(text: str, rating: float, review_count: int) -> int:
    score = 0
    hits = _text_hits(text, _PREMIUM_FB_KEYWORDS)
    score += min(hits * 15, 45)
    if rating >= 4.5:
        score += 20
    elif rating >= 4.0:
        score += 10
    if review_count >= 100:
        score += 15
    elif review_count >= 30:
        score += 8
    score += 20  # base: F&B is our primary target
    return min(score, 100)


def _score_pharma_beauty(text: str, category: str) -> int:
    score = 0
    hits = _text_hits(text, _PHARMA_BEAUTY_KEYWORDS)
    score += min(hits * 20, 60)
    if "pharma" in category.lower():
        score += 30
    return min(score, 100)


def _score_fresh_food(text: str, rating: float) -> int:
    score = 0
    hits = _text_hits(text, _FRESH_FOOD_KEYWORDS)
    score += min(hits * 15, 60)
    if rating >= 4.0:
        score += 15
    return min(score, 100)


def _score_horeca(text: str, review_count: int) -> int:
    score = 0
    hits = _text_hits(text, _HORECA_KEYWORDS)
    score += min(hits * 15, 60)
    if review_count >= 50:
        score += 20
    elif review_count >= 20:
        score += 10
    return min(score, 100)


def detect_buying_signals(lead: Lead) -> list[str]:
    """Extract buying signals from lead data."""
    signals: list[str] = []
    raw = lead.raw_data or {}

    rating = float(raw.get("rating") or raw.get("google_rating") or 0)
    review_count = int(raw.get("review_count") or raw.get("reviews_count") or 0)
    name = (lead.name or "") + " " + (lead.company or "") + " " + (raw.get("business_type") or "")

    if rating >= 4.5:
        signals.append(BuyingSignal.HIGH_RATING.value)
    if review_count >= 100:
        signals.append(BuyingSignal.HIGH_REVIEW_COUNT.value)
    if review_count >= 30:
        signals.append(BuyingSignal.ACTIVE_ONLINE.value)

    # Branch count heuristic
    branch_indicators = ["فروع", "branches", "chain", "سلسلة"]
    if any(kw in name.lower() for kw in branch_indicators):
        signals.append(BuyingSignal.MULTIPLE_BRANCHES.value)

    if _text_hits(name, _PREMIUM_FB_KEYWORDS) >= 2:
        signals.append(BuyingSignal.PREMIUM_KEYWORDS.value)
    if _text_hits(name, _PHARMA_BEAUTY_KEYWORDS) >= 1:
        signals.append(BuyingSignal.PHARMA_KEYWORDS.value)
    if _text_hits(name, _FRESH_FOOD_KEYWORDS) >= 1:
        signals.append(BuyingSignal.FOOD_KEYWORDS.value)

    # Recently opened — if raw_data has opened_recently flag
    if raw.get("recently_opened") or raw.get("opened_recently"):
        signals.append(BuyingSignal.RECENTLY_OPENED.value)

    return list(set(signals))


def score_lead_icp(lead: Lead) -> tuple[int, str]:
    """
    Returns (icp_score 0-100, icp_segment string).
    Evaluates against all 4 ICP profiles and picks the best match.
    """
    raw = lead.raw_data or {}
    rating = float(raw.get("rating") or raw.get("google_rating") or 0)
    review_count = int(raw.get("review_count") or raw.get("reviews_count") or 0)
    category = (lead.category or "other")

    search_text = " ".join([
        lead.name or "",
        lead.company or "",
        raw.get("business_type") or "",
        raw.get("description") or "",
        raw.get("gemini_category") or "",
        category,
    ])

    # pharma_beauty removed — not an active ICP segment for Smart Field
    scores = {
        ICPSegment.PREMIUM_FB: _score_premium_fb(search_text, rating, review_count),
        ICPSegment.FRESH_FOOD: _score_fresh_food(search_text, rating),
        ICPSegment.HORECA:     _score_horeca(search_text, review_count),
    }

    best_segment = max(scores, key=lambda s: scores[s])
    best_score = scores[best_segment]

    if best_score < 20:
        return (best_score, ICPSegment.NOT_ICP.value)

    return (best_score, best_segment.value)


def enrich_lead_with_icp(lead: Lead) -> Lead:
    """Mutates lead in-place with ICP score, segment, and buying signals."""
    icp_score, icp_segment = score_lead_icp(lead)
    signals = detect_buying_signals(lead)

    lead.icp_score = icp_score
    lead.icp_segment = icp_segment
    lead.buying_signals = signals
    lead.update_timestamp()
    return lead
