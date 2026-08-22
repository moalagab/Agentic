"""
عقد المصادقة: كل نقطة نهاية إمّا محمية أو عامّة بقرار صريح.

الخلفية: في 2026-08-22 كانت 42 من 46 نقطة مكشوفة للإنترنت بلا مصادقة،
منها `/api/outbound/send` (إرسال واتساب حقيقي) و`/api/prospecting/run`
(استهلاك رصيد). لم يكن أحد قد قرّر ذلك — النقاط أُضيفت تباعًا ولم يسأل
أحد عن حمايتها.

هذا الاختبار يحوّل السهو إلى قرار: أي نقطة جديدة تفشل البناء ما لم
تُحمَ أو تُدرَج هنا بسبب مكتوب.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parent.parent / "channels" / "webhook_server.py"


# ── النقاط العامّة عمدًا، ولكلٍّ سببها ────────────────────────────────
PUBLIC_ROUTES: dict[str, str] = {
    "/health": "فحص حياة — لا يكشف بيانات",
    # نقاط الـ webhook تستدعيها خدمات خارجية لا تملك مفتاح الإدارة.
    # حمايتها تتم بالتوقيع/السرّ لا بمفتاح الإدارة — انظر
    # test_webhooks_verify_their_caller أدناه.
    "/webhook/whatsapp": "Meta/WAHA تستدعيه — يُحمى بتوقيع X-Hub-Signature",
    "/webhook/telegram": "تيليجرام تستدعيه — يُحمى بـ secret_token",
    "/webhook/linkedin": "LinkedIn تستدعيه",
    "/webhook/google-forms": "Google Forms تستدعيه",
    "/webhook/website": "نموذج الموقع العام",
    "/webhook/website-form": "نموذج الموقع العام",
    "/webhook/ads": "Meta Ads تستدعيه",
    # استقبال العملاء من نماذج الموقع — عام بحكم الغرض.
    "/api/lead": "إرسال عميل من الموقع — عام بحكم وظيفته",
}


def _routes() -> list[tuple[str, str, bool]]:
    """استخرج (المسار، الطريقة، أمحميّ) بتحليل نصّ الملف.

    نقرأ الملف بدل استيراد التطبيق: الاستيراد يُقلع الخدمة كاملةً
    (قاعدة بيانات، مجدول، عملاء خارجيين) وهو ما لا يصلح في CI.
    """
    src = SERVER.read_text(encoding="utf-8")

    def sig_end(text: str, start: int) -> int:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
                if depth == 0:
                    return i
        raise AssertionError("توقيع دالة غير مغلق")

    found = []
    for m in re.finditer(r'@app\.(get|post|delete)\("([^"]+)"', src):
        method, path = m.group(1).upper(), m.group(2)
        dm = re.search(r"\nasync def (\w+)\(", src[m.start(): m.start() + 900])
        assert dm, f"لم يُعثر على معالج للطريق {path}"
        open_paren = m.start() + dm.end() - 1
        params = src[open_paren + 1: sig_end(src, open_paren)]
        found.append((path, method, "require_admin_key" in params))
    return found


ROUTES = _routes()


def test_routes_were_discovered():
    """حارس للاختبار نفسه: لو تغيّر شكل الملف وصار التحليل يُعيد لا شيء،
    لصارت بقية الاختبارات تمرّ فارغةً بلا أن تفحص شيئًا."""
    assert len(ROUTES) >= 40


@pytest.mark.parametrize("path,method,protected", ROUTES, ids=lambda v: str(v))
def test_every_route_is_protected_or_explicitly_public(path, method, protected):
    """كل طريق: إمّا مصادقة إدارية، أو إدراج صريح في قائمة العامّة."""
    if protected:
        return
    assert path in PUBLIC_ROUTES, (
        f"{method} {path} بلا مصادقة وغير مُدرَج كعامّ.\n"
        f"أضِف Depends(require_admin_key)، أو أدرجه في PUBLIC_ROUTES مع سبب."
    )


def test_public_list_has_no_stale_entries():
    """إدخال في قائمة العامّة لطريق لم يعد موجودًا يخفي سهوًا لاحقًا."""
    existing = {p for p, _, _ in ROUTES}
    stale = set(PUBLIC_ROUTES) - existing
    assert not stale, f"مسارات مُدرَجة كعامّة ولم تعد موجودة: {stale}"


def test_destructive_routes_are_protected():
    """
    الحذف والتعديل والإرسال الفعلي لا يجوز أن تكون عامّة بأي حال.

    مكرّرة عمدًا مع الاختبار العام: هذه لا يجوز إسكاتها بإضافتها إلى
    PUBLIC_ROUTES، فالتكرار يجعل ذلك مستحيلًا.
    """
    must_protect = ("/outbound/send", "/prospecting/run", "/backup/run", "/admin/")
    for path, method, protected in ROUTES:
        if method == "DELETE" or any(frag in path for frag in must_protect):
            assert protected, f"{method} {path} تدميري/حسّاس ويجب أن يكون محميًا"


def test_admin_auth_fails_closed():
    """
    غياب ADMIN_API_KEY يجب أن يرفض الطلب لا أن يسمح به.

    السلوك السابق كان يسمح مع تحذير في السجل — أي أن خطأ نشر واحد يترك
    حذف العملاء مفتوحًا للإنترنت، والتحذير وحده لا يمنع شيئًا.
    """
    src = SERVER.read_text(encoding="utf-8")
    body = src[src.index("def require_admin_key("): src.index("# ── Pydantic")]
    assert "raise HTTPException" in body
    assert "return  # dev mode" not in body


def test_admin_key_uses_constant_time_comparison():
    """المقارنة النصّية العادية تسرّب المفتاح عبر قياس زمن الرد."""
    src = SERVER.read_text(encoding="utf-8")
    body = src[src.index("def require_admin_key("): src.index("# ── Pydantic")]
    assert "compare_digest" in body


def test_cors_is_not_wildcarded():
    """`allow_origins=["*"]` يسمح لأي موقع باستدعاء النقاط من متصفح الزائر."""
    src = SERVER.read_text(encoding="utf-8")
    assert 'allow_origins=["*"]' not in src


def test_webhooks_verify_their_caller():
    """
    نقاط الـ webhook عامّة بالضرورة، فيجب أن تتحقّق من هوية المُستدعي.

    بلا ذلك يستطيع أي شخص إرسال تحديث تيليجرام مزوّر — بما فيه ضغطة زر
    'موافقة' — فيتجاوز بوابة الموافقة البشرية ويطلق إرسالًا لعملاء
    حقيقيين.
    """
    src = SERVER.read_text(encoding="utf-8")
    telegram = src[src.index('@app.post("/webhook/telegram"'):]
    telegram = telegram[: telegram.index("async def _handle_telegram_message")]
    assert "X-Telegram-Bot-Api-Secret-Token" in telegram
    assert "compare_digest" in telegram


def test_telegram_approvals_are_owner_only():
    """قرارات الموافقة تُقبل من محادثات المُلّاك فقط — طبقة مستقلة عن السرّ."""
    src = SERVER.read_text(encoding="utf-8")
    handler = src[src.index("async def _handle_telegram_callback"):]
    handler = handler[:4000]
    assert "TELEGRAM_OWNER_CHAT_IDS" in handler
