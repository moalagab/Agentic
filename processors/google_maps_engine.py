"""
Outscraper Prospecting Engine — محرك البحث الحقيقي

يبحث عن أماكن تجارية حقيقية عبر Outscraper (Google Maps data)،
ثم يستخدم Claude لتصنيفها وكتابة رسالة واتساب مخصصة لكل عميل.

ملاحظة مهمة:
- الـ API key يُرسل كـ base64 كما هو (بدون decode)
- الـ URL الصحيح: api.outscraper.cloud (وليس api.app.outscraper.com)

الإصلاحات المطبّقة:
١. Prompt Caching  — system prompt يُرسل مرة واحدة (~90% توفير)
٢. Concurrency     — 5 طلبات Claude بالتوازي (5x أسرع)
٣. CRM Dedup       — تحقق من Supabase قبل استدعاء Claude
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Optional

import anthropic
import httpx

from models.lead import LeadCreate
from crm.supabase_crm import SupabaseCRM

logger = logging.getLogger(__name__)

OUTSCRAPER_URL = "https://api.outscraper.cloud/google-maps-search"

SEARCH_QUERIES = [
    "محمصة قهوة الرياض",
    "مطبخ سحابي الرياض",
    "dark kitchen الرياض",
    "مخبز فاخر الرياض",
    "ورشة شوكولاتة الرياض",
    "مطعم كاتيرينج الرياض",
    "مورد غذائي الرياض",
    "حلويات فاخرة الرياض",
    "مطبخ مركزي الرياض",
    "محل تمور فاخرة الرياض",
]

SYSTEM_PROMPT = """أنت مساعد مبيعات متخصص لشركة Smart Field للنقل المبرد في الرياض.

مهمتك:
١. تقييم كل عميل محتمل وتحديد أولويته
٢. كتابة رسالة واتساب مخصصة بالعربي السعودي

قواعد الرسالة:
- قصيرة (3-4 أسطر فقط)
- مباشرة وواثقة، مو corporate فارغ
- تذكر اسم المحل
- تركز على حماية المنتج مو على النقل
- CTA واضح: رقم واتساب wa.me/966561167169
- لا تذكر الأدوية أو B2C
- لا تدّعي تغطية سعودية كاملة

مثال رسالة صح:
"مرحباً [اسم المحل]، نحن Smart Field متخصصين في النقل المبرد للمحامص والمخابز الفاخرة بالرياض. كل شحنة معنا تجي مع تقرير حراري موثق يحمي منتجك. نبدأ برحلة تجريبية؟ wa.me/966561167169"

رد بـ JSON فقط بدون أي نص إضافي:
{
  "priority": "high/medium/low",
  "score": 0-100,
  "category": "نوع النشاط",
  "message": "رسالة الواتساب",
  "reason": "سبب الأولوية"
}"""


# ===============================
# SerpAPI — جلب الأماكن
# ===============================

async def fetch_places_outscraper(query: str, api_key: str, limit: int = 20) -> list[dict]:
    """
    يجيب أماكن حقيقية من Google Maps عبر Outscraper.

    مهم:
    - api_key يُرسل كـ base64 كما هو (بدون decode)
    - URL: api.outscraper.cloud (وليس api.app.outscraper.com)
    - async=false لاستقبال النتائج فوراً
    """
    params = {
        "query": query,
        "limit": limit,
        "async": "false",
        "language": "ar",
        "region": "SA",
    }

    async with httpx.AsyncClient(timeout=60) as client:
        try:
            response = await client.get(
                OUTSCRAPER_URL,
                headers={"X-API-KEY": api_key},
                params=params,
            )

            if response.status_code != 200:
                logger.warning(f"Outscraper {response.status_code} [{query}]: {response.text[:200]}")
                return []

            data = response.json()
            # النتائج في data["data"] كـ list of lists
            flat = []
            for batch in data.get("data", []):
                if isinstance(batch, list):
                    flat.extend(batch)
                elif isinstance(batch, dict):
                    flat.append(batch)
            return flat

        except Exception as e:
            logger.error(f"Outscraper fetch error [{query}]: {e}")
            return []


def normalize_place(raw: dict) -> dict:
    """
    يحوّل حقول Outscraper لصيغة موحّدة.

    Outscraper fields:
        name, phone, full_address, site, rating, reviews, type, subtypes
    """
    return {
        "name":                   raw.get("name", ""),
        "formatted_phone_number": raw.get("phone", ""),
        "formatted_address":      raw.get("full_address", ""),
        "website":                raw.get("site", ""),
        "rating":                 raw.get("rating", ""),
        "user_ratings_total":     raw.get("reviews", 0),
        "types":                  [raw.get("type", "")] + (raw.get("subtypes", "").split(", ") if raw.get("subtypes") else []),
        "_raw":                   raw,
    }


# ===============================
# Claude — تصنيف + Caching + Semaphore
# ===============================

async def _classify_with_claude(place_info: str) -> dict:
    """Claude — المحرك الأساسي مع Prompt Caching."""
    client = anthropic.AsyncAnthropic()
    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        system=[{
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{
            "role": "user",
            "content": f"صنف هذا العميل واكتب له رسالة:\n{place_info}",
        }],
    )
    return json.loads(
        response.content[0].text.strip()
        .replace("```json", "").replace("```", "").strip()
    )


async def _classify_with_gemini(place_info: str, gemini_key: str) -> dict:
    """Gemini — الاحتياطي عند توقف Claude."""
    prompt = f"{SYSTEM_PROMPT}\n\nصنف هذا العميل واكتب له رسالة:\n{place_info}"
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent",
            headers={"Content-Type": "application/json", "X-goog-api-key": gemini_key},
            json={"contents": [{"parts": [{"text": prompt}]}]},
        )
        response.raise_for_status()
        text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(
            text.strip().replace("```json", "").replace("```", "").strip()
        )


async def classify_and_draft(
    place: dict,
    semaphore: asyncio.Semaphore,
    gemini_key: str = "",
) -> Optional[dict]:
    """
    يصنّف العميل ويكتب الرسالة — Claude أولاً، Gemini احتياطياً.

    FIX 1 — Prompt Caching : system prompt يُخزَّن في Anthropic (~90% توفير)
    FIX 2 — Semaphore      : أقصى 5 استدعاءات متزامنة
    Fallback               : Gemini إذا نفد رصيد Claude أو حدث خطأ
    """
    async with semaphore:
        place_info = (
            f"اسم المحل: {place.get('name', '')}\n"
            f"نوع النشاط: {', '.join(t for t in place.get('types', []) if t)}\n"
            f"العنوان: {place.get('formatted_address', '')}\n"
            f"رقم الهاتف: {place.get('formatted_phone_number', 'غير متوفر')}\n"
            f"التقييم: {place.get('rating', 'غير متوفر')} ({place.get('user_ratings_total', 0)} تقييم)\n"
            f"الموقع الإلكتروني: {place.get('website', 'غير متوفر')}\n"
        )

        # ── المحاولة 1: Claude ────────────────────────────────────────────────
        try:
            result = await _classify_with_claude(place_info)
            result["place"] = place
            result["_engine"] = "claude"
            return result
        except json.JSONDecodeError as e:
            logger.warning(f"Claude JSON parse error [{place.get('name')}]: {e}")
            return None
        except Exception as e:
            logger.warning(f"Claude failed [{place.get('name')}]: {e} — جاري تجربة Gemini...")

        # ── المحاولة 2: Gemini (fallback) ─────────────────────────────────────
        if not gemini_key:
            logger.error(f"Gemini key غير مضبوط — تخطي [{place.get('name')}]")
            return None
        try:
            result = await _classify_with_gemini(place_info, gemini_key)
            result["place"] = place
            result["_engine"] = "gemini"
            logger.info(f"✅ Gemini أكمل بنجاح [{place.get('name')}]")
            return result
        except json.JSONDecodeError as e:
            logger.warning(f"Gemini JSON parse error [{place.get('name')}]: {e}")
            return None
        except Exception as e:
            logger.error(f"Gemini failed [{place.get('name')}]: {e}")
            return None


# ===============================
# قائمة الموافقة اليومية للمالك
# ===============================

async def build_daily_approval_list(results: list[dict]) -> str:
    if not results:
        return "لا يوجد عملاء محتملين جدد اليوم."

    priority_order = {"high": 0, "medium": 1, "low": 2}
    results.sort(key=lambda x: priority_order.get(x.get("priority", "low"), 2))

    lines = ["📋 *عملاء جدد — Smart Field*\n"]

    for i, r in enumerate(results[:20], 1):
        place = r.get("place", {})
        emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(r.get("priority"), "⚪")

        lines.append(
            f"{emoji} *{i}. {place.get('name', 'غير معروف')}*\n"
            f"📍 {place.get('formatted_address', '')}\n"
            f"📞 {place.get('formatted_phone_number', 'غير متوفر')}\n"
            f"⭐ {place.get('rating', '-')} | درجة: {r.get('score', 0)}/100\n"
            f"💬 _الرسالة:_\n{r.get('message', '')}\n"
            f"✅ للإرسال: وافق على رقم {i}\n"
        )

    return "\n".join(lines)


# ===============================
# المحرك الرئيسي
# ===============================

async def run_prospecting_engine(
    outscraper_api_key: str,
    crm: SupabaseCRM,
    notify_callback=None,
    gemini_api_key: str = "",
) -> list[dict]:
    """
    الدالة الرئيسية — تشغّل من الـ Scheduler كل صباح 8:15.
    المعامل outscraper_api_key يُستخدم الآن لـ SerpAPI.
    """
    logger.info("🚀 بدء SerpAPI Prospecting Engine")

    semaphore = asyncio.Semaphore(5)
    seen_phones: set[str] = set()
    valid_places: list[dict] = []

    # ── الخطوة 1: جمع الأماكن من Outscraper ─────────────────────────────────
    for query in SEARCH_QUERIES:
        logger.info(f"🔍 Outscraper: {query}")
        raw_places = await fetch_places_outscraper(query, outscraper_api_key)

        for raw in raw_places:
            place = normalize_place(raw)
            phone = place["formatted_phone_number"]

            if not phone or phone in seen_phones:
                continue

            # FIX 3: تحقق من CRM قبل إرسال لـ Claude
            try:
                existing = await crm.search_lead(phone=phone)
                if existing:
                    logger.debug(f"⏭️  موجود في CRM: {place['name']} ({phone})")
                    continue
            except Exception as e:
                logger.warning(f"CRM search failed [{phone}]: {e}")

            seen_phones.add(phone)
            valid_places.append(place)

        await asyncio.sleep(0.5)

    logger.info(f"📍 أماكن جديدة (بعد dedup): {len(valid_places)}")

    if not valid_places:
        logger.info("لا توجد أماكن جديدة اليوم")
        return []

    # ── الخطوة 2: Claude يصنف الكل بالتوازي ─────────────────────────────────
    logger.info(f"🤖 إرسال {len(valid_places)} مكان لـ Claude (5 متزامن)...")
    tasks = [classify_and_draft(place, semaphore, gemini_api_key) for place in valid_places]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    all_results: list[dict] = []
    for place, result in zip(valid_places, raw_results):
        if isinstance(result, Exception):
            logger.error(f"classify error [{place.get('name')}]: {result}")
            continue
        if not result:
            continue

        # ── الخطوة 3: حفظ في Supabase ────────────────────────────────────────
        try:
            lead_data = LeadCreate(
                name=place.get("name", ""),
                phone=place.get("formatted_phone_number", ""),
                source="serpapi_prospecting",
                category=result.get("category", "food_transport"),
                score=result.get("score", 50),
                priority=result.get("priority", "medium"),
                raw_data={
                    "place": {k: v for k, v in place.items() if k != "_raw"},
                    "draft_message": result.get("message", ""),
                    "reason": result.get("reason", ""),
                    "collected_at": datetime.now().isoformat(),
                },
            )
            await crm.create_lead(lead_data)
        except Exception as e:
            logger.error(f"CRM save error [{place.get('name')}]: {e}")

        all_results.append(result)

    logger.info(f"✅ اكتمل — {len(all_results)} عميل محتمل جديد")

    # ── الخطوة 4: أرسل القائمة للمالك ───────────────────────────────────────
    if notify_callback and all_results:
        message = await build_daily_approval_list(all_results)
        await notify_callback(message)

    return all_results
