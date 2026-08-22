"""
عقد المخطط: كل حقل يكتبه الكود يجب أن يوجد عمودًا في قاعدة البيانات.

الخلفية: محرّك ICP كان يحسب التصنيف لكل عميل منذ إنشائه، ثم يُهمَل
بصمت — لأن `_lead_to_row` لم تكن تمرّره أصلًا، والعمود لم يكن موجودًا.
بلا خطأ ولا تحذير: تحذير 'Migration columns missing' لم يظهر في السجل
ولا مرة واحدة. الأمر لم يُكتشف إلا بسؤال عابر بعد شهور.

هذا الاختبار يقرأ الطرفين — ما يكتبه الكود، وما يعرّفه المخطط — ويقارن.
لا يحتاج شبكة ولا قاعدة بيانات.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "deploy" / "supabase_schema.sql"


def _schema_columns(table: str) -> set[str]:
    """استخرج أسماء أعمدة جدول من ملف المخطط."""
    sql = SCHEMA.read_text(encoding="utf-8")
    m = re.search(
        rf"CREATE TABLE IF NOT EXISTS {table}\s*\((.*?)\n\);",
        sql,
        re.S | re.I,
    )
    assert m, f"لم يُعثر على تعريف جدول {table} في المخطط"

    columns = set()
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("--"):
            continue
        # تخطَّ قيود الجدول (PRIMARY KEY, UNIQUE, CHECK, FOREIGN KEY…)
        if re.match(r"(PRIMARY|UNIQUE|CHECK|FOREIGN|CONSTRAINT)\b", line, re.I):
            continue
        name = line.split()[0].strip(",")
        if name.isidentifier():
            columns.add(name)
    return columns


LEADS_COLUMNS = _schema_columns("leads")


def test_schema_parsing_works():
    """حارس للاختبار نفسه: تحليل فارغ يجعل بقية الفحوص تمرّ بلا معنى."""
    assert len(LEADS_COLUMNS) >= 30
    for essential in ("id", "name", "phone", "status"):
        assert essential in LEADS_COLUMNS


def test_every_written_field_exists_as_a_column():
    """
    كل مفتاح تُنتجه `_lead_to_row` لا بد أن يكون عمودًا.

    هذا هو الفحص الذي كان سيمنع ضياع ICP لشهور.
    """
    from crm.supabase_crm import _lead_to_row
    from models.lead import Lead

    lead = Lead(name="عميل اختبار", phone="+966500000001")
    written = set(_lead_to_row(lead).keys())

    missing = written - LEADS_COLUMNS
    assert not missing, (
        f"الكود يكتب حقولًا غير معرَّفة في المخطط: {sorted(missing)}\n"
        f"إمّا أن تُضاف إلى deploy/supabase_schema.sql، أو تُحذف من _lead_to_row.\n"
        f"تركها كما هي يعني أنها تُهمَل بصمت عند الحفظ."
    )


def test_icp_fields_are_actually_written():
    """
    انحدار مباشر: حقول ICP تصل فعلًا إلى صفّ قاعدة البيانات.

    وجودها في نموذج Lead لا يكفي — كانت موجودة فيه طوال الوقت بينما
    `_lead_to_row` تتجاهلها.
    """
    from crm.supabase_crm import _lead_to_row
    from models.lead import Lead

    lead = Lead(name="مخبز", phone="+966500000002")
    lead.icp_score = 80
    lead.icp_segment = "premium_fb"
    lead.buying_signals = ["إشارة"]

    row = _lead_to_row(lead)
    assert row["icp_score"] == 80
    assert row["icp_segment"] == "premium_fb"
    assert row["buying_signals"] == ["إشارة"]


@pytest.mark.parametrize("column", ["icp_score", "icp_segment", "buying_signals"])
def test_icp_columns_are_declared(column):
    """المخطط المرفوع في الريبو يعرّف أعمدة ICP."""
    assert column in LEADS_COLUMNS


def test_migration_fallback_covers_optional_columns_only():
    """
    الحقول التي يحذفها الـ fallback عند غياب العمود يجب أن تكون معرَّفة
    في المخطط.

    الـ fallback موجود ليعبر فترة الترحيل، لا ليصير الوضع الدائم — وهو
    ما حدث فعلًا: أخفى غياب أعمدة ICP شهورًا.
    """
    from crm.supabase_crm import SupabaseCRM

    undeclared = set(SupabaseCRM._MIGRATION_FIELDS) - LEADS_COLUMNS
    assert not undeclared, (
        f"حقول يُسقطها الـ fallback ولم تُعرَّف في المخطط: {sorted(undeclared)}"
    )


def test_lead_model_and_row_agree_on_icp():
    """
    ما يحسبه محرّك ICP يصل إلى الصف كما هو.

    يغطّي السلسلة كاملةً: التصنيف ← النموذج ← صفّ قاعدة البيانات.
    """
    from crm.supabase_crm import _lead_to_row
    from models.lead import Lead
    from processors.icp_engine import enrich_lead_with_icp

    lead = Lead(
        name="مخبز وكافيه",
        phone="+966500000003",
        raw_data={"gemini_category": "مخبز وكافيه", "rating": 4.6, "review_count": 120},
    )
    enriched = enrich_lead_with_icp(lead)
    row = _lead_to_row(enriched)

    assert row["icp_segment"] == enriched.icp_segment == "premium_fb"
    assert row["icp_score"] == enriched.icp_score > 0
