"""إعادة استخدام عميل HTTP في المسارين الدوريين (SF-LEAK).

لماذا هذا الملف موجود
---------------------
قياس في الإنتاج أظهر أن كل نبضة كل 5 دقائق كانت تُنشئ عميلَي
``httpx.AsyncClient``: واحدًا في ``SmartfieldScheduler._send_heartbeat``
وآخر في ``WAHAMonitor._get_status``. كل عميل يحمل ``SSLContext``، ورسم
كائناته دوري (pool → connection → pool) فلا يكفيه عدّ المراجع. وبما أن
الجمع الدوري لم يكن يعمل عمليًا، تراكم 38 ``SSLContext`` عبر 19 نبضة —
مطابقة تامة — وارتفع RSS نحو 1.44 ميغابايت لكل نبضة.

ما تثبته هذه الاختبارات هو الشرط البنيوي: المسارات المقيسة تستخدم عميلًا
مُحقونًا واحدًا ولا تستطيع إنشاء عميل لكل نداء. لا تثبت — ولا يمكنها أن
تثبت — أن الاستهلاك في الإنتاج توقف؛ ذلك لا يُعرف إلا بعد النشر والمراقبة.

لا شبكة هنا: كل شيء بدائل (fakes).
"""

from __future__ import annotations

import ast
import asyncio

import re
from pathlib import Path

import pytest

from employee.scheduler import SmartfieldScheduler
from processors.waha_monitor import WAHAMonitor

REPO = Path(__file__).resolve().parent.parent


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {"status": "WORKING"}

    def json(self) -> dict:
        return self._payload


class FakeClient:
    """عميل واحد يسجّل كل نداء. هو نفسه عبر النداءات — وهذا بيت القصيد."""

    def __init__(self, response=None, raises: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self.closed = 0
        self._response = response if response is not None else FakeResponse()
        self._raises = raises

    async def get(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if self._raises is not None:
            raise self._raises
        return self._response

    async def aclose(self) -> None:
        self.closed += 1


def make_monitor(client, **kw) -> WAHAMonitor:
    return WAHAMonitor(
        waha_url="http://waha.test",
        api_key="k",
        client=client,
        session="default",
        **kw,
    )


def make_scheduler(client) -> SmartfieldScheduler:
    # employee غير مستخدم في مسار النبضة، فيكفي None لعزل ما نختبره.
    return SmartfieldScheduler(None, heartbeat_client=client)


# ── 1 + 2: النداءات المتكررة تستخدم العميل نفسه ────────────────────────────
def test_waha_get_status_reuses_the_injected_client():
    client = FakeClient()
    monitor = make_monitor(client)

    for _ in range(5):
        assert asyncio.run(monitor._get_status()) == "WORKING"

    assert len(client.calls) == 5, "كل نداء يمرّ عبر العميل نفسه"
    assert monitor._client is client


def test_heartbeat_reuses_the_injected_client(monkeypatch):
    client = FakeClient()
    scheduler = make_scheduler(client)
    monkeypatch.setattr(
        "employee.scheduler.get_settings",
        lambda: type("S", (), {"HEARTBEAT_URL": "http://beat.test/ping"})(),
    )

    for _ in range(5):
        asyncio.run(scheduler._send_heartbeat())

    assert len(client.calls) == 5
    assert scheduler._heartbeat_client is client


# ── 3: لا يمكن إنشاء عميل لكل نداء ─────────────────────────────────────────
def test_measured_paths_never_construct_a_client(monkeypatch):
    """لو حاول أي مسار بناء AsyncClient لانفجر الاختبار هنا."""

    def explode(*a, **k):  # pragma: no cover - يُفترض ألا يُنادى
        raise AssertionError("تم إنشاء httpx.AsyncClient داخل مسار دوري")

    monkeypatch.setattr("httpx.AsyncClient", explode)

    waha_client = FakeClient()
    monitor = make_monitor(waha_client)
    for _ in range(3):
        asyncio.run(monitor._get_status())

    beat_client = FakeClient()
    scheduler = make_scheduler(beat_client)
    monkeypatch.setattr(
        "employee.scheduler.get_settings",
        lambda: type("S", (), {"HEARTBEAT_URL": "http://beat.test/ping"})(),
    )
    for _ in range(3):
        asyncio.run(scheduler._send_heartbeat())

    assert len(waha_client.calls) == 3
    assert len(beat_client.calls) == 3


def test_both_clients_are_structurally_required():
    """الحقن إلزامي: لا يوجد بديل صامت يعيد السلوك القديم."""
    with pytest.raises(TypeError):
        WAHAMonitor(waha_url="http://waha.test", api_key="k")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        SmartfieldScheduler(None)  # type: ignore[call-arg]


# ── 4: المهلة ما زالت 10 ثوانٍ ─────────────────────────────────────────────
def test_timeout_is_still_ten_seconds_on_both_paths(monkeypatch):
    waha_client = FakeClient()
    asyncio.run(make_monitor(waha_client)._get_status())
    assert waha_client.calls[0]["timeout"] == 10

    beat_client = FakeClient()
    monkeypatch.setattr(
        "employee.scheduler.get_settings",
        lambda: type("S", (), {"HEARTBEAT_URL": "http://beat.test/ping"})(),
    )
    asyncio.run(make_scheduler(beat_client)._send_heartbeat())
    assert beat_client.calls[0]["timeout"] == 10


# ── 5: سلوك النجاح لم يتغيّر ───────────────────────────────────────────────
def test_success_semantics_unchanged():
    client = FakeClient(FakeResponse(200, {"status": "SCAN_QR_CODE"}))
    monitor = make_monitor(client)
    monitor._consecutive_failures = 4

    assert asyncio.run(monitor._get_status()) == "SCAN_QR_CODE"
    assert monitor._consecutive_failures == 0, "النجاح يصفّر عدّاد الإخفاق"

    call = client.calls[0]
    assert call["url"] == "http://waha.test/api/sessions/default"
    assert call["headers"] == {"X-Api-Key": "k"}


def test_404_still_returns_none_without_resetting_failures():
    client = FakeClient(FakeResponse(404))
    monitor = make_monitor(client)
    monitor._consecutive_failures = 2

    assert asyncio.run(monitor._get_status()) is None
    assert monitor._consecutive_failures == 2, "404 لا يُعدّ نجاحًا ولا إخفاقًا"


# ── 6: سلوك الخطأ لم يتغيّر ────────────────────────────────────────────────
def test_error_semantics_unchanged():
    client = FakeClient(raises=RuntimeError("boom"))
    monitor = make_monitor(client)

    assert asyncio.run(monitor._get_status()) is None
    assert monitor._consecutive_failures == 1, "الإخفاق يزيد العدّاد"


def test_heartbeat_still_swallows_exceptions(monkeypatch):
    client = FakeClient(raises=RuntimeError("beat down"))
    scheduler = make_scheduler(client)
    monkeypatch.setattr(
        "employee.scheduler.get_settings",
        lambda: type("S", (), {"HEARTBEAT_URL": "http://beat.test/ping"})(),
    )
    asyncio.run(scheduler._send_heartbeat())  # لا يرفع


def test_heartbeat_url_guard_still_short_circuits(monkeypatch):
    client = FakeClient()
    scheduler = make_scheduler(client)
    monkeypatch.setattr(
        "employee.scheduler.get_settings",
        lambda: type("S", (), {"HEARTBEAT_URL": ""})(),
    )
    asyncio.run(scheduler._send_heartbeat())
    assert client.calls == [], "بلا رابط لا يُرسل شيء ولا يُلمس العميل"


# ── 7 + 8 + 9: بنية الإغلاق عند الإيقاف ───────────────────────────────────
#
# لا تُشغَّل lifespan هنا: استيرادها يجرّ fastapi وهي ليست من تبعيات
# الاختبار عمدًا. لذلك نفحص البنية عبر AST — لا بوجود نص "aclose" الذي
# يمرّ حتى لو كان الإغلاق في مسار لا يُبلَغ عند فشل الإقلاع.


def _lifespan_ast():
    src = (REPO / "channels" / "webhook_server.py").read_text(encoding="utf8")
    tree = ast.parse(src)
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "lifespan"
    )
    return src, fn


def _try_covering_yield():
    """الـ try التي تحيط بـ yield، إن وُجدت."""
    src, fn = _lifespan_ast()
    yields = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Yield)]
    assert len(yields) == 1, "lifespan يجب أن تحوي yield واحدة"
    covering = [
        t for t in ast.walk(fn)
        if isinstance(t, ast.Try) and t.lineno <= yields[0] <= t.end_lineno
    ]
    return src, covering


def test_cleanup_sits_in_a_finally_that_covers_the_yield():
    """A: التنظيف في مسار finally، لا بعد yield فحسب."""
    src, covering = _try_covering_yield()
    assert covering, (
        "لا توجد try تحيط بـ yield: أي استثناء أثناء الإقلاع يتجاوز التنظيف"
    )
    assert any(t.finalbody for t in covering), "الـ try المحيطة بلا finally"


def test_protected_region_includes_client_creation_and_yield():
    """B: المنطقة المحمية تشمل إنشاء العميلين و yield معًا."""
    src, covering = _try_covering_yield()
    guarded = [t for t in covering if t.finalbody]
    assert guarded, "لا توجد finally محيطة"
    body_src = "\n".join(
        ast.get_source_segment(src, stmt) or "" for t in guarded for stmt in t.body
    )
    assert body_src.count("httpx.AsyncClient()") == 2, (
        "يجب أن يُنشأ العميلان داخل المنطقة المحمية"
    )
    assert "yield" in body_src, "yield يجب أن تقع داخل المنطقة المحمية"


def test_both_clients_are_closed_inside_the_cleanup_path():
    """C: كلا الإغلاقين داخل finally."""
    src, covering = _try_covering_yield()
    fin = "\n".join(
        ast.get_source_segment(src, stmt) or ""
        for t in covering if t.finalbody for stmt in t.finalbody
    )
    assert "aclose()" in fin
    assert "_heartbeat_http_client" in fin
    assert "_waha_http_client" in fin
    assert "is not None" in fin, "الإغلاق محروس ضد عميل لم يُنشأ"


def test_one_close_failure_does_not_block_the_other():
    """D: كل إغلاق معزول باستثنائه، فلا يمنع فشلُ أحدهما الآخر."""
    src, covering = _try_covering_yield()
    fin_nodes = [stmt for t in covering if t.finalbody for stmt in t.finalbody]
    inner_tries = [
        n for stmt in fin_nodes for n in ast.walk(stmt)
        if isinstance(n, ast.Try) and n.handlers
    ]
    assert inner_tries, "الإغلاق يجب أن يكون داخل try/except لكل عميل"
    handled = "\n".join(
        ast.get_source_segment(src, h) or "" for t in inner_tries for h in t.handlers
    )
    assert "close_failed" in handled, "فشل الإغلاق يُسجَّل ولا يُبتلع صامتًا"

    # وسلوكيًا على بديلين، بمعزل عن lifespan نفسها:
    class Stubborn(FakeClient):
        async def aclose(self):
            raise RuntimeError("refuses to close")

    async def drive():
        bad, good = Stubborn(), FakeClient()
        for client in (bad, good):
            if client is not None:
                try:
                    await client.aclose()
                except Exception:
                    pass
        return good

    assert asyncio.run(drive()).closed == 1


def test_scheduler_stop_failure_cannot_skip_the_http_cleanup():
    """كل عملية تنظيف مستقلة معزولة باستثنائها.

    APScheduler يرفع SchedulerNotRunningError إن استُدعي shutdown وهو
    متوقّف، وعلَمنا الداخلي مستقلّ عن حالته — فقد يتباعدان. لو لم يكن
    stop() معزولًا لتجاوز استثناؤه إغلاق العميلين كليهما.
    """
    src, covering = _try_covering_yield()
    fin_stmts = [st for t in covering if t.finalbody for st in t.finalbody]
    assert fin_stmts, "لا يوجد finally"

    def isolated(node):
        """هل النداء داخل try/except خاص به ضمن finally؟"""
        for st in fin_stmts:
            for t in ast.walk(st):
                if (isinstance(t, ast.Try) and t.handlers
                        and t.lineno <= node.lineno <= t.end_lineno):
                    return True
        return False

    stops = [n for st in fin_stmts for n in ast.walk(st)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "stop"]
    assert len(stops) == 1, "إيقاف الجدول يُحاول مرة واحدة فقط"
    assert isolated(stops[0]), (
        "فشل _scheduler.stop() سيتجاوز إغلاق العميلين: يجب عزله بـ try/except"
    )

    closes = [n for st in fin_stmts for n in ast.walk(st)
              if isinstance(n, ast.Await) and isinstance(n.value, ast.Call)
              and getattr(n.value.func, "attr", "") == "aclose"]
    assert len(closes) == 1, "إغلاق واحد داخل حلقة تغطي العميلين"
    assert isolated(closes[0]), "كل إغلاق معزول باستثنائه"

    # الترتيب محفوظ: إيقاف الجدول ثم إغلاق العميلين.
    assert stops[0].lineno < closes[0].lineno


def test_every_independent_cleanup_step_is_attempted(monkeypatch):
    """سلوكيًا على بدائل: فشل الخطوة الأولى لا يمنع ما بعدها."""

    class BadScheduler:
        def __init__(self): self.stopped = 0
        def stop(self):
            self.stopped += 1
            raise RuntimeError("scheduler refuses to stop")

    async def drive():
        sched = BadScheduler()
        beat, waha = FakeClient(), FakeClient()
        # نفس شكل finally في lifespan
        if sched:
            try:
                sched.stop()
            except Exception:
                pass
        for _name, client in (("heartbeat", beat), ("waha", waha)):
            if client is not None:
                try:
                    await client.aclose()
                except Exception:
                    pass
        return sched, beat, waha

    sched, beat, waha = asyncio.run(drive())
    assert sched.stopped == 1, "الإيقاف حوول مرة واحدة"
    assert beat.closed == 1, "فشل إيقاف الجدول لم يمنع إغلاق النبضة"
    assert waha.closed == 1, "ولا إغلاق عميل WAHA"


def test_scheduler_stop_is_safe_without_a_successful_start():
    """stop() محروس بـ _running داخليًا، فلا يحتاج lifespan علمًا إضافيًا."""
    scheduler = make_scheduler(FakeClient())
    assert scheduler._running is False
    scheduler.stop()  # لا يرفع ولا يفعل شيئًا
    assert scheduler._running is False


# ── الفحص الساكن: المسارات المقيسة خالية من بناء العملاء ──────────────────
def _method_source(path: str, name: str) -> str:
    text = (REPO / path).read_text(encoding="utf8")
    match = re.search(
        r"\n    async def %s\(.*?(?=\n    async def |\n    def |\Z)" % name, text, re.S
    )
    assert match, "%s غير موجودة في %s" % (name, path)
    return match.group(0)


def test_no_client_construction_left_in_the_measured_paths():
    assert "httpx.AsyncClient(" not in _method_source(
        "processors/waha_monitor.py", "_get_status"
    )
    assert "httpx.AsyncClient(" not in _method_source(
        "employee/scheduler.py", "_send_heartbeat"
    )


def test_untouched_waha_failure_paths_are_left_alone():
    """خارج نطاق هذه المرحلة عمدًا — يوثَّق حتى لا يُظن أنه سهو."""
    for name in ("_start_session", "_stop_session"):
        assert "httpx.AsyncClient(" in _method_source(
            "processors/waha_monitor.py", name
        )
