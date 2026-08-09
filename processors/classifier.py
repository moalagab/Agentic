"""
Rule-based lead pre-classifier for Smartfield Lead Generation System.
المصنف الأولي للعملاء المحتملين بناءً على قواعد محددة

Provides fast, offline scoring before the lead is sent to Claude.
This allows prioritization and sanity-checking without API calls.
"""

from __future__ import annotations

from typing import Any

import structlog

from models.lead import LeadCategory, LeadCreate, LeadPriority

logger = structlog.get_logger(__name__)

# Keywords that indicate food/pharma/industrial transport cargo
FOOD_KEYWORDS = {
    "لحم", "لحوم", "دجاج", "اسماك", "سمك", "مأكولات بحرية", "خضار", "خضروات",
    "فواكه", "فاكهة", "الألبان", "ألبان", "عصير", "مجمد", "meat", "chicken",
    "fish", "seafood", "vegetables", "fruits", "dairy", "frozen", "food",
    "مواد غذائية", "اغذية", "أغذية", "مطعم", "مطاعم", "restaurant",
}

PHARMA_KEYWORDS = {
    "دواء", "أدوية", "ادوية", "صيدلية", "صيدليات", "مستشفى", "مستشفيات",
    "طبي", "طبية", "علاج", "لقاح", "لقاحات", "pharma", "pharmaceutical",
    "medicine", "hospital", "clinic", "medical", "drug", "vaccine",
    "مستلزمات طبية", "تجهيزات طبية",
}

# Meal Run — healthy restaurants and meal/diet-subscription service
# providers, officially approved 2026-08-04 as a B2B segment. Customer type
# is any such business with more than one subscriber of its own (restaurant
# OR dedicated subscription brand — not narrower than that). More specific
# than FOOD_KEYWORDS on purpose so a genuine healthy-restaurant/diet brand is
# categorized MEAL_RUN instead of generic FOOD_TRANSPORT. Contract stays
# with that business only — see LeadCategory.MEAL_RUN.
MEAL_SUBSCRIPTION_KEYWORDS = {
    "دايت", "diet", "اشتراك وجبات", "meal subscription", "meal plan",
    "خطة غذائية", "خطة أكل", "سعرات", "calorie", "كيتو", "keto",
    "لوكارب", "low carb", "فيت فود", "fit food", "fitness meals",
    "healthy meals", "وجبات صحية", "meal delivery", "توصيل وجبات",
    "نظام غذائي", "تخسيس", "weight loss meals", "clean eating",
    "مطعم صحي", "healthy restaurant", "مطعم دايت", "diet restaurant",
    "مشتركين", "subscribers", "اشتراكات", "subscriptions",
}

INDUSTRIAL_KEYWORDS = {
    "كيماويات", "كيمياء", "بتروكيماويات", "صناعي", "مصنع", "chemical",
    "industrial", "petrochemical", "factory", "manufacturing",
}

RETAIL_KEYWORDS = {
    "هايبر", "سوبر ماركت", "تجزئة", "محل", "متجر", "سلسلة",
    "hypermarket", "supermarket", "retail", "chain", "store",
}

LOGISTICS_KEYWORDS = {
    "شحن", "لوجستيات", "توزيع", "نقل", "logistics", "shipping",
    "distribution", "transport", "freight", "cargo",
}

SAUDI_CITIES = {
    "الرياض", "جدة", "مكة", "المدينة", "الدمام", "الخبر", "الظهران",
    "أبها", "تبوك", "القصيم", "بريدة", "حائل", "نجران", "جيزان",
    "ينبع", "الجبيل", "الطائف", "riyadh", "jeddah", "mecca", "medina",
    "dammam", "khobar", "dhahran", "abha", "tabuk", "jubail",
}


class LeadClassifier:
    """
    Fast rule-based pre-classifier that scores leads without API calls.
    Assigns preliminary category and score before Claude sees the lead.
    """

    def __init__(self) -> None:
        self._log = logger.bind(component="LeadClassifier")

    def pre_classify(self, lead_create: LeadCreate) -> dict[str, Any]:
        """
        Perform rule-based pre-classification on incoming lead data.
        يجري تصنيفاً أولياً للعميل بناءً على القواعد.

        Scoring breakdown:
        - Has company name:          +10 points
        - Has email:                 +10 points
        - Cargo type is food:        +20 points (pharma excluded 2026-08-09 — not an active ICP segment)
        - Fleet size > 5:            +15 points
        - Has budget:                +20 points
        - Route is domestic Saudi:   +10 points

        Returns:
            dict with keys: score (int), category (str), confidence (str),
                            reasons (list of str)
        """
        score = 0
        reasons: list[str] = []
        all_text = self._collect_text(lead_create)

        # ── Contact completeness ──────────────────────────────────────────────
        if lead_create.company:
            score += 10
            reasons.append("لديه اسم شركة (+10)")

        if lead_create.email:
            score += 10
            reasons.append("لديه بريد إلكتروني (+10)")

        # ── Cargo type analysis ───────────────────────────────────────────────
        # PHARMA_TRANSPORT is still detected as a category (useful for
        # routing/decline messaging) but no longer scores as a priority
        # cargo type — pharma is not an active ICP segment (excluded
        # 2026-08-09, see processors/icp_engine.py).
        category = self._detect_category(all_text, lead_create.cargo_type)
        if category == LeadCategory.MEAL_RUN:
            # Officially approved 2026-08-04 B2B segment — most inbound
            # requests are this type currently. Scored like FOOD_TRANSPORT;
            # quoting still has to go through the route-based Meal Run
            # pricing, never the per-trip Van rate (see agent/prompts.py).
            score += 20
            reasons.append(f"Meal Run — قطاع معتمد رسميًا: {category} (+20)")
        elif category == LeadCategory.FOOD_TRANSPORT:
            score += 20
            reasons.append(f"نوع بضاعة ذو أولوية عالية: {category} (+20)")
        elif category == LeadCategory.PHARMA_TRANSPORT:
            reasons.append(f"نوع بضاعة: {category} (قطاع غير مستهدف حاليًا، +0)")
        elif category == LeadCategory.INDUSTRIAL_COLD:
            score += 15
            reasons.append(f"نوع بضاعة صناعي: {category} (+15)")
        elif category == LeadCategory.RETAIL_CHAIN:
            score += 12
            reasons.append(f"سلسلة تجزئة: {category} (+12)")
        elif category == LeadCategory.LOGISTICS_COMPANY:
            score += 10
            reasons.append(f"شركة لوجستيات: {category} (+10)")

        # ── Fleet size ────────────────────────────────────────────────────────
        if lead_create.fleet_size_needed:
            if lead_create.fleet_size_needed > 10:
                score += 15
                reasons.append(f"أسطول كبير (>{lead_create.fleet_size_needed} شاحنات) (+15)")
            elif lead_create.fleet_size_needed >= 5:
                score += 10
                reasons.append(f"أسطول متوسط ({lead_create.fleet_size_needed} شاحنات) (+10)")
            else:
                score += 5
                reasons.append(f"أسطول صغير ({lead_create.fleet_size_needed} شاحنات) (+5)")

        # ── Budget ────────────────────────────────────────────────────────────
        if lead_create.budget_monthly:
            if lead_create.budget_monthly >= 50_000:
                score += 20
                reasons.append(f"ميزانية عالية (≥50,000 ريال) (+20)")
            elif lead_create.budget_monthly >= 20_000:
                score += 15
                reasons.append(f"ميزانية جيدة (≥20,000 ريال) (+15)")
            elif lead_create.budget_monthly >= 5_000:
                score += 10
                reasons.append(f"ميزانية متوسطة (≥5,000 ريال) (+10)")
            else:
                score += 5
                reasons.append(f"ميزانية محدودة (<5,000 ريال) (+5)")

        # ── Domestic Saudi route ──────────────────────────────────────────────
        route_text = " ".join(filter(None, [lead_create.route_from, lead_create.route_to]))
        if route_text and self._is_saudi_route(route_text):
            score += 10
            reasons.append("مسار داخل المملكة العربية السعودية (+10)")

        # ── Source bonus ──────────────────────────────────────────────────────
        source = str(lead_create.source).lower()
        if source in ("linkedin", "ads"):
            score += 5
            reasons.append(f"مصدر عالي الجودة: {source} (+5)")

        # Cap at 100
        score = min(100, score)

        # Determine priority
        if score >= 70:
            priority = "high"
        elif score >= 40:
            priority = "medium"
        else:
            priority = "low"

        result = {
            "score": score,
            "category": category.value if hasattr(category, "value") else str(category),
            "priority": priority,
            "confidence": "high" if score >= 60 else "medium" if score >= 30 else "low",
            "reasons": reasons,
        }

        self._log.debug(
            "Pre-classification complete",
            name=lead_create.name,
            score=score,
            category=result["category"],
            priority=priority,
        )

        return result

    def _collect_text(self, lead_create: LeadCreate) -> str:
        """Combine all text fields for keyword matching."""
        parts = [
            lead_create.name or "",
            lead_create.company or "",
            lead_create.cargo_type or "",
            lead_create.notes or "",
            lead_create.route_from or "",
            lead_create.route_to or "",
        ]
        # Include raw_data values if they're strings
        for v in lead_create.raw_data.values():
            if isinstance(v, str):
                parts.append(v)
        return " ".join(parts).lower()

    def _detect_category(
        self, text: str, cargo_type: str | None
    ) -> LeadCategory:
        """Detect lead category from text and cargo type."""
        search_text = text.lower()
        if cargo_type:
            search_text = f"{cargo_type.lower()} {search_text}"

        # Check keywords in priority order. PHARMA stays first regardless of
        # any other match — the legal exclusion (no SFDA carrier license)
        # takes precedence over a missed categorization. MEAL_SUBSCRIPTION
        # is checked before the generic FOOD_KEYWORDS so a genuine
        # diet/subscription brand doesn't fall into plain food_transport.
        if any(kw.lower() in search_text for kw in PHARMA_KEYWORDS):
            return LeadCategory.PHARMA_TRANSPORT

        if any(kw.lower() in search_text for kw in MEAL_SUBSCRIPTION_KEYWORDS):
            return LeadCategory.MEAL_RUN

        if any(kw.lower() in search_text for kw in FOOD_KEYWORDS):
            return LeadCategory.FOOD_TRANSPORT

        if any(kw.lower() in search_text for kw in RETAIL_KEYWORDS):
            return LeadCategory.RETAIL_CHAIN

        if any(kw.lower() in search_text for kw in INDUSTRIAL_KEYWORDS):
            return LeadCategory.INDUSTRIAL_COLD

        if any(kw.lower() in search_text for kw in LOGISTICS_KEYWORDS):
            return LeadCategory.LOGISTICS_COMPANY

        return LeadCategory.OTHER

    def _is_saudi_route(self, route_text: str) -> bool:
        """Check if route mentions Saudi cities."""
        text_lower = route_text.lower()
        return any(city.lower() in text_lower for city in SAUDI_CITIES)

    def get_score_breakdown(self, lead_create: LeadCreate) -> dict[str, Any]:
        """
        Return detailed score breakdown for debugging and transparency.
        يعيد تفاصيل التقييم للمراجعة والشفافية.
        """
        result = self.pre_classify(lead_create)
        return {
            "total_score": result["score"],
            "category": result["category"],
            "priority": result["priority"],
            "confidence": result["confidence"],
            "score_reasons": result["reasons"],
            "max_possible_score": 100,
            "scoring_breakdown": {
                "company_name": 10,
                "has_email": 10,
                "cargo_type_food": 20,
                "fleet_size_gt5": 15,
                "budget": 20,
                "saudi_route": 10,
                "quality_source": 5,
                "total": 90,
            },
        }
