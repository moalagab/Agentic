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
    # NOTE (2026-08-09): Smart Field does not currently have a rented vehicle
    # (confirmed in AI-BOS Operating Context, discovered 2026-08-04). Do not
    # add claims here that imply an existing fleet is ready to dispatch —
    # vehicles are mobilized after a qualifying contract is signed.
    "strengths": [
        "تجهيز مركبة مبردة مخصصة عند توقيع العقد (شبكة موردين معتمدين)",
        "نظام تتبع GPS وتقرير درجة الحرارة مع كل شحنة",
        "سائقون مدرّبون على سلسلة التبريد",
        "تغطية الرياض وقابلية التوسّع للمناطق الرئيسية",
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
    # pharma_transport removed 2026-08-09 — legally prohibited for Smart
    # Field (no SFDA carrier license + GDP requirements not met), not just
    # deprioritized. Do not re-add without an actual carrier license.
    "meal_run": {
        "name_ar": "Meal Run — خط توزيع اشتراكات وجبات",
        "description": (
            "خط توزيع صباحي/مسائي ثابت يمر على مشتركي مطعم صحي أو مزوّد "
            "خدمة وجبات/اشتراك غذائي واحد (له أكثر من مشترك) ضمن نطاق "
            "جغرافي متقارب. العقد والفاتورة مع تلك المنشأة فقط — بيوت "
            "المشتركين محطات توقف، لا عقود فردية أبدًا."
        ),
        "suitable_for": ["مطاعم صحية", "شركات ومزوّدي اشتراك وجبات صحية/دايت", "خطط غذائية بتوصيل يومي"],
        "requires": "عدد مشتركين + تركّز جغرافي (حي واحد أو أحياء متجاورة) — التركّز يحسم الجدوى لا العدد وحده",
        "added": "2026-08-04",
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
# Capacities describe what CAN be arranged once a client signs, not vehicles
# currently on hand. No rate_per_trip here — Smart Field has no rented
# vehicle today, so any trip price would be a guess with no real cost basis.
# Pricing is quoted per-client via the CPQ engine after qualification.

VEHICLES = {
    "small_van": {
        "name_ar": "فان مبرد صغير",
        "capacity_kg": 1000,
        "capacity_m3": 8,
        "suitable_for": "توصيل داخل المدينة، كميات صغيرة",
    },
    "medium_truck": {
        "name_ar": "شاحنة متوسطة",
        "capacity_kg": 5000,
        "capacity_m3": 30,
        "suitable_for": "نقل بين المدن، مصانع، مطاعم",
    },
    "large_truck": {
        "name_ar": "شاحنة كبيرة",
        "capacity_kg": 15000,
        "capacity_m3": 80,
        "suitable_for": "كميات كبيرة، عقود شهرية، مستودعات",
    },
    "reefer_trailer": {
        "name_ar": "مقطورة مبردة",
        "capacity_kg": 25000,
        "capacity_m3": 120,
        "suitable_for": "الشحنات الكبيرة جداً، عقود طويلة الأمد",
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
    # Distance/time are geographic facts; no rate here — see VEHICLES note.
    "routes": {
        "riyadh_dammam": {"distance_km": 400, "est_time_hrs": 4},
        "riyadh_jeddah": {"distance_km": 950, "est_time_hrs": 9},
        "jeddah_mecca":  {"distance_km": 80,  "est_time_hrs": 1},
        "riyadh_qassim": {"distance_km": 320, "est_time_hrs": 3},
        "dammam_jubail": {"distance_km": 100, "est_time_hrs": 1},
    },
}

# ─── Pricing Rules ─────────────────────────────────────────────────────────────
# No indicative SAR figures here by design — Smart Field has no rented
# vehicle today (see COMPANY note above), so a quoted number would have no
# real cost basis. Per AI-BOS's 2026-08-04 decision, vehicle rental is
# financed BACKWARDS from a signed commitment, not the other way around:
# a vehicle is mobilized only after a client signs a qualifying contract
# (monthly-recurring or 3-month minimum). Every quote must say so explicitly
# and must NOT promise immediate start.

PRICING = {
    "model": "per_trip",  # or "monthly_retainer"
    "base_factors": [
        "المسافة (كم)",
        "نوع الشاحنة (حجم الحمولة)",
        "درجة الحرارة المطلوبة",
        "الكمية (كغ أو م³)",
        "تردد الرحلات",
        "الالتزام التعاقدي (اشتراك شهري متجدد أو 3 أشهر كحد أدنى)",
    ],
    "discounts": {
        "monthly_contract": "خصم على عقود شهرية — يُحدَّد بعد التقييم",
        "annual_contract": "خصم إضافي على العقود السنوية",
    },
    "commitment_required": (
        "لا تُجهَّز أي مركبة قبل توقيع عقد بحد أدنى للالتزام "
        "(اشتراك شهري متجدد أو 3 أشهر). يوجد فارق زمني بين التوقيع "
        "وبدء الخدمة الفعلي لتجهيز المركبة — يجب توضيحه للعميل صراحة."
    ),
    "note": "لا نعطي رقمًا نهائيًا في المحادثة — السعر الدقيق يُصدر عبر عرض رسمي بعد تفاصيل الشحنة والمسار.",
}

# ─── Meal Run Pricing (real numbers — confirmed by Mo, 2026-08-04/09) ─────────
# Route-based, NOT per-trip like the general PRICING above. Built bottom-up
# from real small-van costs (see processors/cpq_engine.py) + 15% reserve +
# 25-35% margin. Still never state these numbers directly in conversation —
# same rule as PRICING["note"] — but they are the real basis for what the
# sales team quotes, unlike the general per-trip figures which stay
# deliberately number-free until CPQ runs.
MEAL_RUN_PRICING = {
    "unit": "per_route_per_day",  # NOT per subscriber, NOT per trip
    "tiers": [
        {
            "range_ar": "حي واحد أو حيّان متجاوران، حتى 15 محطة",
            "sar_per_day": "180-250",
            "note": "لا يزال مبنيًا على سعر الرحلة العام — يُعاد حسابه بمنهجية bottom-up عند أول عميل فعلي بهذا الحجم",
        },
        {
            "range_ar": "نطاق أوسع (3+ أحياء) أو 16-30 محطة",
            "sar_per_day": "340-400",
            "note": "يغطي هامش ربح 25-35% فوق التكلفة الفعلية + احتياطي 15%، محسوب بدقة",
        },
        {
            "range_ar": "متفرق جغرافيًا (يحتاج خطين أو أكثر)",
            "sar_per_day": None,
            "note": "غالبًا غير مجدٍ اقتصاديًا — التركّز الجغرافي هو المتغير الحاسم، يُسعَّر كخطين منفصلين إن أُصر عليه",
        },
    ],
    "rule": "التسعير بالخط لا بالمشترك. لا عقد ولا فاتورة مباشرة مع أي مشترك فردي — العقد مع المطعم الصحي أو مزوّد خدمة الوجبات نفسه فقط.",
}

# ─── FAQ ──────────────────────────────────────────────────────────────────────

FAQ = [
    {
        "q": "هل تقدرون توصلون للمدينة المنورة؟",
        "a": "نعم، المدينة المنورة ضمن تغطيتنا — الرد خلال 24 ساعة وتحديد موعد مناسب.",
    },
    {
        "q": "كم يكلف نقل دجاج من الرياض للدمام؟",
        "a": "يعتمد على الوزن والتردد ونوع المركبة — أرسل التفاصيل ونجهّز لك عرض سعر رسمي.",
    },
    {
        "q": "هل عندكم وثائق سلسلة التبريد للأدوية؟",
        "a": "لا نغطي قطاع الأدوية حاليًا — هذا خارج نطاق ترخيصنا كناقل، لا مجرد قرار تسويقي. نركّز على الأغذية.",
    },
    {
        "q": "أنا مشترك في خطة وجبات صحية، تقدرون توصلون وجباتي أنا شخصيًا؟",
        "a": "لا نتعاقد مباشرة مع أفراد — نتعاقد مع المطعم الصحي أو مزوّد خدمة الوجبات نفسه لخدمة خط توصيل كامل لمشتركيه. اسأل مزوّد وجبتك إن كان يتعامل مع Smart Field.",
    },
    {
        "q": "أنا صاحب مطعم صحي/مزوّد اشتراك وجبات، كيف تسعّرون خدمة التوصيل؟",
        "a": "نسعّر بالخط لا بالمشترك — أرسل عدد المشتركين ونطاقهم الجغرافي (حي واحد أفضل) ونجهّز لك عرض Meal Run رسمي.",
    },
    {
        "q": "هل يمكن عقد شهري؟",
        "a": "بالتأكيد — العقود الشهرية والسنوية توفر خصمًا يُحدَّد بعد التقييم، وهي أيضًا الحد الأدنى للالتزام المطلوب قبل تجهيز أي مركبة.",
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
        "a": "بعد توقيع العقد نجهّز المركبة المناسبة — المدة تُحدَّد عند التعاقد ونوضّحها لك قبل التوقيع.",
    },
]

# ─── Case Studies ─────────────────────────────────────────────────────────────
# Removed 2026-08-09: these three entries (poultry factory, pharma company,
# restaurant chain — 42k/28k/35k SAR monthly) did not correspond to any real
# client in the pipeline and were being presented to prospects as social
# proof. Do not re-add invented clients/results/values here — only use real,
# named case studies once Smart Field has them.
CASE_STUDIES: list[dict] = []

# ─── Knowledge Base as formatted text (for Claude prompt injection) ───────────

def get_kb_text() -> str:
    """Return a concise knowledge base string to inject into Claude's system prompt."""
    routes_text = "\n".join(
        f"  {k.replace('_', '→')}: {v['distance_km']} كم (~{v['est_time_hrs']} ساعة)"
        for k, v in COVERAGE["routes"].items()
    )
    faq_text = "\n".join(
        f"  س: {f['q']}\n  ج: {f['a']}"
        for f in FAQ[:5]
    )
    vehicles_text = "\n".join(
        f"  {v['name_ar']}: حتى {v['capacity_kg']} كغ"
        for v in VEHICLES.values()
    )
    meal_run_text = "\n".join(
        f"  {t['range_ar']}: {t['sar_per_day'] + ' ريال/يوم' if t['sar_per_day'] else 'يُسعَّر كخطين منفصلين'} — {t['note']}"
        for t in MEAL_RUN_PRICING["tiers"]
    )

    return f"""
## معلومات Smart Field (للرجوع إليها في المحادثات)

### الخدمات
- نقل مبرد: -2°C إلى +8°C (لحوم، ألبان، خضار، سمك)
- نقل مجمد: -18°C إلى -25°C (لحوم مجمدة، آيسكريم)
- Meal Run: خط توزيع لمطعم صحي أو مزوّد خدمة اشتراك وجبات — راجع القسم أدناه، شرط حاسم قبل أي رد
- أسطول يُجهَّز عند التعاقد — لا يوجد أسطول جاهز حاليًا

### أنواع المركبات المتاحة عند التعاقد (بدون أسعار — تُحدَّد لكل عميل)
{vehicles_text}

### المسارات الرئيسية (مسافة/وقت فقط — لا أسعار تقديرية)
{routes_text}

### شرط أساسي قبل أي عرض
{PRICING["commitment_required"]}

### Meal Run — قواعد حاسمة (معظم الطلبات الحالية من هذا النوع)
{MEAL_RUN_PRICING["rule"]}
تصنيف الطلب: هل التعاقد مع **منشأة** — مطعم صحي أو مزوّد خدمة وجبات (مقبول) — أم **فرد واحد** يطلب توصيل وجباته الخاصة (مرفوض — وجّهه لمزوّد وجبته)؟
جدول الأسعار الداخلي (لا تذكر رقمًا للعميل مباشرة — للفريق فقط):
{meal_run_text}

### أبرز الأسئلة والأجوبة
{faq_text}

### ملاحظة
لا تذكر أبدًا رقمًا أو نطاق سعر محدد في المحادثة، ولا تعد بمركبة جاهزة أو بدء فوري.
السعر الدقيق ومدة التجهيز تصدر فقط عبر عرض رسمي بعد جمع تفاصيل الشحنة، ويشترط
حد أدنى للالتزام (اشتراك شهري متجدد أو 3 أشهر) قبل تجهيز أي مركبة. الأدوية
والمستلزمات الطبية محظورة قانونيًا (لا ترخيص ناقل SFDA) — ارفضها دائمًا بأدب.
""".strip()


def get_scoring_context() -> str:
    """Return scoring weights context for the agent.

    Pharma/pharmacy was removed 2026-08-09 — legally prohibited (no SFDA
    carrier license), matching processors/icp_engine.py, which excludes
    pharma_beauty from ICP scoring entirely regardless of any other factor.
    Meal Run (meal_subscription) added 2026-08-09 — officially approved B2B
    segment 2026-08-04, broadened 2026-08-09: the primary/base definition is
    a healthy restaurant or meal-service provider (not narrowly "a
    subscription company") with more than one subscriber of its own; only
    counts if the contract is with that business, never a single individual
    subscriber.
    """
    return """
## جدول التقييم — Rule-Based Score

| العامل | النقاط |
|---|---|
| Meal Run (عقد B2B مع مطعم صحي أو مزوّد خدمة وجبات، خط بمشتركين مُجمَّعين) | 23 |
| مجمدات / لحوم | 22 |
| أغذية طازجة | 20 |
| سلاسل تجزئة | 16 |
| مطابخ سحابية | 15 |
| في مدينة رئيسية | 10 |
| أكثر من 10 شاحنات | +20 |
| طلب عاجل | +15 |
| ميزانية >50k ريال | +25 |
| أدوية/صيدليات (أي سياق) | 0 دائمًا — محظور قانونيًا |

الأولوية: HIGH ≥70 | MEDIUM 40-69 | LOW <40
""".strip()
