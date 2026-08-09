"""
Buying Signals Engine
Detects market purchase-intent signals from lead data and enriches lead.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from models.lead import BuyingSignal, Lead


# Phrase lists for intent detection
_HIGH_INTENT_PHRASES = [
    "نحتاج", "نريد", "نبحث عن", "نطلب", "موردين",
    "need", "looking for", "require", "want", "supplier",
    "urgent", "عاجل", "سريع", "اسعار", "pricing", "quote",
    "عرض سعر", "متى تتوفر", "when available",
]

_EXPANSION_PHRASES = [
    "فرع جديد", "new branch", "expansion", "توسع", "opening soon",
    "سنفتح", "increasing", "نزيد", "grow", "نمو",
]

_DISSATISFIED_PHRASES = [
    "مشكلة", "problem", "شكوى", "complaint", "تأخر", "delayed",
    "مزود حالي", "current provider", "نبحث عن بديل", "switching",
    "disappointed", "خيبة أمل",
]


def _phrase_hits(text: str, phrases: list[str]) -> list[str]:
    text_lower = text.lower()
    return [p for p in phrases if p.lower() in text_lower]


def analyze_buying_signals(lead: Lead) -> list[str]:
    """
    Combine rule-based signals from lead fields + raw_data.
    Returns a deduplicated list of BuyingSignal values.
    """
    signals: set[str] = set(lead.buying_signals or [])
    raw: dict[str, Any] = lead.raw_data or {}

    rating = float(raw.get("rating") or raw.get("google_rating") or 0)
    review_count = int(raw.get("review_count") or raw.get("reviews_count") or 0)
    notes_text = " ".join([
        lead.notes or "",
        raw.get("description") or "",
        raw.get("notes") or "",
        raw.get("message") or "",
    ])

    # Rating signals
    if rating >= 4.5:
        signals.add(BuyingSignal.HIGH_RATING.value)

    # Review volume = established business
    if review_count >= 200:
        signals.add(BuyingSignal.HIGH_REVIEW_COUNT.value)
        signals.add(BuyingSignal.ACTIVE_ONLINE.value)
    elif review_count >= 50:
        signals.add(BuyingSignal.ACTIVE_ONLINE.value)

    # Intent phrases from notes / messages
    intent_hits = _phrase_hits(notes_text, _HIGH_INTENT_PHRASES)
    if intent_hits:
        signals.add("high_purchase_intent")

    expansion_hits = _phrase_hits(notes_text, _EXPANSION_PHRASES)
    if expansion_hits:
        signals.add(BuyingSignal.MULTIPLE_BRANCHES.value)

    dissatisfied_hits = _phrase_hits(notes_text, _DISSATISFIED_PHRASES)
    if dissatisfied_hits:
        signals.add("switching_intent")

    # Recently opened (within 6 months marker from raw)
    opened_date_str = raw.get("opened_date") or raw.get("date_opened")
    if opened_date_str:
        try:
            opened_date = datetime.fromisoformat(str(opened_date_str))
            if (datetime.utcnow() - opened_date) < timedelta(days=180):
                signals.add(BuyingSignal.RECENTLY_OPENED.value)
        except (ValueError, TypeError):
            pass

    # Budget mentioned
    if lead.budget_monthly and lead.budget_monthly > 0:
        signals.add("budget_confirmed")

    # Explicitly requested a quote
    if lead.status in ("quotation_requested", "quotation_sent"):
        signals.add("quote_requested")

    # Fleet size indicates seriousness
    if lead.fleet_size_needed and lead.fleet_size_needed >= 3:
        signals.add("large_fleet_need")

    return list(signals)


def signal_score(signals: list[str]) -> int:
    """
    Convert signals list to a 0-100 intent score.
    Higher = more likely to buy now.
    """
    weights = {
        BuyingSignal.HIGH_RATING.value:       5,
        BuyingSignal.HIGH_REVIEW_COUNT.value: 5,
        BuyingSignal.ACTIVE_ONLINE.value:     5,
        BuyingSignal.MULTIPLE_BRANCHES.value: 10,
        BuyingSignal.RECENTLY_OPENED.value:   15,
        BuyingSignal.PREMIUM_KEYWORDS.value:  10,
        # Pharma excluded as an ICP segment 2026-08-09 — the signal itself is
        # still detected (useful for auto-decline/redirect), but contributes
        # no score so pharma leads don't get prioritized ahead of real ICP fits.
        BuyingSignal.PHARMA_KEYWORDS.value:   0,
        BuyingSignal.FOOD_KEYWORDS.value:     8,
        "high_purchase_intent":               20,
        "switching_intent":                   15,
        "budget_confirmed":                   15,
        "quote_requested":                    20,
        "large_fleet_need":                   10,
    }
    total = sum(weights.get(s, 3) for s in signals)
    return min(total, 100)


def enrich_lead_with_signals(lead: Lead) -> Lead:
    """Mutates lead in-place: refreshes buying_signals list."""
    signals = analyze_buying_signals(lead)
    lead.buying_signals = signals
    lead.update_timestamp()
    return lead
