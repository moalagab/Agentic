"""
اختبارات بوّابات محرّك المتابعة: المهلة، السقف، وحدّ المحاولات.

الحادثة التي تحرس ضدها هذه الاختبارات: من 2026-06-10 إلى 2026-08-22
توقّفت كل متابعات النظام. 177 بطاقة موافقة أُرسلت ولم يُبتّ فيها، وشرط
`if pending > 0: return` بلا مهلة حوّلها إلى قفل دائم. النظام سجّل
'blocked' 866 مرة بمرح، و319 عميلًا من 336 لم يُتواصل معهم إطلاقًا.

الدرس المعمّم: كل حارس يمنع تقدّمًا يحتاج إجابة على 'ماذا لو لم يُحلّ
هذا أبدًا؟'
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from processors.followup_engine import (
    _CARD_TTL_HOURS,
    _MAX_SEND_FAILURES,
    _bump_send_failure,
    _count_pending_followup_cards,
    _queue_followup_card,
    _resolve_followup_card,
    CreativeFollowupEngine,
)


def _insert_card_at(db_path, lead_id: str, hours_ago: float, resolved: bool = False):
    """أدرج بطاقة بختم زمني محدّد — لاختبار المهلة دون انتظار حقيقي."""
    import sqlite3

    stamp = (datetime.utcnow() - timedelta(hours=hours_ago)).isoformat()
    action = "followup_card_resolved" if resolved else "followup_card_queued"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO agent_actions (action_type, description, result, lead_id, created_at)"
        " VALUES (?, 'اختبار', 'pending', ?, ?)",
        (action, lead_id, stamp),
    )
    conn.commit()
    conn.close()


# ── المهلة: الحارس لا يتحوّل إلى قفل دائم ────────────────────────────
def test_fresh_card_blocks(temp_memory_db):
    """بطاقة حديثة تحجب الدفعة التالية — السلوك المقصود."""
    _queue_followup_card("lead-1")
    assert _count_pending_followup_cards() == 1


def test_expired_card_stops_blocking(temp_memory_db):
    """
    بطاقة أقدم من المهلة تُعتبر مهجورة ولا تحجب.

    هذا هو الإصلاح المباشر للقفل: بدونه تكفي بطاقة واحدة منسيّة لتجميد
    كل المتابعات إلى الأبد.
    """
    _insert_card_at(temp_memory_db, "lead-old", hours_ago=_CARD_TTL_HOURS + 1)
    assert _count_pending_followup_cards() == 0


def test_card_just_inside_ttl_still_blocks(temp_memory_db):
    """الحد نفسه يحجب — يمنع انزياح المهلة بخطأ إشارة."""
    _insert_card_at(temp_memory_db, "lead-edge", hours_ago=_CARD_TTL_HOURS - 1)
    assert _count_pending_followup_cards() == 1


def test_the_june_deadlock_cannot_recur(temp_memory_db):
    """
    إعادة بناء الحادثة: 177 بطاقة عمرها 73 يومًا.

    بالحساب التراكمي القديم (queued − resolved) كانت النتيجة 177 إلى
    الأبد. بالمهلة يجب أن تكون صفرًا.
    """
    for i in range(177):
        _insert_card_at(temp_memory_db, f"lead-{i}", hours_ago=73 * 24)
    assert _count_pending_followup_cards() == 0


# ── المطابقة لكل بطاقة على حدة ───────────────────────────────────────
def test_resolved_card_does_not_block(temp_memory_db):
    _queue_followup_card("lead-2")
    _resolve_followup_card("lead-2")
    assert _count_pending_followup_cards() == 0


def test_resolving_one_lead_does_not_release_another(temp_memory_db):
    """
    المطابقة تتم عبر lead_id لا بطرح إجماليين.

    الطرح التراكمي القديم كان يختلّ عند أي resolved مفقود ولا يتعافى:
    بطاقة واحدة بُتّ فيها كانت 'تحرّر' بطاقة عميل آخر.
    """
    _queue_followup_card("lead-A")
    _queue_followup_card("lead-B")
    _resolve_followup_card("lead-A")
    assert _count_pending_followup_cards() == 1


def test_counter_never_goes_negative(temp_memory_db):
    """resolved زائد عن queued لا يُنتج عددًا سالبًا."""
    _resolve_followup_card("ghost")
    _resolve_followup_card("ghost")
    assert _count_pending_followup_cards() >= 0


# ── حدّ المحاولات الفاشلة ────────────────────────────────────────────
def test_failures_accumulate_per_record(temp_memory_db):
    assert _bump_send_failure("fu-1") == 1
    assert _bump_send_failure("fu-1") == 2
    assert _bump_send_failure("fu-2") == 1


def test_failure_cap_is_reachable(temp_memory_db):
    """
    العدّاد يبلغ الحدّ فيُغلق السجل.

    481 محاولة فاشلة تكرّرت كل ساعتين لشهرين لأن السجل الفاشل لم يكن
    يُغلق أبدًا.
    """
    for _ in range(_MAX_SEND_FAILURES):
        count = _bump_send_failure("fu-doomed")
    assert count >= _MAX_SEND_FAILURES


# ── سقف الدفعة ───────────────────────────────────────────────────────
def test_batch_cap_is_bounded():
    """
    السقف موجود ومعقول.

    بدونه كانت أول دورة بعد رفع القفل سترسل بطاقة لكل عميل مؤهّل
    (337) دفعةً واحدة — إغراق يجعل المراجعة البشرية مستحيلة، وهو ما
    كان الحارس الأصلي يحاول منعه.
    """
    assert 0 < CreativeFollowupEngine.MAX_CARDS_PER_RUN <= 50


def test_attempt_limit_is_bounded():
    assert 0 < CreativeFollowupEngine.MAX_ATTEMPTS <= 5


def test_ttl_is_sane():
    """المهلة أطول من دورة (ساعتان) وأقصر من أسبوع."""
    assert 2 < _CARD_TTL_HOURS <= 168


# ── استحقاق المتابعة ─────────────────────────────────────────────────
@pytest.fixture
def engine():
    return CreativeFollowupEngine(crm=None, notifier=None)


def test_lead_without_timestamp_is_due(engine):
    """سجل بلا ختم زمني يُعتبر مستحقًّا — أفضل من إسقاطه بصمت."""
    assert engine._is_due({}, 0) is True


def test_recent_lead_is_not_due(engine):
    recent = {"updated_at": datetime.utcnow().isoformat(), "source": "whatsapp"}
    assert engine._is_due(recent, 0) is False


def test_old_lead_becomes_due(engine):
    old = {"updated_at": (datetime.utcnow() - timedelta(days=30)).isoformat(), "source": "whatsapp"}
    assert engine._is_due(old, 0) is True


def test_malformed_timestamp_does_not_crash(engine):
    """ختم زمني تالف يُعامل كمستحقّ ولا يُسقط الدورة كلها."""
    assert engine._is_due({"updated_at": "ليس تاريخًا"}, 0) is True
