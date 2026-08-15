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

import ast
import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Optional

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

async def fetch_places_outscraper(query: str, api_key: str, limit: int = 20) -> Optional[list[dict]]:
    """
    يجيب أماكن حقيقية من Google Maps عبر Outscraper.

    مهم:
    - api_key يُرسل كـ base64 كما هو (بدون decode)
    - URL: api.outscraper.cloud (وليس api.app.outscraper.com)
    - async=false لاستقبال النتائج فوراً

    Returns None on a request/API failure (so the caller can tell "the API
    errored" apart from "the API succeeded but found nothing") — [] means a
    real, successful empty result.
    """
    params = {
        "query":       query,
        "limit":       limit,
        "async":       "false",
        "language":    "ar",
        "region":      "SA",
        "coordinates": "24.7136,46.6753",   # pin to Riyadh city center
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
                return None

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
            return None


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

def _parse_gemini_json(text: str) -> dict:
    """Extract JSON from Gemini response.

    Handles: markdown code blocks, extra text before/after JSON,
    Python-style single-quoted dicts.
    """
    # Strip markdown code blocks
    text = text.strip().replace("```json", "").replace("```", "").strip()

    # 1. Direct JSON parse
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    # 2. Extract first {...} block via regex
    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        try:
            return json.loads(match.group())
        except (json.JSONDecodeError, ValueError):
            pass
        # 3. Python literal_eval for single-quoted dicts
        try:
            result = ast.literal_eval(match.group())
            if isinstance(result, dict):
                return result
        except (ValueError, SyntaxError):
            pass

    # 4. Try full text with literal_eval (single-quoted Python dict)
    try:
        result = ast.literal_eval(text)
        if isinstance(result, dict):
            return result
    except (ValueError, SyntaxError):
        pass

    raise ValueError(f"Could not parse Gemini response as JSON. Preview: {text[:120]!r}")


async def _classify_with_gemini(place_info: str, gemini_key: str) -> dict:
    """Gemini with thinking disabled — returns clean JSON directly.

    Tries gemini-2.5-flash first; falls back to gemini-2.5-flash-lite on
    503 (overloaded) or 429 (quota exhausted) — quota is tracked per-model
    on the free tier, so flash-lite often still has headroom when flash
    doesn't.
    """
    from google.genai import types as gt
    from google.genai import errors as ge
    from agent.ai_client import _gemini_client

    if not gemini_key:
        raise ValueError("No Gemini API key available")

    client = _gemini_client(gemini_key)
    prompt = f"صنف هذا العميل واكتب له رسالة:\n{place_info}"

    for model in ("gemini-2.5-flash", "gemini-2.5-flash-lite"):
        config = gt.GenerateContentConfig(
            max_output_tokens=2048,
            system_instruction=SYSTEM_PROMPT,
            thinking_config=gt.ThinkingConfig(thinking_budget=0),
        )
        try:
            resp = await client.aio.models.generate_content(
                model=model, contents=prompt, config=config,
            )
            text = resp.text.strip() if resp.text else ""
            if not text:
                raise ValueError("Empty response from Gemini")
            return _parse_gemini_json(text)
        except (ge.ServerError, ge.ClientError) as e:
            msg = str(e)
            if any(s in msg for s in ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED")):
                logger.warning(f"{model} unavailable ({msg[:80]}), trying next model...")
                continue
            raise

    raise RuntimeError("All Gemini models unavailable (503/429)")


async def _classify_with_gemini_http(place_info: str, gemini_key: str) -> dict:
    """Gemini HTTP fallback — not used (keeping for legacy)."""
    raise NotImplementedError("Use _classify_with_gemini instead")


async def classify_and_draft(
    place: dict,
    semaphore: asyncio.Semaphore,
    gemini_key: str = "",
) -> Optional[dict]:
    """
    يصنّف العميل ويكتب الرسالة عبر Gemini.

    FIX 2 — Semaphore: أقصى 5 استدعاءات متزامنة.

    كانت هذي الدالة تجرب مرتين: مرة بدون مفتاح صريح (تعتمد على متغير بيئة
    GEMINI_API_KEY غير المضبوط فعلياً بهذا النشر — تفشل دائمًا فورًا بخطأ
    "لا يوجد مفتاح")، ثم مرة بالمفتاح الصريح. المحاولة الأولى كانت ميتة
    دائمًا — أُزيلت، الاستدعاء الآن مباشر بالمفتاح الصريح فقط.
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

        if not gemini_key:
            logger.error(f"Gemini key غير مضبوط — تخطي [{place.get('name')}]")
            return None
        try:
            result = await _classify_with_gemini(place_info, gemini_key)
            result["place"] = place
            result["_engine"] = "gemini"
            return result
        except Exception as e:
            logger.error(f"Gemini classification failed [{place.get('name')}]: {e}")
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
    تستخدم Outscraper فعليًا (Google Maps data) — راجع processors/serpapi_engine.py
    للمحرك المبني على SerpAPI الحقيقي (Google Search)، غير مفعّل بالجدولة حاليًا.
    """
    logger.info("🚀 بدء Outscraper Prospecting Engine")

    semaphore = asyncio.Semaphore(5)
    seen_phones: set[str] = set()
    valid_places: list[dict] = []
    failed_queries = 0

    # ── الخطوة 1: جمع الأماكن من Outscraper ─────────────────────────────────
    for query in SEARCH_QUERIES:
        logger.info(f"🔍 Outscraper: {query}")
        raw_places = await fetch_places_outscraper(query, outscraper_api_key)

        if raw_places is None:
            failed_queries += 1
            await asyncio.sleep(0.5)
            continue

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

    # كل الاستعلامات فشلت (خطأ API/فوترة، لا نتائج فارغة عادية) — كانت تمر
    # صامتة لـ47 يومًا متتالية (24 يونيو–9 أغسطس 2026) لأن الكود القديم ما
    # يفرّق بين "فشل الطلب" و"نجح الطلب بدون نتائج". هذا التنبيه يقطع الصمت.
    if failed_queries == len(SEARCH_QUERIES) and notify_callback:
        try:
            await notify_callback(
                "🔴 *فشل التنقيب اليومي بالكامل*\n"
                f"كل استعلامات Outscraper الـ{len(SEARCH_QUERIES)} فشلت اليوم — "
                "على الأرجح مشكلة رصيد/فوترة بحساب Outscraper.com.\n"
                "راجع `outscraper.com` وتحقق من بيانات الدفع/الرصيد."
            )
        except Exception as exc:
            logger.error(f"Failed to send total-failure alert: {exc}")

    logger.info(f"📍 أماكن جديدة (بعد dedup): {len(valid_places)} | استعلامات فاشلة: {failed_queries}/{len(SEARCH_QUERIES)}")

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
            # Compute ICP score + buying signals before saving
            _icp_score, _icp_segment, _signals = 0, None, []
            try:
                from processors.icp_engine import score_lead_icp, detect_buying_signals
                from models.lead import Lead as _Lead
                _tmp_lead = _Lead(
                    name=place.get("name", "unnamed"),
                    phone=place.get("formatted_phone_number") or None,
                    email="noreply@placeholder.com" if not place.get("formatted_phone_number") else None,
                    raw_data={
                        "rating": place.get("rating"),
                        "review_count": place.get("user_ratings_total"),
                        "description": result.get("reason", ""),
                        "gemini_category": result.get("category", ""),
                    },
                )
                _icp_score, _icp_segment = score_lead_icp(_tmp_lead)
                _signals = detect_buying_signals(_tmp_lead)
            except Exception as _icp_err:
                logger.warning(f"ICP scoring failed [{place.get('name')}]: {_icp_err}")

            lead_data = LeadCreate(
                name=place.get("name", ""),
                phone=place.get("formatted_phone_number", ""),
                source="serpapi_prospecting",
                category="food_transport",  # All prospecting leads are food businesses
                score=result.get("score", 50),
                priority=result.get("priority", "medium"),
                icp_score=_icp_score,
                icp_segment=_icp_segment,
                buying_signals=_signals,
                raw_data={
                    "place": {k: v for k, v in place.items() if k != "_raw"},
                    "draft_message": result.get("message", ""),
                    "reason": result.get("reason", ""),
                    "gemini_category": result.get("category", ""),  # original Arabic
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
