"""
Hybrid Scoring Engine — محرك التقييم الهجين

Step 1: Rule-based scoring (deterministic, fast, auditable)
Step 2: Claude adds text analysis on top of the computed score

The rule-based score is LOCKED before Claude sees it.
Claude cannot change the numeric score — only adds qualitative reasoning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from models.lead import LeadCategory, LeadCreate

logger = structlog.get_logger(__name__)


@dataclass
class ScoreRule:
    name: str
    name_ar: str
    points: int
    matched: bool = False
    reason: str = ""


@dataclass
class HybridScoreResult:
    # Rule-based (locked, cannot be changed by Claude)
    rule_score: int
    rule_breakdown: list[ScoreRule]
    category: str
    priority: str

    # Claude adds (qualitative only)
    claude_analysis: str = ""
    risk_flags: list[str] = field(default_factory=list)
    next_best_action: str = ""

    # Derived
    estimated_monthly_revenue: float = 0.0
    estimated_trips: int = 0
    confidence_score: int = 0
    probability_to_close: float = 0.0
    expected_deal_value: float = 0.0

    @property
    def total_score(self) -> int:
        return self.rule_score

    @property
    def score_summary_ar(self) -> str:
        lines = [f"التقييم الإجمالي: {self.rule_score}/100", ""]
        for r in self.rule_breakdown:
            if r.matched:
                lines.append(f"✅ {r.name_ar}: +{r.points}")
            else:
                lines.append(f"⬜ {r.name_ar}: 0/{r.points}")
        return "\n".join(lines)


class HybridScorer:
    """
    Deterministic rule-based scoring system for Smart Field leads.

    Scoring table (total = 100 pts):
    ─────────────────────────────────────────────────────────
    Category / Business Type           │ Points
    ─────────────────────────────────────────────────────────
    مصنع غذاء / Food Factory           │ +20
    يحتاج تبريد / Needs Refrigeration  │ +15
    في الرياض أو مدن رئيسية            │ +10
    أكثر من 5 توصيلات شهرياً          │ +20
    طلب عاجل / Urgent Request          │ +15
    ─────────────────────────────────────────────────────────
    Budget tier                        │ +30 max
    Fleet size                         │ +25 max
    Contact completeness               │ +10 max
    ─────────────────────────────────────────────────────────
    Pharma excluded 2026-08-09 — legally prohibited for Smart Field (no SFDA
    carrier license), not just an unwanted segment (see processors/icp_engine.py).
    Mentioning pharma keywords no longer elevates a lead's business-type
    score or priority.

    Meal Run / diet-subscription added 2026-08-09 as "meal_subscription" —
    officially approved 2026-08-04 B2B segment (see company-profile.md).
    """

    # Business type scores
    BUSINESS_SCORES = {
        "meal_subscription": 23,  # Meal Run — officially approved B2B segment 2026-08-04, most inbound requests are this type
        "food_frozen": 22,      # Frozen food - high demand
        "food_fresh": 20,       # Fresh produce
        "food_general": 18,     # General food
        "retail_chain": 16,     # Supermarkets
        "cloud_kitchen": 15,    # Cloud kitchens
        "catering": 14,         # Catering companies
        "food_supplier": 13,    # B2B food suppliers
        "industrial": 12,       # Industrial cold
        "logistics": 10,        # Logistics partner
        "specialty": 10,        # Specialty food (chocolate, cheese)
        "online_food": 8,       # Online food stores
        "other": 5,
    }

    PHARMA_KW = {"دواء","أدوية","ادوية","صيدلية","مستشفى","طبي","طبية","لقاح","pharma","pharmaceutical","medicine","hospital","medical","drug","vaccine","مستلزمات طبية"}
    # Meal Run — healthy restaurants and meal/diet-subscription service
    # providers, officially approved 2026-08-04 B2B segment. Customer type is
    # any such business with more than one subscriber of its own (restaurant
    # OR dedicated subscription brand). Checked before FROZEN_KW/FOOD_KW in
    # _detect_business_type so a genuine healthy-restaurant/diet brand isn't
    # miscategorized as generic food.
    MEAL_KW = {"دايت","diet","اشتراك وجبات","meal subscription","meal plan","خطة غذائية","خطة أكل","سعرات","calorie","كيتو","keto","لوكارب","low carb","فيت فود","fit food","fitness meals","healthy meals","وجبات صحية","meal delivery","توصيل وجبات","نظام غذائي","تخسيس","weight loss meals","clean eating","مطعم صحي","healthy restaurant","مطعم دايت","diet restaurant","مشتركين","subscribers","اشتراكات","subscriptions"}
    FROZEN_KW = {"مجمد","مجمده","frozen","ice cream","بوظة","جليد"}
    FOOD_KW   = {"لحم","لحوم","دجاج","اسماك","سمك","خضار","فواكه","الألبان","ألبان","meat","chicken","fish","seafood","dairy","food","مواد غذائية","أغذية","مطعم","مطاعم","restaurant","مخبز","خبز","bakery","catering","تموين"}
    RETAIL_KW = {"هايبر","سوبر ماركت","تجزئة","سلسلة","hypermarket","supermarket","retail","chain"}
    CLOUD_KW  = {"cloud kitchen","مطبخ سحابي","مطابخ سحابية","dark kitchen","ghost kitchen"}
    SPECIALTY_KW = {"شوكولاتة","جبن","بيكري","chocolate","cheese","bakery","artisan","specialty"}
    ONLINE_KW = {"متجر إلكتروني","متاجر إلكترونية","online store","e-commerce","تجارة إلكترونية"}
    INDUSTRIAL_KW = {"كيماويات","صناعي","مصنع","chemical","industrial","factory","manufacturing"}
    LOGISTICS_KW  = {"شحن","لوجستيات","توزيع","logistics","shipping","distribution","transport"}

    URGENT_KW = {"عاجل","فوري","الآن","هذا الأسبوع","urgent","asap","immediately","immediately","this week","خلال أسبوع"}
    SAUDI_CITIES = {"الرياض","جدة","مكة","المدينة","الدمام","الخبر","الظهران","أبها","تبوك","القصيم","بريدة","حائل","نجران","جيزان","ينبع","الجبيل","الطائف","riyadh","jeddah","mecca","medina","dammam","khobar"}

    def score(self, lead: LeadCreate) -> HybridScoreResult:
        """
        Run deterministic scoring. Returns a locked HybridScoreResult.
        Claude receives this result and ONLY adds qualitative analysis.
        """
        text = self._collect_text(lead)
        rules: list[ScoreRule] = []
        total = 0

        # ── 1. Business Type (25 pts max) ─────────────────────────────────────
        btype, bscore = self._detect_business_type(text, lead.cargo_type)
        r = ScoreRule("business_type", f"نوع النشاط: {btype}", bscore, True, btype)
        rules.append(r)
        total += bscore

        # ── 2. Needs Refrigeration explicitly (15 pts) ─────────────────────────
        needs_ref = self._needs_refrigeration(text, lead.cargo_type)
        r2 = ScoreRule("needs_refrigeration", "يحتاج تبريداً صريحاً", 15, needs_ref)
        rules.append(r2)
        if needs_ref:
            total += 15

        # ── 3. Main city / Riyadh (10 pts) ────────────────────────────────────
        in_city = self._in_main_city(lead)
        r3 = ScoreRule("main_city", "في مدينة رئيسية (الرياض/جدة/الدمام)", 10, in_city)
        rules.append(r3)
        if in_city:
            total += 10

        # ── 4. Deliveries / Fleet size (20 pts) ───────────────────────────────
        fleet_pts = self._fleet_score(lead.fleet_size_needed)
        r4 = ScoreRule("fleet_size", f"حجم الأسطول: {lead.fleet_size_needed or 'غير محدد'}", 20, fleet_pts > 0, f"{fleet_pts} pts")
        rules.append(r4)
        total += fleet_pts

        # ── 5. Urgency (15 pts) ───────────────────────────────────────────────
        urgent = self._is_urgent(text)
        r5 = ScoreRule("urgency", "طلب عاجل / Urgent", 15, urgent)
        rules.append(r5)
        if urgent:
            total += 15

        # ── 6. Budget tier (30 pts max) ───────────────────────────────────────
        budget_pts = self._budget_score(lead.budget_monthly)
        r6 = ScoreRule("budget", f"الميزانية الشهرية: {lead.budget_monthly or 'غير محدد'} ريال", 30, budget_pts > 0, f"{budget_pts} pts")
        rules.append(r6)
        total += budget_pts

        # ── 7. Contact completeness (10 pts) ──────────────────────────────────
        contact_pts = (5 if lead.company else 0) + (5 if lead.email else 0)
        r7 = ScoreRule("contact_completeness", "اكتمال معلومات التواصل", 10, contact_pts > 0, f"{contact_pts} pts")
        rules.append(r7)
        total += contact_pts

        total = min(100, total)

        # Priority
        if total >= 70:
            priority = "high"
        elif total >= 40:
            priority = "medium"
        else:
            priority = "low"

        # Estimated values
        est_revenue, est_trips = self._estimate_values(lead, btype, total)
        confidence = min(95, total + 10) if lead.company and lead.phone else total
        prob_close = round({"high": 0.55, "medium": 0.25, "low": 0.08}.get(priority, 0.1), 2)
        deal_value = est_revenue * 12 * prob_close

        result = HybridScoreResult(
            rule_score=total,
            rule_breakdown=rules,
            category=self._btype_to_category(btype),
            priority=priority,
            estimated_monthly_revenue=est_revenue,
            estimated_trips=est_trips,
            confidence_score=confidence,
            probability_to_close=prob_close,
            expected_deal_value=round(deal_value),
        )

        logger.info(
            "Hybrid scoring complete",
            name=lead.name,
            score=total,
            category=result.category,
            priority=priority,
            est_revenue=est_revenue,
        )
        return result

    # ── Private helpers ────────────────────────────────────────────────────────

    def _collect_text(self, lead: LeadCreate) -> str:
        parts = [lead.name or "", lead.company or "", lead.cargo_type or "", lead.notes or ""]
        for v in lead.raw_data.values():
            if isinstance(v, str):
                parts.append(v)
        return " ".join(parts).lower()

    def _detect_business_type(self, text: str, cargo: str | None) -> tuple[str, int]:
        t = f"{cargo or ''} {text}".lower()
        # Pharma excluded 2026-08-09 — no priority branch for PHARMA_KW here.
        # PHARMA_KW is still used below in _needs_refrigeration() (pharma
        # products genuinely need cold chain — that's a physical fact, not
        # segment targeting), just not as a business-type score booster.
        # MEAL_KW checked first — most specific, and most inbound requests
        # are this type as of 2026-08-09.
        if any(k in t for k in self.MEAL_KW):        return "meal_subscription", 23
        if any(k in t for k in self.FROZEN_KW):      return "food_frozen", 22
        if any(k in t for k in self.CLOUD_KW):       return "cloud_kitchen", 15
        if any(k in t for k in self.SPECIALTY_KW):   return "specialty", 10
        if any(k in t for k in self.FOOD_KW):        return "food_fresh", 20
        if any(k in t for k in self.RETAIL_KW):      return "retail_chain", 16
        if any(k in t for k in self.INDUSTRIAL_KW):  return "industrial", 12
        if any(k in t for k in self.LOGISTICS_KW):   return "logistics", 10
        if any(k in t for k in self.ONLINE_KW):      return "online_food", 8
        return "other", 5

    def _needs_refrigeration(self, text: str, cargo: str | None) -> bool:
        ref_kw = {"مبرد","تبريد","مجمد","بارد","refrigerat","cold chain","chilled","frozen","temperature"}
        t = f"{cargo or ''} {text}".lower()
        return (
            any(k in t for k in ref_kw)
            or any(k in t for k in self.PHARMA_KW)
            or any(k in t for k in self.FROZEN_KW)
            or any(k in t for k in self.MEAL_KW)
        )

    def _in_main_city(self, lead: LeadCreate) -> bool:
        route_text = " ".join(filter(None, [lead.route_from, lead.route_to, lead.company])).lower()
        return any(city.lower() in route_text for city in self.SAUDI_CITIES)

    def _fleet_score(self, fleet: int | None) -> int:
        if not fleet:          return 0
        if fleet > 10:         return 20
        if fleet >= 5:         return 15
        if fleet >= 2:         return 8
        return 4

    def _is_urgent(self, text: str) -> bool:
        return any(k.lower() in text for k in self.URGENT_KW)

    def _budget_score(self, budget: float | None) -> int:
        if not budget:            return 0
        if budget >= 100_000:     return 30
        if budget >= 50_000:      return 25
        if budget >= 20_000:      return 18
        if budget >= 10_000:      return 12
        if budget >= 5_000:       return 8
        return 4

    def _estimate_values(self, lead: LeadCreate, btype: str, score: int) -> tuple[float, int]:
        """Estimate monthly revenue and trip count based on lead data."""
        base_rates = {
            # Meal Run: ~5,500 SAR/month is a conservative default for an
            # UNQUALIFIED lead — real Meal Run route pricing is 180-250
            # SAR/day for a small route (<=15 stops, 1-2 neighborhoods) up to
            # 340-400 SAR/day for a 16-30 stop / 3+ neighborhood route
            # (company-profile.md). Subscriber count and geographic
            # clustering decide the real number per deal — ask directly,
            # don't trust this estimate for an actual quote.
            "meal_subscription": 5500,
            "food_frozen": 6000, "food_fresh": 5000,
            "food_general": 4500, "retail_chain": 7000, "cloud_kitchen": 3500,
            "catering": 3000, "food_supplier": 4000, "industrial": 5500,
            "logistics": 4000, "specialty": 3500, "online_food": 2500, "other": 3000,
        }
        rate_per_truck = base_rates.get(btype, 3000)
        fleet = lead.fleet_size_needed or 1
        est_revenue = lead.budget_monthly or (rate_per_truck * fleet)
        est_trips = fleet * 20  # ~20 trips/truck/month
        return round(est_revenue), est_trips

    def _btype_to_category(self, btype: str) -> str:
        mapping = {
            "meal_subscription": "meal_run",  # kept distinct from food_transport — see LeadCategory.MEAL_RUN
            "food_frozen": "food_transport", "food_fresh": "food_transport",
            "food_general": "food_transport", "cloud_kitchen": "food_transport",
            "catering": "food_transport", "food_supplier": "food_transport",
            "specialty": "food_transport", "online_food": "food_transport",
            "retail_chain": "retail_chain",
            "industrial": "industrial_cold",
            "logistics": "logistics_company",
        }
        return mapping.get(btype, "other")
