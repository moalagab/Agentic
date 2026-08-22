"""
ICP Engine — Ideal Customer Profile Scoring
يصنف كل عميل على 4 شرائح ICP ويعطيه نقاط 0-100
(pharma_beauty استُبعد نهائيًا 2026-08-09 — محظور قانونيًا، لا ترخيص ناقل
SFDA؛ meal_subscription أُضيف 2026-08-09 — قطاع Meal Run المعتمد رسميًا)
"""

from __future__ import annotations

import re
from typing import Any

from models.lead import BuyingSignal, ICPSegment, Lead, LeadCategory


# ═══════════════════════════════════════════════════════════════════════
# تطبيع النص العربي قبل المطابقة
# ═══════════════════════════════════════════════════════════════════════
# بدونه تفشل مطابقات حقيقية بفروق إملائية لا دلالة لها: "مورّد" بالشدّة
# لا تطابق "مورد" (9+8 عملاء)، و"أغذية" بالهمزة لا تطابق "اغذية".
_AR_DIACRITICS = re.compile(r"[\u064B-\u0652\u0640]")


def _normalize_ar(text: str) -> str:
    """وحّد الشكل الإملائي: حذف التشكيل والتطويل، وتوحيد الألف والياء والتاء."""
    t = _AR_DIACRITICS.sub("", str(text or "").lower())
    for src, dst in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ى", "ي"), ("ة", "ه")):
        t = t.replace(src, dst)
    return t


# ═══════════════════════════════════════════════════════════════════════
# استبعاد: منشآت ليست أغذية أصلًا
# ═══════════════════════════════════════════════════════════════════════
# موردو معدات المطابخ ومجدّدوها ظهروا في البيانات (~15 عميلًا) وكانوا
# يُصنَّفون HoReCa لأن أسماءهم تحوي "مطاعم"/"مطابخ". لا ينقلون شيئًا
# مبرَّدًا — استبعادهم أدقّ من ترتيبهم في ذيل القائمة.
_EXCLUDE_KEYWORDS = [
    "مستلزمات مطابخ", "مستلزمات المطابخ", "مستلزمات المطاعم",
    "مجدد مطابخ", "معدات مطابخ", "معدات المطاعم", "دواليب",
    "اثاث", "تجهيزات محلات",
]

# ═══════════════════════════════════════════════════════════════════════
# كلمات الشرائح — مبنية على المفردات الفعلية في البيانات (165 تصنيفًا)
# ═══════════════════════════════════════════════════════════════════════
_PREMIUM_FB_KEYWORDS = [
    # مخابز وحلويات — أكبر مجموعة فعلية في البيانات
    "مخبز", "مخابز", "bakery", "معجنات", "حلويات", "حلا", "كيك", "cake",
    "patisserie", "بيتسري", "باتيسري", "confectionery",
    # شوكولاتة — بصيغتيها الشائعتين
    "شوكولاته", "شيكولاته", "chocolate",
    # قهوة ومحامص
    "محمصه", "محامص", "قهوه", "coffee", "كافيه", "cafe", "café", "مقهي",
    "specialty", "سبيشلتي", "roastery",
    # تمور — فئة فاخرة قائمة بذاتها في السوق السعودي
    "تمور", "تمر", "dates",
    # مطابخ سحابية ومركزية
    "مطبخ سحابي", "مطبخ مركزي", "cloud kitchen",
    # مثلجات
    "ايس كريم", "ice cream", "جيلاتو", "gelato", "بوظه", "dessert",
    # واصفات الفخامة
    "فاخر", "فاخره", "راقي", "gourmet", "artisan", "premium",
]

_FRESH_FOOD_KEYWORDS = [
    "عضوي", "organic", "طازج", "fresh", "لحوم", "لحم", "meat",
    "دجاج", "chicken", "مأكولات بحريه", "بحريه", "seafood", "سمك", "fish",
    "خضار", "خضروات", "vegetables", "فواكه", "فاكهه", "fruits",
    "فواكه مجففه", "بقاله", "سوبرماركت", "supermarket", "grocery",
    "البان", "dairy", "اجبان", "مزرعه", "farm",
    "مواد غذائيه", "منتجات غذائيه", "اغذيه طبيعيه",
]

# Meal Run — مطاعم صحية ومزوّدو وجبات دايت لديهم مشتركون.
# العقد والفوترة مع المنشأة وحدها؛ بيوت المشتركين محطات على خط واحد.
_MEAL_SUBSCRIPTION_KEYWORDS = [
    "دايت", "diet", "اشتراك وجبات", "اشتراكات", "مشتركين", "subscribers",
    "meal subscription", "meal plan", "meal prep", "خطه غذائيه", "نظام غذائي",
    "سعرات", "calorie", "كيتو", "keto", "لوكارب", "low carb",
    "فيت فود", "fit food", "fitness meals", "healthy meals",
    "وجبات صحيه", "مأكولات صحيه", "مطعم صحي", "صحيه", "healthy",
    "تخسيس", "weight loss", "clean eating", "توصيل وجبات", "meal delivery",
]

_HORECA_KEYWORDS = [
    "فندق", "hotel", "منتجع", "resort", "ضيافه", "hospitality",
    "مطعم", "مطاعم", "restaurant",
    # التوريد والتموين — ظهرت بكثرة في البيانات ولم تكن مغطاة
    "مورد", "موردين", "توريد", "تموين", "متعهد", "catering",
    "supplier", "distributor", "موزع", "banquet", "حفلات",
    "food service", "كانتين", "بوفيه",
]

# محفوظة للتوثيق فقط — الشريحة مستبعدة قانونيًا ولا تدخل التقييم
_PHARMA_BEAUTY_KEYWORDS = [
    "صيدليه", "pharmacy", "pharma", "دواء", "ادويه", "medicine",
    "مستشفي", "hospital", "clinic", "عياده", "مختبر",
    "cosmetics", "مستحضرات", "skincare", "مكملات", "طبي", "vaccine",
]


def _text_hits(text: str, keywords: list[str]) -> int:
    """عدد الكلمات المفتاحية الموجودة في النص، بعد تطبيع الطرفين."""
    t = _normalize_ar(text)
    return sum(1 for k in keywords if _normalize_ar(k) in t)


def _is_excluded(text: str) -> bool:
    """True إذا كانت المنشأة خارج نطاق نقل الأغذية أصلًا."""
    return _text_hits(text, _EXCLUDE_KEYWORDS) > 0


def _quality_bonus(rating: float, review_count: int) -> int:
    """
    مكافأة الجودة — تُطبَّق **بعد** اختيار الشريحة وبالتساوي عليها جميعًا.

    التصميم السابق كان يمنح كل شريحة مكافآت مختلفة (ونقاط أساس لـ
    premium_fb وحدها)، فصارت الجودة تُقرِّر *أي* شريحة يقع فيها العميل
    لا *مدى* جودته داخلها. النتيجة: 56% من العملاء صُنِّفوا premium_fb
    بلا أي كلمة مطابقة، لمجرد أن تقييمهم على الخرائط مرتفع.
    """
    bonus = 0
    if rating >= 4.5:
        bonus += 25
    elif rating >= 4.0:
        bonus += 15
    elif rating >= 3.5:
        bonus += 5
    if review_count >= 100:
        bonus += 15
    elif review_count >= 30:
        bonus += 8
    return bonus


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
    if _text_hits(name, _MEAL_SUBSCRIPTION_KEYWORDS) >= 1:
        signals.append(BuyingSignal.MEAL_SUBSCRIPTION_KEYWORDS.value)

    # Recently opened — if raw_data has opened_recently flag
    if raw.get("recently_opened") or raw.get("opened_recently"):
        signals.append(BuyingSignal.RECENTLY_OPENED.value)

    return list(set(signals))


# الحد الأدنى لاعتبار العميل ضمن الـ ICP. مطابقة كلمة واحدة (20 نقطة)
# بلا أي إشارة جودة لا تكفي — العتبة تتطلب إمّا مطابقتين، أو مطابقة
# واحدة مع تقييم/مراجعات معقولة.
_MIN_ICP_SCORE = 30


def score_lead_icp(lead: Lead) -> tuple[int, str]:
    """
    أعد (icp_score 0-100، اسم الشريحة).

    المبدأ: **الكلمات المفتاحية وحدها تحدّد الشريحة**، والجودة (التقييم
    وعدد المراجعات) تعدّل النتيجة *داخل* الشريحة المختارة.

    التصميم السابق خلط الأمرين: كانت premium_fb تحصل على +20 نقطة أساس
    لا تحصل عليها أي شريحة أخرى، وكل شريحة لها مكافآت جودة مختلفة. ولأن
    الاختيار max(scores)، كان أي مطعم أو فندق بتقييم مرتفع يقع في
    premium_fb افتراضيًا — 56% من العملاء صُنِّفوا هناك بلا كلمة واحدة
    مطابقة، فصارت الشريحة سلّة افتراضية لا تصنيفًا.

    عميل لا تطابقه أي كلمة في أي شريحة يُعاد كـ not_icp: "لا نعرف" أصدق
    من نسبته لشريحة بعينها.
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
        str(category),
    ])

    # منشآت خارج نطاق نقل الأغذية (معدات مطابخ، تجهيزات محلات)
    if _is_excluded(search_text):
        return (0, ICPSegment.NOT_ICP.value)

    # pharma_beauty مستبعدة تمامًا (2026-08-09): ليست أولوية منخفضة بل
    # منع قانوني — لا ترخيص ناقل من الهيئة العامة للغذاء والدواء.
    affinity = {
        ICPSegment.MEAL_SUBSCRIPTION: _text_hits(search_text, _MEAL_SUBSCRIPTION_KEYWORDS),
        ICPSegment.PREMIUM_FB:        _text_hits(search_text, _PREMIUM_FB_KEYWORDS),
        ICPSegment.HORECA:            _text_hits(search_text, _HORECA_KEYWORDS),
        ICPSegment.FRESH_FOOD:        _text_hits(search_text, _FRESH_FOOD_KEYWORDS),
    }

    # ترتيب dict مضمون في بايثون 3.7+، وmax يُعيد أول أعلى قيمة — لذا
    # ترتيب المفاتيح أعلاه هو كاسر التعادل، مرتّبًا من الأضيق تعريفًا
    # إلى الأوسع:
    #   meal_subscription — نموذج عمل محدّد (مطعم صحي باشتراكات لا يقع
    #                        في horeca العامة)
    #   premium_fb        — فئات منتجات محدّدة (مخبز، محمصة، تمور)
    #   horeca            — نوع المنشأة (مطعم، فندق، مورّد، موزّع)
    #   fresh_food        — طبيعة البضاعة، الأوسع
    # لذلك "مورّد منتجات غذائية" يقع في horeca (موزّع) لا fresh_food،
    # و"مطعم مأكولات بحرية" يقع في horeca (مطعم) لا في بضاعته.
    best_segment = max(affinity, key=lambda seg: affinity[seg])
    hits = affinity[best_segment]

    if hits == 0:
        return (0, ICPSegment.NOT_ICP.value)

    keyword_score = min(hits * 20, 60)
    score = min(keyword_score + _quality_bonus(rating, review_count), 100)

    if score < _MIN_ICP_SCORE:
        return (score, ICPSegment.NOT_ICP.value)

    return (score, best_segment.value)


def enrich_lead_with_icp(lead: Lead) -> Lead:
    """Mutates lead in-place with ICP score, segment, and buying signals."""
    icp_score, icp_segment = score_lead_icp(lead)
    signals = detect_buying_signals(lead)

    lead.icp_score = icp_score
    lead.icp_segment = icp_segment
    lead.buying_signals = signals
    lead.update_timestamp()
    return lead
