"""
CPQ Engine — محرك التسعير الذكي (Configure Price Quote)
Layer 6 of RevOS v6

يولّد عروض أسعار دقيقة وفورية بناءً على:
- المسار (من/إلى + المسافة)
- درجة الحرارة المطلوبة
- نوع المركبة
- الحجم / الوزن
- تردد الرحلات
- مستوى الإلحاح
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)

# ─── Known Routes Database ────────────────────────────────────────────────────

KNOWN_ROUTES: dict[tuple[str, str], int] = {
    ("الرياض", "الدمام"): 400,
    ("الدمام", "الرياض"): 400,
    ("الرياض", "جدة"): 950,
    ("جدة", "الرياض"): 950,
    ("الرياض", "القصيم"): 320,
    ("القصيم", "الرياض"): 320,
    ("الرياض", "مكة"): 870,
    ("مكة", "الرياض"): 870,
    ("الرياض", "المدينة"): 970,
    ("المدينة", "الرياض"): 970,
    ("الرياض", "تبوك"): 1280,
    ("تبوك", "الرياض"): 1280,
    ("الرياض", "أبها"): 930,
    ("أبها", "الرياض"): 930,
    ("جدة", "مكة"): 80,
    ("مكة", "جدة"): 80,
    ("جدة", "المدينة"): 430,
    ("المدينة", "جدة"): 430,
    ("الدمام", "الجبيل"): 100,
    ("الجبيل", "الدمام"): 100,
    ("الدمام", "الخبر"): 15,
    ("الدمام", "أبها"): 900,
}

# ─── Vehicle Pricing ──────────────────────────────────────────────────────────
#
# cost_basis holds REAL operating costs for the SMALL VAN specifically
# (confirmed by Mo 2026-08-04/09, cross-checked against AI-BOS's independent
# bottom-up Meal Run pricing derivation in the vault — the two agree once the
# margin formula below is applied). There is still no rented vehicle as of
# this date; these are the figures Smart Field would actually pay once one is.
#
#   vehicle_rental_monthly : 3,500 SAR/month — small refrigerated van, no driver
#   driver_salary_monthly  : 2,000 SAR/month
#   fuel_sar_per_km        : diesel, 1.79 SAR/L (Aramco 2026 price, reviewed
#                             annually) at ~13 L/100km consumption ⇒ ~0.23 SAR/km
#
# medium_truck / large_truck / reefer_trailer still have no cost_basis —
# calculate() refuses to quote those tiers (raises, does not fabricate) until
# real numbers are supplied here. Do NOT fill them in by scaling small_van's
# numbers by capacity — that is a guess, not data.
#
# RESERVE_PERCENT / MARGIN_PERCENT: reserve is added to direct cost first
# (maintenance, driver absence, etc.), then price is derived so MARGIN_PERCENT
# is a share of REVENUE, not a markup on cost:
#   price = (direct_cost * (1 + RESERVE_PERCENT)) / (1 - MARGIN_PERCENT)
# This matches the methodology already validated against real numbers in the
# Meal Run pricing (see company-profile.md — 30km route: 218.5 SAR direct
# cost -> 251.3 with 15% reserve -> 335 SAR at 25% margin, reproduced exactly
# by this formula). MARGIN_PERCENT's value (30%) is still a placeholder for
# general CPQ trips — Mo confirmed 25-35% specifically for Meal Run routes;
# confirm before treating 30% as final for non-Meal-Run quotes too.
RESERVE_PERCENT = 0.15
MARGIN_PERCENT = 0.30
FUEL_SAR_PER_KM = 0.23  # diesel, ~13 L/100km, 1.79 SAR/L (Aramco 2026)

VEHICLE_CONFIG = {
    "small_van": {
        "name_ar": "فان مبرد صغير",
        "capacity_kg": 1000,
        "capacity_m3": 8,
        "cost_basis": {
            "vehicle_rental_monthly": 3500,
            "driver_salary_monthly": 2000,
        },
    },
    "medium_truck": {
        "name_ar": "شاحنة متوسطة",
        "capacity_kg": 5000,
        "capacity_m3": 30,
        "cost_basis": None,  # NEEDS REAL COST DATA
    },
    "large_truck": {
        "name_ar": "شاحنة كبيرة",
        "capacity_kg": 15000,
        "capacity_m3": 80,
        "cost_basis": None,  # NEEDS REAL COST DATA
    },
    "reefer_trailer": {
        "name_ar": "مقطورة مبردة",
        "capacity_kg": 25000,
        "capacity_m3": 120,
        "cost_basis": None,  # NEEDS REAL COST DATA
    },
}

# ─── Temperature Zone Premiums ────────────────────────────────────────────────

TEMP_PREMIUMS = {
    "chilled":  {"name_ar": "مبرد (+2°C إلى +8°C)", "multiplier": 1.0},
    "frozen":   {"name_ar": "مجمد (-18°C إلى -25°C)", "multiplier": 1.15},
    "pharma":   {"name_ar": "صيدلاني (+2°C إلى +8°C مع توثيق)", "multiplier": 1.30},
}

# ─── Urgency Multipliers ──────────────────────────────────────────────────────

URGENCY_MULTIPLIERS = {
    "normal":   1.0,    # 24h+ notice
    "express":  1.20,   # 4-8h notice
    "urgent":   1.40,   # <4h notice
}

# ─── Discount Rules ───────────────────────────────────────────────────────────

def calculate_frequency_discount(trips_per_month: int) -> float:
    """Returns discount multiplier based on monthly trip frequency."""
    if trips_per_month >= 30:
        return 0.80  # 20% discount
    elif trips_per_month >= 20:
        return 0.85  # 15% discount
    elif trips_per_month >= 10:
        return 0.90  # 10% discount
    elif trips_per_month >= 5:
        return 0.95  # 5% discount
    return 1.0


@dataclass
class CPQQuote:
    """Result of a CPQ calculation."""
    # Inputs
    route_from: str
    route_to: str
    vehicle_type: str
    temperature_zone: str
    distance_km: int
    frequency_per_month: int
    urgency: str

    # Calculated
    base_trip_rate: float = 0.0
    distance_cost: float = 0.0
    temp_premium_pct: float = 0.0
    urgency_premium_pct: float = 0.0
    frequency_discount_pct: float = 0.0

    price_per_trip: float = 0.0
    monthly_estimate: float = 0.0
    annual_estimate: float = 0.0

    # Discounts for contracts
    monthly_contract_price: float = 0.0
    annual_contract_price: float = 0.0

    valid_until: datetime = field(default_factory=lambda: datetime.utcnow() + timedelta(days=7))
    generated_at: datetime = field(default_factory=datetime.utcnow)

    # Breakdown text (Arabic)
    breakdown_ar: list[str] = field(default_factory=list)
    quote_summary_ar: str = ""
    vehicle_name_ar: str = ""
    temp_name_ar: str = ""


class CPQEngine:
    """
    Configure-Price-Quote engine for Smartfield refrigerated transport.
    يولّد عروض أسعار فورية ودقيقة بناءً على متغيرات الشحنة.
    """

    def __init__(self) -> None:
        self._log = logger.bind(component="CPQEngine")

    def calculate(
        self,
        route_from: str,
        route_to: str,
        vehicle_type: str = "small_van",
        temperature_zone: str = "chilled",
        frequency_per_month: int = 1,
        urgency: str = "normal",
        distance_km: Optional[int] = None,
        volume_m3: Optional[float] = None,
        weight_kg: Optional[float] = None,
    ) -> CPQQuote:
        """
        Main CPQ calculation.

        Args:
            route_from: Origin city
            route_to: Destination city
            vehicle_type: small_van / medium_truck / large_truck / reefer_trailer
                          (only small_van has real cost data as of 2026-08-09 —
                          the others raise ValueError instead of guessing a price)
            temperature_zone: chilled / frozen / pharma
            frequency_per_month: Number of trips per month
            urgency: normal / express / urgent
            distance_km: Override distance (auto-detected if not provided)
            volume_m3: Cargo volume (used for vehicle recommendation if type not specified)
            weight_kg: Cargo weight (used for vehicle recommendation)
        """
        # ── Resolve distance ──────────────────────────────────────────────────
        if distance_km is None:
            distance_km = self._lookup_distance(route_from, route_to)

        # ── Resolve vehicle type (auto-recommend if needed) ───────────────────
        vehicle_type = self._resolve_vehicle(vehicle_type, volume_m3, weight_kg)

        # ── Validate inputs ───────────────────────────────────────────────────
        vehicle = VEHICLE_CONFIG.get(vehicle_type, VEHICLE_CONFIG["small_van"])
        temp_cfg = TEMP_PREMIUMS.get(temperature_zone, TEMP_PREMIUMS["chilled"])
        urgency_mult = URGENCY_MULTIPLIERS.get(urgency, 1.0)

        cost_basis = vehicle.get("cost_basis")
        if cost_basis is None:
            # Refuse to fabricate a price. See the VEHICLE_CONFIG comment —
            # only small_van has real operating costs as of 2026-08-09.
            raise ValueError(
                f"No real cost data for vehicle_type='{vehicle_type}' — "
                "refusing to generate a quote with a guessed price. Add "
                "cost_basis (vehicle_rental_monthly, driver_salary_monthly) "
                "to VEHICLE_CONFIG for this tier first."
            )

        # ── Calculate base trip cost from real operating costs ─────────────────
        # cost_per_trip = fixed monthly costs / trips  +  distance * diesel rate.
        # This replaces the old flat base_rate + per_km_rate guess. Dividing
        # the fixed cost by frequency_per_month means cost-per-trip naturally
        # falls as trip volume rises (the real economics behind a volume
        # discount), so the old calculate_frequency_discount() schedule is
        # NOT reapplied here — stacking it on top would double-count the
        # same effect.
        trips = max(frequency_per_month, 1)

        fixed_monthly = (
            cost_basis["vehicle_rental_monthly"] + cost_basis["driver_salary_monthly"]
        )

        base_trip_rate = fixed_monthly / trips           # fixed cost share for this trip
        distance_cost = distance_km * FUEL_SAR_PER_KM     # actual diesel cost for this trip
        raw_trip_cost = base_trip_rate + distance_cost

        # ── Apply reserve, then margin as a share of revenue ────────────────────
        # price = (direct_cost * (1 + RESERVE_PERCENT)) / (1 - MARGIN_PERCENT)
        # NOT direct_cost * (1 + MARGIN_PERCENT) — see the VEHICLE_CONFIG
        # comment for why (margin-of-revenue vs markup-on-cost are different
        # numbers for the same nominal percentage; this formula is the one
        # validated against real Meal Run route pricing).
        raw_trip_cost = (raw_trip_cost * (1 + RESERVE_PERCENT)) / (1 - MARGIN_PERCENT)

        # ── Apply premiums ────────────────────────────────────────────────────
        after_temp = raw_trip_cost * temp_cfg["multiplier"]
        after_urgency = after_temp * urgency_mult

        freq_discount = 1.0  # already reflected via trips-based division above
        price_per_trip = round(after_urgency, -1)  # round to nearest 10 SAR

        monthly_estimate = round(price_per_trip * frequency_per_month, -2)
        annual_estimate = monthly_estimate * 12

        # Contract pricing
        monthly_contract = round(monthly_estimate * 0.88, -2)   # 12% contract discount
        annual_contract = round(annual_estimate * 0.80, -2)     # 20% annual discount

        # ── Build breakdown ───────────────────────────────────────────────────
        breakdown = self._build_breakdown(
            vehicle=vehicle,
            temp_cfg=temp_cfg,
            distance_km=distance_km,
            base_trip_rate=base_trip_rate,
            distance_cost=distance_cost,
            raw_trip_cost=raw_trip_cost,
            temp_premium_pct=(temp_cfg["multiplier"] - 1) * 100,
            urgency_pct=(urgency_mult - 1) * 100,
            freq_discount_pct=(1 - freq_discount) * 100,
            price_per_trip=price_per_trip,
            urgency=urgency,
        )

        quote = CPQQuote(
            route_from=route_from,
            route_to=route_to,
            vehicle_type=vehicle_type,
            temperature_zone=temperature_zone,
            distance_km=distance_km,
            frequency_per_month=frequency_per_month,
            urgency=urgency,
            base_trip_rate=base_trip_rate,
            distance_cost=distance_cost,
            temp_premium_pct=(temp_cfg["multiplier"] - 1) * 100,
            urgency_premium_pct=(urgency_mult - 1) * 100,
            frequency_discount_pct=(1 - freq_discount) * 100,
            price_per_trip=price_per_trip,
            monthly_estimate=monthly_estimate,
            annual_estimate=annual_estimate,
            monthly_contract_price=monthly_contract,
            annual_contract_price=annual_contract,
            breakdown_ar=breakdown,
            vehicle_name_ar=vehicle["name_ar"],
            temp_name_ar=temp_cfg["name_ar"],
        )

        quote.quote_summary_ar = self._build_summary(quote)

        self._log.info(
            "cpq.calculated",
            route=f"{route_from}→{route_to}",
            vehicle=vehicle_type,
            distance_km=distance_km,
            price_per_trip=price_per_trip,
            monthly=monthly_estimate,
        )

        return quote

    def recommend_vehicle(
        self,
        weight_kg: Optional[float] = None,
        volume_m3: Optional[float] = None,
        fleet_size: Optional[int] = None,
    ) -> str:
        """Recommend the best vehicle type based on cargo specs."""
        if weight_kg and weight_kg > 15000:
            return "reefer_trailer"
        if weight_kg and weight_kg > 5000:
            return "large_truck"
        if volume_m3 and volume_m3 > 30:
            return "large_truck"
        if fleet_size and fleet_size >= 10:
            return "large_truck"
        if weight_kg and weight_kg > 1000:
            return "medium_truck"
        if volume_m3 and volume_m3 > 8:
            return "medium_truck"
        return "medium_truck"

    def _resolve_vehicle(
        self,
        vehicle_type: str,
        volume_m3: Optional[float],
        weight_kg: Optional[float],
    ) -> str:
        if vehicle_type in VEHICLE_CONFIG:
            return vehicle_type
        return self.recommend_vehicle(weight_kg=weight_kg, volume_m3=volume_m3)

    def _lookup_distance(self, from_city: str, to_city: str) -> int:
        """Look up known route distance or estimate based on city tier."""
        # Normalize city names
        from_norm = from_city.strip()
        to_norm = to_city.strip()

        if from_norm == to_norm:
            return 30  # Same city delivery

        key = (from_norm, to_norm)
        if key in KNOWN_ROUTES:
            return KNOWN_ROUTES[key]

        # Estimate: major cities tend to be 300-700 km apart in Saudi Arabia
        self._log.debug("cpq.unknown_route", from_city=from_norm, to_city=to_norm)
        return 400  # Conservative default for unknown routes

    def _build_breakdown(self, **kwargs) -> list[str]:
        v = kwargs
        lines = [
            f"المركبة: {v['vehicle']['name_ar']}",
            f"المسافة: {v['distance_km']} كم",
            f"السعر الأساسي: {v['base_trip_rate']:,.0f} ريال",
            f"تكلفة المسافة: {v['distance_cost']:,.0f} ريال",
            f"السعر قبل الإضافات: {v['raw_trip_cost']:,.0f} ريال",
        ]
        if v["temp_premium_pct"] > 0:
            lines.append(f"إضافة درجة الحرارة ({v['temp_cfg']['name_ar']}): +{v['temp_premium_pct']:.0f}%")
        if v["urgency_pct"] > 0:
            urgency_labels = {"express": "سريع", "urgent": "عاجل"}
            label = urgency_labels.get(v["urgency"], "")
            lines.append(f"إضافة الإلحاح ({label}): +{v['urgency_pct']:.0f}%")
        if v["freq_discount_pct"] > 0:
            lines.append(f"خصم التردد: -{v['freq_discount_pct']:.0f}%")
        lines.append(f"سعر الرحلة الواحدة: {v['price_per_trip']:,.0f} ريال")
        return lines

    def _build_summary(self, q: CPQQuote) -> str:
        return (
            f"عرض سعر — {q.route_from} إلى {q.route_to}\n"
            f"━━━━━━━━━━━━━━\n"
            f"المركبة: {q.vehicle_name_ar}\n"
            f"درجة الحرارة: {q.temp_name_ar}\n"
            f"المسافة: {q.distance_km} كم\n"
            f"\n"
            f"💰 سعر الرحلة الواحدة: *{q.price_per_trip:,.0f} ريال*\n"
            f"📅 التقدير الشهري ({q.frequency_per_month} رحلة): *{q.monthly_estimate:,.0f} ريال*\n"
            f"\n"
            f"خيارات العقد:\n"
            f"  • عقد شهري: {q.monthly_contract_price:,.0f} ريال/شهر (توفير 12%)\n"
            f"  • عقد سنوي: {q.annual_contract_price:,.0f} ريال/سنة (توفير 20%)\n"
            f"\n"
            f"صالح حتى: {q.valid_until.strftime('%Y-%m-%d')}"
        )

    def to_dict(self, quote: CPQQuote) -> dict:
        """Convert quote to JSON-serializable dict."""
        return {
            "route_from": quote.route_from,
            "route_to": quote.route_to,
            "vehicle_type": quote.vehicle_type,
            "vehicle_name_ar": quote.vehicle_name_ar,
            "temperature_zone": quote.temperature_zone,
            "temp_name_ar": quote.temp_name_ar,
            "distance_km": quote.distance_km,
            "frequency_per_month": quote.frequency_per_month,
            "urgency": quote.urgency,
            "price_per_trip": quote.price_per_trip,
            "monthly_estimate": quote.monthly_estimate,
            "annual_estimate": quote.annual_estimate,
            "monthly_contract_price": quote.monthly_contract_price,
            "annual_contract_price": quote.annual_contract_price,
            "breakdown_ar": quote.breakdown_ar,
            "quote_summary_ar": quote.quote_summary_ar,
            "valid_until": quote.valid_until.isoformat(),
            "generated_at": quote.generated_at.isoformat(),
        }
