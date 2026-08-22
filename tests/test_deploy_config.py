"""
تماسك إعدادات النشر واتّساق قرارات العمل عبر الكود.

نوعا الانحراف اللذان تحرسهما هذه الاختبارات، وكلاهما وقع فعلًا:

١. انحراف إعدادات: ملف systemd في الريبو كان يربط 0.0.0.0 بينما سكربت
   الإعداد يربط 127.0.0.1. المنشور فعليًا كان 0.0.0.0، فبقي التطبيق
   مكشوفًا مباشرةً على المنفذ 8000 متجاوزًا nginx و TLS.

٢. انحراف قرار عمل: شريحة الصيدليات استُبعدت من المصنّف في 2026-08-09
   لمنع قانوني، لكن أحدًا لم يتتبّع القرار إلى قائمة استعلامات التنقيب —
   فظلّ النظام ينفق نصف ميزانيته اليومية على شريحة لا يمكن التعاقد معها.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


# ═══ إعدادات النشر ═══════════════════════════════════════════════════
def test_service_binds_to_localhost_only():
    """
    uvicorn يربط على 127.0.0.1؛ nginx هو المنفذ العام الوحيد.

    الربط على 0.0.0.0 يكشف التطبيق مباشرةً على 8000 — وقد تحقّقنا يوم
    2026-08-22 أن المنفذ كان يستجيب فعلًا من الإنترنت.
    """
    unit = (ROOT / "deploy" / "smartfield.service").read_text(encoding="utf-8")
    assert "--host 0.0.0.0" not in unit
    assert "--host 127.0.0.1" in unit


def test_single_worker_matches_in_memory_state():
    """
    الحالة محفوظة في ذاكرة العملية (منع التكرار، الخيوط المصعَّدة)، فأي
    عامل إضافي يعني نسختين لا تريان بعضهما.

    إمّا worker واحد، أو نقل الحالة إلى مخزن مشترك — وهذا الاختبار يجعل
    ذلك قرارًا واعيًا لا مفاجأة إنتاج.
    """
    unit = (ROOT / "deploy" / "smartfield.service").read_text(encoding="utf-8")
    m = re.search(r"--workers\s+(\d+)", unit)
    assert m and m.group(1) == "1"


def test_env_example_documents_security_settings():
    """
    كل إعداد أمني له مدخل موثّق في .env.example.

    غياب المدخل يعني نشرًا بقيمة فارغة — وهو ما يعطّل الحماية بصمت.
    """
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    for key in (
        "ADMIN_API_KEY",
        "TELEGRAM_WEBHOOK_SECRET",
        "CORS_ORIGINS",
        "HEARTBEAT_URL",
    ):
        assert re.search(rf"^{key}=", example, re.M), f"{key} غير موثّق في .env.example"


def test_gitignore_covers_secrets():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert re.search(r"^\.env$", ignored, re.M)


# ═══ اتّساق قرارات العمل ═════════════════════════════════════════════
EXCLUDED_TERMS = ["صيدلي", "مستشف", "عياد", "pharmacy", "hospital", "clinic"]


@pytest.mark.parametrize("term", EXCLUDED_TERMS)
def test_prospecting_never_targets_the_excluded_segment(term):
    """
    لا استعلام تنقيب يستهدف شريحة يستبعدها المصنّف قانونيًا.

    الاستبعاد ليس تفضيلًا: لا ترخيص ناقل من الهيئة العامة للغذاء
    والدواء. إنفاق ميزانية البحث عليها يجلب عملاء لا يمكن التعاقد معهم.
    """
    from processors.serpapi_engine import SERPAPI_QUERIES

    offenders = [q for q in SERPAPI_QUERIES if term in q.lower()]
    assert not offenders, f"استعلامات تستهدف شريحة مستبعدة: {offenders}"


def test_pharma_segment_is_never_scored():
    """المصنّف لا يُعيد شريحة الصيدليات مهما كان المدخل."""
    from models.lead import Lead
    from processors.icp_engine import score_lead_icp

    for category in ("صيدلية", "مستشفى", "عيادة تجميل"):
        lead = Lead(name="س", phone="+966500000001",
                    raw_data={"gemini_category": category, "rating": 5.0})
        assert score_lead_icp(lead)[1] != "pharma_beauty"


def test_primary_segment_gets_the_larger_share_of_prospecting():
    """
    الشريحة الأولى تأخذ النصيب الأكبر من استعلامات كل دورة.

    التنقيب يأخذ أول N استعلامات فقط، فترتيب القائمة هو ما يُنقَّب عنه
    فعليًا لا مجرّد تفضيل مكتوب.
    """
    from processors.serpapi_engine import PRIMARY_QUERIES, select_queries

    chosen = select_queries(4, rotation=0)
    primary = [q for q in chosen if q in PRIMARY_QUERIES]
    assert len(primary) > len(chosen) - len(primary)


def test_query_rotation_covers_the_whole_list():
    """
    التناوب يغطّي كل الاستعلامات عبر الأيام.

    التقطيع الثابت `SERPAPI_QUERIES[:4]` كان يعني تنقيبًا في نفس
    الأربعة كل يوم إلى الأبد، فلا تُرى بقية الشرائح أبدًا.
    """
    from processors.serpapi_engine import SERPAPI_QUERIES, select_queries

    seen = set()
    for day in range(30):
        seen.update(select_queries(4, rotation=day))
    assert len(seen) >= len(SERPAPI_QUERIES) * 0.75


def test_quota_config_matches_priority_config():
    """
    الشريحة الأولى في توزيع الحصة هي نفسها الأعلى أولوية في الترتيب.

    تعريف الأولوية في مكانين يعني أن تغيير أحدهما دون الآخر يُنتج نظامًا
    يقول شيئًا ويفعل غيره.
    """
    from processors.icp_engine import ICP_PRIORITY, PRIMARY_SEGMENTS

    top = min(ICP_PRIORITY.values())
    assert {s for s, p in ICP_PRIORITY.items() if p == top} == set(PRIMARY_SEGMENTS)


# ═══ حماية السجلات ═══════════════════════════════════════════════════
def test_log_redaction_is_installed_in_the_production_entrypoint():
    """
    محو الأسرار مُثبَّت في channels/webhook_server.py لا في main.py فقط.

    الخدمة تشغّل uvicorn على الوحدة مباشرةً، فـ main.py وكل ما فيه من
    إعداد تسجيل لا يُنفَّذ في الإنتاج إطلاقًا.
    """
    src = (ROOT / "channels" / "webhook_server.py").read_text(encoding="utf-8")
    assert "logging_redaction" in src
    assert "_redaction.install()" in src


def test_redactor_removes_a_bot_token_from_an_httpx_error():
    """
    رسالة خطأ httpx تحوي الرابط كاملًا، ورابط تيليجرام يحوي التوكن.

    هكذا وصل التوكن إلى 622 سطرًا في سجل مقروء للجميع.
    """
    import logging_redaction as redaction

    token = "8755844059:AAFAKEfakeFAKEfakeFAKEfakeFAKEfake12"
    message = f"Client error '400' for url 'https://api.telegram.org/bot{token}/sendMessage'"
    cleaned = redaction.build_redactor()(None, "error", {"error": message})["error"]

    assert token not in cleaned
    assert redaction.REDACTED in cleaned


def test_redactor_leaves_ordinary_text_untouched():
    """المحو لا يشوّه رسائل بريئة — وإلا صار السجل غير مقروء."""
    import logging_redaction as redaction

    text = "أُرسلت 10 بطاقات موافقة إلى المالك"
    assert redaction.build_redactor()(None, "info", {"msg": text})["msg"] == text
