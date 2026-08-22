"""
اختبارات تطبيع أرقام الهواتف.

كل حالة هنا مأخوذة من بيانات الإنتاج الفعلية (2026-08-22). النظام كان
يحاول إرسال رسائل واتساب إلى معرّفات داخلية ظنًّا أنها أرقام هواتف:
481 محاولة فاشلة تكرّرت كل ساعتين منذ يونيو.
"""

from __future__ import annotations

import pytest

from processors.followup_engine import _normalize_wa_phone


# ── الحالات التي فشلت فعليًا في الإنتاج ───────────────────────────────
# القيم مأخوذة حرفيًا من جدول lead_profiles.
PRODUCTION_FAILURES = [
    ("+",                    "الشرطة وحدها — 375 محاولة فاشلة"),
    ("141476642177168@lid",  "معرّف LID — 106 محاولة فاشلة"),
    ("+120363030037973026",  "مجموعة واتساب (كل المجموعات تبدأ بـ 120363)"),
    ("+120363181895608244",  "مجموعة واتساب أخرى"),
    ("+status",              "بث الحالة status@broadcast"),
    ("194124032528459@lid",  "معرّف LID ثانٍ"),
    ("+109757922828432",     "معرّف داخلي بـ15 رقمًا"),
    ("+29738521358364",      "معرّف داخلي بـ14 رقمًا"),
    ("+88666043088926",      "معرّف داخلي، بادئة غير معروفة"),
]


@pytest.mark.parametrize("raw,reason", PRODUCTION_FAILURES)
def test_rejects_production_failures(raw, reason):
    """كل معرّف داخلي رُصد في الإنتاج يجب أن يُرفض."""
    assert _normalize_wa_phone(raw) == "", f"كان يجب رفضه: {reason}"


# ── أرقام صحيحة يجب ألّا تُرفض ────────────────────────────────────────
VALID_NUMBERS = [
    ("+966550000001",    "+966550000001", "سعودي دولي"),
    ("966501234567",     "+966501234567", "سعودي بلا +"),
    ("+966 50 123 4567", "+966501234567", "سعودي بمسافات"),
    ("+966-50-123-4567", "+966501234567", "سعودي بشرطات"),
    ("+971501234567",    "+971501234567", "إماراتي"),
    ("+14155238886",     "+14155238886",  "أمريكي"),
    ("+201001234567",    "+201001234567", "مصري"),
]


@pytest.mark.parametrize("raw,expected,desc", VALID_NUMBERS)
def test_accepts_valid_numbers(raw, expected, desc):
    """الرقم الصحيح يُقبل ويُطبَّع إلى E.164."""
    assert _normalize_wa_phone(raw) == expected, desc


# ── حالات حدّية ───────────────────────────────────────────────────────
@pytest.mark.parametrize("raw", ["", None, "   ", "abc", "()-+", "0501234567"])
def test_rejects_unusable_input(raw):
    """
    فارغ، None، نص، أو رقم محلّي يبدأ بصفر — كلها غير صالحة للواتساب.

    الصيغة المحلّية (05...) مرفوضة عمدًا: لا يمكن استنتاج رمز الدولة
    بثقة، وتخمينه يعني إرسال رسالة إلى شخص آخر تمامًا.
    """
    assert _normalize_wa_phone(raw) == ""


def test_normalization_is_idempotent():
    """تطبيع رقم مطبَّع مسبقًا لا يغيّره — شرط لأمان إعادة المعالجة."""
    once = _normalize_wa_phone("+966 50 123 4567")
    assert _normalize_wa_phone(once) == once


def test_no_valid_number_is_silently_altered():
    """
    الأرقام الصحيحة تحتفظ بكل خاناتها.

    يحرس ضد خطأ ينشأ لو ضُبطت حدود الطول لاحقًا: بتر خانة يعني رقمًا
    صالح الشكل لشخص مختلف — وهو أسوأ من الرفض الصريح.
    """
    for raw, expected, _ in VALID_NUMBERS:
        digits_in = "".join(c for c in raw if c.isdigit())
        digits_out = "".join(c for c in _normalize_wa_phone(raw) if c.isdigit())
        assert digits_out == digits_in
