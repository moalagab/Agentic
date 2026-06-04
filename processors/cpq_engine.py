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

VEHICLE_CONFIG = {
    "small_van": {
        "name_ar": "فان مبرد صغير",
        "capacity_kg": 1000,
        "capacity_m3": 8,
        "base_rate": 350,          # SAR per trip (city)
        "per_km_rate": 1.2,        # SAR per km
        "min_trip": 350,
    },
    "medium_truck": {
        "name_ar": "شاحنة متوسطة",
        "capacity_kg": 5000,
        "capacity_m3": 30,
        "base_rate": 700,
        "per_km_rate": 1.8,
        "min_trip": 700,
    },
    "large_truck": {
        "name_ar": "شاحنة كبيرة",
        "capacity_kg": 15000,
        "capacity_m3": 80,
        "base_rate": 1200,
        "per_km_rate": 2.5,
        "min_trip": 1200,
    },
    "reefer_trailer": {
        "name_ar": "مقطورة مبردة",
        "capacity_kg": 25000,
        "capacity_m3": 120,
        "base_rate": 2000,
        "per_km_rate": 3.5,
        "min_trip": 2000,
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
        vehicle_type: str = "medium_truck",
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
        vehicle = VEHICLE_CONFIG.get(vehicle_type, VEHICLE_CONFIG["medium_truck"])
        temp_cfg = TEMP_PREMIUMS.get(temperature_zone, TEMP_PREMIUMS["chilled"])
        urgency_mult = URGENCY_MULTIPLIERS.get(urgency, 1.0)
        freq_discount = calculate_frequency_discount(frequency_per_month)

        # ── Calculate base trip cost ──────────────────────────────────────────
        is_intercity = distance_km > 60

        if is_intercity:
            base_trip_rate = vehicle["base_rate"]
            distance_cost = distance_km * vehicle["per_km_rate"]
        else:
            # City delivery — flat base rate
            base_trip_rate = vehicle["base_rate"]
            distance_cost = distance_km * (vehicle["per_km_rate"] * 0.6)  # city multiplier

        raw_trip_cost = max(base_trip_rate + distance_cost, vehicle["min_trip"])

        # ── Apply premiums ────────────────────────────────────────────────────
        after_temp = raw_trip_cost * temp_cfg["multiplier"]
        after_urgency = after_temp * urgency_mult

        # ── Apply frequency discount ──────────────────────────────────────────
        price_per_trip = round(after_urgency * freq_discount, -1)  # round to nearest 10 SAR
        price_per_trip = max(price_per_trip, vehicle["min_trip"])

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
