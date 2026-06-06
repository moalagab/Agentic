"""
Smart Field Knowledge Base — قاعدة معرفة سمارت فيلد

Single source of truth for all company facts.
Claude reads this before every customer conversation and proposal.
"""

from __future__ import annotations

# ─── Company Profile ──────────────────────────────────────────────────────────

COMPANY = {
    "name_ar": "سمارت فيلد",
    "name_en": "Smart Field",
    "tagline": "النقل المبرد الاحترافي في المملكة العربية السعودية",
    "founded": "2020",
    "hq": "الرياض، المملكة العربية السعودية",
    "certifications": ["ISO 22000", "HACCP", "GAP Saudi"],
    "website": "smartfield.sa",
    "strengths": [
        "أسطول حديث من شاحنات Thermo King",
        "تتبع GPS لحظة بلحظة لكل شحنة",
        "سائقون مدرّبون على سلسلة التبريد",
        "تقارير درجة الحرارة عند التسليم",
        "تغطية الرياض وجميع المناطق الرئيسية",
        "نظام CRM متكامل لتتبع الشحنات",
    ],
}

# ─── Services ─────────────────────────────────────────────────────────────────

SERVICES = {
    "chilled_transport": {
        "name_ar": "نقل مبرد",
        "temp_range": "-2°C إلى +8°C",
        "suitable_for": ["لحوم طازجة", "ألبان", "خضار وفاكهة", "أسماك", "منتجات مخبزية"],
        "min_load": "100 كغ",
    },
    "frozen_transport": {
        "name_ar": "نقل مجمد",
        "temp_range": "-18°C إلى -25°C",
        "suitable_for": ["لحوم مجمدة", "بوظة وآيسكريم", "دواجن مجمدة", "مأكولات بحرية مجمدة"],
        "min_load": "100 كغ",
    },
    "pharma_transport": {
        "name_ar": "نقل صيدلاني",
        "temp_range": "+2°C إلى +8°C",
        "suitable_for": ["أدوية", "لقاحات", "مستحضرات حيوية", "مستلزمات طبية حساسة"],
        "special": "وثائق سلسلة التبريد الكاملة مع كل شحنة",
    },
    "dedicated_fleet": {
        "name_ar": "أسطول مخصص",
        "description": "تخصيص شاحنة أو أكثر بشكل دائم للعميل بعقد شهري",
        "suitable_for": ["سلاسل التجزئة", "مصانع الأغذية", "شركات التموين"],
    },
    "distribution": {
        "name_ar": "توزيع داخل المدينة",
        "description": "رحلات متعددة داخل المدينة في يوم واحد",
        "min_stops": 3,
    },
}

# ─── Vehicle Types ─────────────────────────────────────────────────────────────

VEHICLES = {
    "small_van": {
        "name_ar": "فان مبرد صغير",
        "capacity_kg": 1000,
        "capacity_m3": 8,
        "suitable_for": "توصيل داخل المدينة، كميات صغيرة",
        "rate_per_trip": "350-550 ريال",
    },
    "medium_truck": {
        "name_ar": "شاحنة متوسطة",
        "capacity_kg": 5000,
        "capacity_m3": 30,
        "suitable_for": "نقل بين المدن، مصانع، مطاعم",
        "rate_per_trip": "700-1200 ريال",
    },
    "large_truck": {
        "name_ar": "شاحنة كبيرة",
        "capacity_kg": 15000,
        "capacity_m3": 80,
        "suitable_for": "كميات كبيرة، عقود شهرية، مستودعات",
        "rate_per_trip": "1200-2500 ريال",
    },
    "reefer_trailer": {
        "name_ar": "مقطورة مبردة",
        "capacity_kg": 25000,
        "capacity_m3": 120,
        "suitable_for": "الشحنات الكبيرة جداً، عقود طويلة الأمد",
        "rate_per_trip": "2000-4500 ريال",
    },
}

# ─── Coverage Areas ────────────────────────────────────────────────────────────

COVERAGE = {
    "primary": {
        "cities": ["الرياض", "جدة", "الدمام", "الخبر", "الظهران"],
        "response_time": "خلال 4 ساعات",
        "availability": "24/7",
    },
    "secondary": {
        "cities": ["مكة المكرمة", "المدينة المنورة", "الطائف", "القصيم", "بريدة", "أبها", "تبوك"],
        "response_time": "خلال 24 ساعة",
        "availability": "أيام العمل",
    },
    "routes": {
        "riyadh_dammam": {"distance_km": 400, "est_time_hrs": 4, "rate_medium": 900},
        "riyadh_jeddah": {"distance_km": 950, "est_time_hrs": 9, "rate_medium": 1800},
        "jeddah_mecca":  {"distance_km": 80,  "est_time_hrs": 1, "rate_medium": 450},
        "riyadh_qassim": {"distance_km": 320, "est_time_hrs": 3, "rate_medium": 750},
        "dammam_jubail": {"distance_km": 100, "est_time_hrs": 1, "rate_medium": 500},
    },
}

# ─── Pricing Rules ─────────────────────────────────────────────────────────────

PRICING = {
    "model": "per_trip",  # or "monthly_retainer"
    "base_factors": [
        "المسافة (كم)",
        "نوع الشاحنة (حجم الحمولة)",
        "درجة الحرارة المطلوبة",
        "الكمية (كغ أو م³)",
        "تردد الرحلات",
    ],
    "discounts": {
        "monthly_contract": "10-15% خصم على عقود شهرية",
        "volume_above_20": "5% خصم إضافي على 20+ رحلة شهرياً",
        "annual_contract": "20% خصم على العقود السنوية",
    },
    "indicative_rates": {
        "city_delivery_small": "350-550 ريال/رحلة (داخل الرياض)",
        "intercity_medium": "700-1800 ريال/رحلة (بين المدن الرئيسية)",
        "monthly_dedicated": "8000-25000 ريال/شهر (شاحنة مخصصة)",
        "pharma_premium": "زيادة 20-30% على الأسعار العادية",
    },
    "note": "الأسعار تقديرية — السعر الدقيق يعتمد على تفاصيل الشحنة والمسار",
}

# ─── FAQ ──────────────────────────────────────────────────────────────────────

FAQ = [
    {
        "q": "هل تقدرون توصلون للمدينة المنورة؟",
        "a": "نعم، المدينة المنورة ضمن تغطيتنا — الرد خلال 24 ساعة وتحديد موعد مناسب.",
    },
    {
        "q": "كم يكلف نقل دجاج من الرياض للدمام؟",
        "a": "شاحنة متوسطة الرياض–الدمام (400 كم) تبدأ من 850 ريال. يعتمد على الوزن والتردد.",
    },
    {
        "q": "هل عندكم وثائق سلسلة التبريد للأدوية؟",
        "a": "نعم، نوفر logger درجة الحرارة وتقرير كامل مع كل شحنة صيدلانية.",
    },
    {
        "q": "هل يمكن عقد شهري؟",
        "a": "بالتأكيد — العقود الشهرية توفر 10-15%، والسنوية توفر حتى 20%.",
    },
    {
        "q": "ما هي أقل كمية؟",
        "a": "لا يوجد حد أدنى رسمي، لكن أقل رحلة جدوى اقتصادية عادة من 300-500 كغ.",
    },
    {
        "q": "هل تتبعون الشحنة؟",
        "a": "نعم، GPS مباشر — يمكن إرسال رابط تتبع لحظي للعميل.",
    },
    {
        "q": "كم وقت تحتاج لتجهيز شاحنة؟",
        "a": "في الرياض: 2-4 ساعات للطلبات العاجلة. للمسبوقة: الحجز المسبق بيوم.",
    },
]

# ─── Case Studies ─────────────────────────────────────────────────────────────

CASE_STUDIES = [
    {
        "client_type": "مصنع دواجن — الرياض",
        "challenge": "احتاج لنقل 8 طن دجاج يومياً لأكثر من 15 نقطة توزيع",
        "solution": "4 شاحنات مخصصة بعقد شهري مع جدول توزيع ثابت",
        "result": "تقليل الهدر 40%، توفير 15% تكلفة مقارنة بالسابق",
        "monthly_value": "42,000 ريال/شهر",
    },
    {
        "client_type": "شركة أدوية — جدة",
        "challenge": "نقل لقاحات تتطلب +2 إلى +8 درجة مع توثيق كامل",
        "solution": "شاحنة مخصصة مع logger مستمر وتقارير يومية",
        "result": "صفر انتهاك لسلسلة التبريد على مدى 18 شهراً",
        "monthly_value": "28,000 ريال/شهر",
    },
    {
        "client_type": "سلسلة مطاعم — الرياض وجدة",
        "challenge": "توصيل مكونات طازجة لـ 22 فرعاً يومياً",
        "solution": "نظام توزيع داخل المدينة 6 أيام أسبوعياً",
        "result": "تحسين طزاجة المنتج، انخفاض الشكاوى 70%",
        "monthly_value": "35,000 ريال/شهر",
    },
]

# ─── Knowledge Base as formatted text (for Claude prompt injection) ───────────

def get_kb_text() -> str:
    """Return a concise knowledge base string to inject into Claude's system prompt."""
    routes_text = "\n".join(
        f"  {k.replace('_', '→')}: {v['rate_medium']} ريال تقريباً ({v['distance_km']} كم)"
        for k, v in COVERAGE["routes"].items()
    )
    faq_text = "\n".join(
        f"  س: {f['q']}\n  ج: {f['a']}"
        for f in FAQ[:5]
    )
    case_text = "\n".join(
        f"  {c['client_type']}: {c['result']} — {c['monthly_value']}"
        for c in CASE_STUDIES
    )
    vehicles_text = "\n".join(
        f"  {v['name_ar']}: حتى {v['capacity_kg']} كغ — {v['rate_per_trip']}"
        for v in VEHICLES.values()
    )

    return f"""
## معلومات Smart Field (للرجوع إليها في المحادثات)

### الخدمات
- نقل مبرد: -2°C إلى +8°C (لحوم، ألبان، خضار، سمك)
- نقل مجمد: -18°C إلى -25°C (لحوم مجمدة، آيسكريم)
- نقل صيدلاني: +2°C إلى +8°C مع توثيق كامل
- أسطول مخصص بعقود شهرية

### أنواع الشاحنات والأسعار
{vehicles_text}

### أسعار المسارات الرئيسية (تقديرية)
{routes_text}

### الخصومات
- عقد شهري: 10-15%
- 20+ رحلة/شهر: 5% إضافي
- عقد سنوي: 20%

### أبرز الأسئلة والأجوبة
{faq_text}

### نماذج من عملائنا
{case_text}

### ملاحظة
الأسعار تقديرية. دائماً أخبر العميل أن السعر الدقيق يتطلب تفاصيل الشحنة.
""".strip()


def get_scoring_context() -> str:
    """Return scoring weights context for the agent."""
    return """
## جدول التقييم — Rule-Based Score

| العامل | النقاط |
|---|---|
| صيدليات / موزع أدوية | 25 |
| مجمدات / لحوم | 22 |
| أغذية طازجة | 20 |
| سلاسل تجزئة | 16 |
| مطابخ سحابية | 15 |
| في مدينة رئيسية | 10 |
| أكثر من 10 شاحنات | +20 |
| طلب عاجل | +15 |
| ميزانية >50k ريال | +25 |

الأولوية: HIGH ≥70 | MEDIUM 40-69 | LOW <40
""".strip()
