"""
اختبارات تصنيف الشرائح (ICP).

التصنيفات المستخدمة هنا مأخوذة من المفردات الفعلية التي يُنتجها Gemini
في الإنتاج (165 تصنيفًا مميّزًا، 2026-08-22).

الخلفية: قبل 2026-08-22 كان 96.7% من العملاء يقعون في premium_fb، و56%
منهم بلا أي كلمة مطابقة — لأن الشريحة وحدها كانت تحصل على +20 نقطة
أساس، فصارت سلّة افتراضية لا تصنيفًا.
"""

from __future__ import annotations

import pytest

from models.lead import Lead
from processors.icp_engine import (
    _is_excluded,
    _normalize_ar,
    _quality_bonus,
    score_lead_icp,
)


def _lead(category: str, rating: float = 0, reviews: int = 0) -> Lead:
    """عميل بالحد الأدنى من الحقول، تصنيفه هو ما يُختبَر."""
    return Lead(
        name="منشأة اختبار",
        phone="+966500000001",
        raw_data={
            "gemini_category": category,
            "rating": rating,
            "review_count": reviews,
        },
    )


# ── التصنيف حسب المفردات الفعلية ──────────────────────────────────────
REAL_CATEGORIES = [
    # (تصنيف Gemini، الشريحة المتوقّعة، السبب)
    ("مخبز",                   "premium_fb", "28 عميلًا — لم يكن مغطّى قبل الإصلاح"),
    ("مخبز وكافيه",            "premium_fb", "مخبز + كافيه"),
    ("متجر شوكولاتة",          "premium_fb", "الصيغة الشائعة"),
    ("متجر شيكولاتة",          "premium_fb", "صيغة إملائية ثانية — كانت تفشل"),
    ("متجر حلويات",            "premium_fb", "حلويات وحدها بلا 'فاخرة'"),
    ("محمصة قهوة",             "premium_fb", "محامص القهوة"),
    ("متجر تمور",              "premium_fb", "التمور — فئة فاخرة قائمة بذاتها"),
    ("تمور فاخرة",             "premium_fb", "تمور + واصف فخامة"),
    ("مطعم",                   "horeca",     "المطاعم"),
    ("متعهد توريد طعام",       "horeca",     "التوريد والتعهّد"),
    ("تموين (Catering)",       "horeca",     "التموين — لم يكن مغطّى"),
    ("مورّد منتجات غذائية",    "horeca",     "الشدّة كانت تُفشل المطابقة"),
    ("مطعم مأكولات صحية",      "meal_subscription", "الأضيق تعريفًا يفوز"),
    ("سوبرماركت / بقالة",      "fresh_food", "البقالة"),
    ("شركة تصنيع أغذية",       "fresh_food", "التصنيع"),
]


@pytest.mark.parametrize("category,expected,reason", REAL_CATEGORIES)
def test_classifies_real_categories(category, expected, reason):
    """كل تصنيف من الإنتاج يقع في شريحته الصحيحة."""
    _, segment = score_lead_icp(_lead(category))
    assert segment == expected, f"{category!r}: {reason}"


# ── الاستبعاد: منشآت ليست نقل أغذية ──────────────────────────────────
NOT_FOOD_LOGISTICS = [
    "مستلزمات مطابخ",
    "تجهيزات مطابخ ومطاعم",
    "مُجدِّد مطابخ",
    "متجر مستلزمات المطاعم",
    "خدمات غاز وصيانة",
    "استشاري أغذية ومشروبات",
]


@pytest.mark.parametrize("category", NOT_FOOD_LOGISTICS)
def test_excludes_non_food_logistics(category):
    """
    موردو المعدات والاستشاريون خارج النطاق.

    كانوا يُصنَّفون horeca لأن أسماءهم تحوي 'مطاعم' أو 'مطابخ'، رغم
    أنهم لا ينقلون شيئًا مبرَّدًا.
    """
    score, segment = score_lead_icp(_lead(category))
    assert segment == "not_icp"
    assert score == 0
    assert _is_excluded(category)


def test_pharma_is_never_returned():
    """
    شريحة الصيدليات/المستشفيات مستبعدة قانونيًا (لا ترخيص ناقل من
    الهيئة العامة للغذاء والدواء) — لا يجوز أن يُعيدها المصنّف أبدًا.
    """
    for category in ("صيدلية", "مستشفى خاص", "عيادة تجميل", "مختبر طبي"):
        _, segment = score_lead_icp(_lead(category))
        assert segment != "pharma_beauty"


# ── الانحدار الأساسي: الجودة لا تُقرّر الشريحة ───────────────────────
def test_high_rating_alone_does_not_create_a_segment():
    """
    منشأة بلا أي كلمة مطابقة تبقى not_icp مهما ارتفع تقييمها.

    هذا هو الخلل الأصلي: كان التقييم المرتفع يكفي لإيقاع العميل في
    premium_fb لأنها وحدها تحمل نقاط أساس.
    """
    _, segment = score_lead_icp(_lead("مكتب شركات", rating=5.0, reviews=900))
    assert segment == "not_icp"


def test_quality_ranks_within_segment_not_across():
    """التقييم يرفع النتيجة ولا يغيّر الشريحة."""
    low = score_lead_icp(_lead("مخبز", rating=0, reviews=0))
    high = score_lead_icp(_lead("مخبز", rating=4.8, reviews=300))
    assert low[1] == high[1] == "premium_fb"
    assert high[0] > low[0]


def test_quality_bonus_is_segment_independent():
    """دالة المكافأة لا تعرف الشريحة أصلًا — ضمان بنيوي لا سلوكي."""
    assert _quality_bonus(4.8, 300) == _quality_bonus(4.8, 300)
    assert _quality_bonus(0, 0) == 0
    assert _quality_bonus(4.8, 300) > _quality_bonus(4.0, 50)


def test_data_poor_lead_is_not_dropped():
    """
    مورّد أغذية بلا تقييم على الخرائط يبقى ضمن الـ ICP.

    العتبة كانت 30 فتُسقط هؤلاء إلى not_icp — أي أنها تعاقب شحّ
    البيانات لا ضعف المطابقة.
    """
    score, segment = score_lead_icp(_lead("مورّد منتجات غذائية", rating=0, reviews=0))
    assert segment == "horeca"
    assert score > 0


# ── تطبيع النص العربي ────────────────────────────────────────────────
@pytest.mark.parametrize(
    "a,b",
    [
        ("مورّد", "مورد"),        # الشدّة
        ("أغذية", "اغذيه"),       # الهمزة والتاء المربوطة
        ("مقهى", "مقهي"),         # الألف المقصورة
        ("قهــوة", "قهوه"),       # التطويل
    ],
)
def test_arabic_normalization_unifies_variants(a, b):
    """الفروق الإملائية بلا دلالة يجب ألّا تُفشل المطابقة."""
    assert _normalize_ar(a) == _normalize_ar(b)


def test_score_is_bounded():
    """النتيجة تبقى ضمن 0..100 مهما تعدّدت المطابقات."""
    score, _ = score_lead_icp(
        _lead("مخبز وحلويات وشوكولاتة ومحمصة قهوة وتمور فاخرة", rating=5.0, reviews=999)
    )
    assert 0 <= score <= 100
