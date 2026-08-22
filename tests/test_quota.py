"""
اختبارات توزيع الحصة اليومية بين الشرائح.

قرار تجاري (2026-08-22): مقدّمو الوجبات الصحية والاشتراكات الشهرية هم
العميل المستهدف الأول، مع حصة محفوظة لبقية الشرائح حتى لا تُجوَّع.

الخطر الذي تحرسه هذه الاختبارات: أي حصة تُطبَّق بسذاجة تُهدر مقاعد حين
تشحّ إحدى الفئتين، فتتقلّص الدفعة اليومية بلا سبب.
"""

from __future__ import annotations

from collections import Counter

import pytest

from processors.icp_engine import (
    ICP_PRIORITY,
    PRIMARY_SEGMENTS,
    PRIMARY_SHARE,
    allocate_by_quota,
    lead_priority_key,
)

CAP = 10


def _pool(make_lead, segment, n, base=90):
    return [make_lead(segment=segment, icp_score=base - i, score=base - i) for i in range(n)]


def _mix(batch):
    return Counter(str(l.get("icp_segment")) for l in batch)


# ── لا تضيع مقاعد ────────────────────────────────────────────────────
def test_abundant_pools_respect_the_share(make_lead):
    """مع وفرة الطرفين، التوزيع يتبع PRIMARY_SHARE."""
    leads = _pool(make_lead, "meal_subscription", 20) + _pool(make_lead, "premium_fb", 50)
    batch = allocate_by_quota(leads, CAP)
    mix = _mix(batch)
    assert len(batch) == CAP
    assert mix["meal_subscription"] == round(CAP * PRIMARY_SHARE)


def test_scarce_primary_is_backfilled(make_lead):
    """
    عميلان فقط في الشريحة الأولى ⇒ بقية الشرائح تأخذ المقاعد الشاغرة.

    هذه هي الحالة الفعلية اليوم (2 من 337)، وبدون الردم كانت الدفعة
    ستنكمش إلى عميلين بدل عشرة.
    """
    leads = _pool(make_lead, "meal_subscription", 2) + _pool(make_lead, "premium_fb", 50)
    batch = allocate_by_quota(leads, CAP)
    assert len(batch) == CAP
    assert _mix(batch)["meal_subscription"] == 2


def test_absent_primary_gives_all_seats_to_others(make_lead):
    leads = _pool(make_lead, "premium_fb", 30) + _pool(make_lead, "horeca", 30)
    batch = allocate_by_quota(leads, CAP)
    assert len(batch) == CAP
    assert _mix(batch)["meal_subscription"] == 0


def test_only_primary_takes_every_seat(make_lead):
    batch = allocate_by_quota(_pool(make_lead, "meal_subscription", 30), CAP)
    assert len(batch) == CAP
    assert _mix(batch)["meal_subscription"] == CAP


def test_pool_smaller_than_cap_returns_everything(make_lead):
    leads = _pool(make_lead, "meal_subscription", 1) + _pool(make_lead, "premium_fb", 2)
    batch = allocate_by_quota(leads, CAP)
    assert len(batch) == 3


@pytest.mark.parametrize(
    "n_primary,n_other",
    [(0, 0), (0, 5), (5, 0), (1, 1), (100, 100), (3, 7), (7, 3)],
)
def test_never_wastes_a_seat(make_lead, n_primary, n_other):
    """
    الثابت المحوري: حجم الدفعة = min(السقف، عدد المتاحين).

    لو انكسر هذا، تنكمش الدفعة اليومية بصمت — وهو بالضبط نوع الفشل
    الذي لا يظهر في أي سجل.
    """
    leads = _pool(make_lead, "meal_subscription", n_primary) + _pool(make_lead, "premium_fb", n_other)
    batch = allocate_by_quota(leads, CAP)
    assert len(batch) == min(CAP, n_primary + n_other)


def test_empty_input_and_zero_cap(make_lead):
    assert allocate_by_quota([], CAP) == []
    assert allocate_by_quota(_pool(make_lead, "meal_subscription", 5), 0) == []


# ── الترتيب ──────────────────────────────────────────────────────────
def test_primary_segment_leads_the_batch(make_lead):
    """الشريحة الأولى تتصدّر الناتج حتى لو كانت نتيجتها أدنى."""
    leads = _pool(make_lead, "meal_subscription", 3, base=40) + _pool(make_lead, "premium_fb", 20, base=100)
    batch = allocate_by_quota(leads, CAP)
    assert str(batch[0].get("icp_segment")) == "meal_subscription"


def test_priority_key_orders_segments_then_score(make_lead):
    """الشريحة تسبق الرقم — عميل Meal Run بـ60 يسبق premium_fb بـ100."""
    meal = make_lead(segment="meal_subscription", icp_score=60, score=60)
    prem = make_lead(segment="premium_fb", icp_score=100, score=100)
    assert sorted([prem, meal], key=lead_priority_key)[0] is meal


def test_not_icp_sorts_last(make_lead):
    """العملاء خارج النطاق آخر الطابور دائمًا."""
    rows = [
        make_lead(segment="not_icp", icp_score=100, score=100),
        make_lead(segment="horeca", icp_score=10, score=10),
    ]
    assert str(sorted(rows, key=lead_priority_key)[-1]["icp_segment"]) == "not_icp"


def test_unknown_segment_sorts_before_not_icp(make_lead):
    """
    عميل بلا تصنيف (بيانات قديمة) يسبق not_icp.

    'لم يُصنَّف بعد' ليس 'صُنِّف وسقط' — الخلط بينهما يدفن سجلات قديمة
    صالحة إلى ذيل الطابور.
    """
    unknown = make_lead(segment=None, icp_score=0, score=50)
    rejected = make_lead(segment="not_icp", icp_score=0, score=50)
    assert sorted([rejected, unknown], key=lead_priority_key)[0] is unknown


# ── سلامة الإعداد ────────────────────────────────────────────────────
def test_configuration_is_coherent():
    """الشريحة الأولى معرَّفة، ولها أعلى أولوية، والحصة ضمن المدى."""
    assert PRIMARY_SEGMENTS, "لا بد من تعريف شريحة أولى واحدة على الأقل"
    assert 0.0 < PRIMARY_SHARE <= 1.0
    for segment in PRIMARY_SEGMENTS:
        assert ICP_PRIORITY[segment] == min(ICP_PRIORITY.values())
