"""
تهيئة مشتركة للاختبارات.

المبدأ: لا اختبار يلمس الشبكة أو قاعدة بيانات الإنتاج. كل ما يحتاج
تخزينًا يعمل على SQLite مؤقّتة تُنشأ وتُهدم مع كل اختبار.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# اجعل جذر المشروع قابلًا للاستيراد بلا تثبيت الحزمة
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def temp_memory_db(tmp_path, monkeypatch):
    """
    وجّه ذاكرة الوكيل إلى قاعدة SQLite مؤقّتة وأنشئ جداولها.

    employee.memory يثبّت المسار في ثابت على مستوى الوحدة، فالتوجيه
    يتم بـ monkeypatch عليه مباشرةً. بدون هذا تكتب الاختبارات في
    data/smartfield_memory.db الحقيقية.
    """
    import employee.memory as memory

    db_path = tmp_path / "test_memory.db"
    monkeypatch.setattr(memory, "DB_PATH", db_path)
    memory.init_db()
    return db_path


@pytest.fixture
def make_lead():
    """مصنع سجلات عملاء بالشكل الذي تُعيده Supabase (dict لا نموذج)."""

    def _make(segment=None, icp_score=0, score=0, phone="+966500000001", **extra):
        row = {
            "id": extra.pop("id", f"lead-{segment}-{icp_score}-{score}"),
            "name": extra.pop("name", "عميل اختبار"),
            "phone": phone,
            "icp_segment": segment,
            "icp_score": icp_score,
            "score": score,
        }
        row.update(extra)
        return row

    return _make
