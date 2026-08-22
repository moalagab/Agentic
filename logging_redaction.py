"""
محو الأسرار من السجلات.
Redacts secret values from every structlog record before it is written.

الدافع: رسائل استثناءات httpx تتضمّن الرابط المطلوب كاملًا، ورابط
تيليجرام يحوي توكن البوت — فكان `error=str(exc)` يكتب التوكن نصًّا
صريحًا في logs/app.log عند كل فشل إرسال. مواضع تسجيل الاستثناءات كثيرة
ومتفرقة (أكثر من 200 في المشروع)، ومعالجتها موضعًا موضعًا تترك الباب
مفتوحًا أمام كل موضع جديد يُضاف لاحقًا.

لذلك نعالج عند المخرج لا عند المصدر: نبني قائمة القيم السرّية الفعلية
من الإعدادات مرة واحدة، ونستبدلها في كل قيمة نصية قبل الكتابة.
"""

from __future__ import annotations

import re
from typing import Any, Callable

REDACTED = "‹محذوف›"

# أسماء الإعدادات التي تُعدّ قيمها سرّية
_SECRET_FIELDS = (
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_WEBHOOK_SECRET", "ADMIN_API_KEY",
    "WAHA_API_KEY", "SUPABASE_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY",
    "SERPAPI_KEY", "OUTSCRAPER_API_KEY", "HUBSPOT_API_KEY",
    "AIRTABLE_API_KEY", "TWILIO_AUTH_TOKEN", "TWILIO_API_KEY_SECRET",
    "WHATSAPP_BUSINESS_TOKEN", "WHATSAPP_APP_SECRET",
    "LINKEDIN_CLIENT_SECRET", "BUFFER_ACCESS_TOKEN",
    "X_BEARER_TOKEN", "X_ACCESS_TOKEN_SECRET",
)

# شبكة أمان مستقلة عن الإعدادات: نمط توكن تيليجرام داخل أي رابط. يمسك
# التوكن حتى لو غُيِّر في .env دون إعادة تشغيل، أو ظهر توكن بوت آخر.
_BOT_URL = re.compile(r"/bot\d+:[A-Za-z0-9_-]{20,}")

# أنماط أسرار شائعة قد ترد من مصادر خارجية لا نملك قيمتها
_GENERIC = (
    re.compile(r"sk-ant-api03-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
)


def _collect_secrets() -> list[str]:
    values: list[str] = []
    try:
        from config import get_settings
        settings = get_settings()
    except Exception:
        return values
    for name in _SECRET_FIELDS:
        val = getattr(settings, name, "")
        # القيم القصيرة تُتجاهَل: استبدالها يشوّه نصوصًا بريئة
        if isinstance(val, str) and len(val) >= 12:
            values.append(val)
    # الأطول أولًا حتى لا يُفسد استبدالٌ جزئيٌّ سرًّا يحتوي آخر
    values.sort(key=len, reverse=True)
    return values


def build_redactor() -> Callable[[Any, str, dict], dict]:
    """أعد معالج structlog يمحو الأسرار من كل حقل نصّي في السجل."""
    secrets = _collect_secrets()

    def scrub(text: str) -> str:
        for secret in secrets:
            if secret in text:
                text = text.replace(secret, REDACTED)
        text = _BOT_URL.sub(f"/bot{REDACTED}", text)
        for pattern in _GENERIC:
            text = pattern.sub(REDACTED, text)
        return text

    def processor(logger: Any, method_name: str, event_dict: dict) -> dict:
        for key, value in event_dict.items():
            if isinstance(value, str) and value:
                event_dict[key] = scrub(value)
        return event_dict

    return processor


def install() -> bool:
    """احقن المعالج في إعداد structlog القائم دون تغيير صيغة الإخراج.

    نضيفه قبل العارض مباشرةً (آخر معالج في السلسلة) بدل إعادة ضبط
    structlog كاملًا — إعادة الضبط كانت ستغيّر شكل السجل الذي تعتمد
    عليه أدوات التشغيل.
    """
    try:
        import structlog
        config = structlog.get_config()
        processors = list(config.get("processors", []))
        if any(getattr(p, "__name__", "") == "processor" and
               getattr(p, "__module__", "") == __name__ for p in processors):
            return False   # مُثبَّت مسبقًا
        # أدرج قبل العارض (آخر عنصر)، أو في النهاية إن كانت السلسلة فارغة
        insert_at = max(len(processors) - 1, 0)
        processors.insert(insert_at, build_redactor())
        structlog.configure(processors=processors)
        return True
    except Exception:
        return False
