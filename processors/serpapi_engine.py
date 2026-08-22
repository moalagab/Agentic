"""
SerpAPI Engine — Google Maps prospecting + Content Intelligence
- مصدر ثانٍ للتنقيب: Google Maps local results
- ذكاء المحتوى: Saudi Google Trends + أخبار صناعة Cold Chain
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import Any, Optional

import httpx

from models.lead import LeadCreate, LeadSource

logger = logging.getLogger(__name__)

SERPAPI_URL = "https://serpapi.com/search"

# مجموعة استعلامات مختلفة عن Outscraper لتنويع النتائج
# ═══════════════════════════════════════════════════════════════════════
# استعلامات التنقيب — مرتّبة حسب أولوية العميل المستهدف
# ═══════════════════════════════════════════════════════════════════════
# تنبيه: التشغيل يأخذ أول `max_queries` فقط (4 يوميًا)، فترتيب هذه
# القائمة هو ما يُنقَّب عنه فعليًا لا مجرد تفضيل.
#
# القائمة السابقة كانت تبدأ بـ "صيدلية الرياض" و"مستشفى خاص الرياض"،
# أي أن نصف الميزانية اليومية كان يذهب إلى شريحة يستبعدها المصنّف
# **قانونيًا** (لا ترخيص ناقل من الهيئة العامة للغذاء والدواء)، ولم يكن
# فيها استعلام واحد لمقدّمي الوجبات الصحية والاشتراكات — وهو ما يفسّر
# وجود عميلين اثنين فقط في شريحة meal_subscription من أصل 337.

# الشريحة الأولى: مقدّمو الوجبات الصحية والاشتراكات الشهرية (Meal Run)
PRIMARY_QUERIES = [
    "اشتراك وجبات صحية الرياض",
    "مطعم صحي دايت الرياض",
    "توصيل وجبات دايت الرياض",
    "فيت فود الرياض",
    "مطبخ دايت اشتراكات الرياض",
    "وجبات كيتو الرياض",
    "نظام غذائي اشتراك شهري الرياض",
    "مطعم وجبات صحية للشركات الرياض",
]

# الشرائح التالية: تُنقَّب بالتناوب بعد تغطية الشريحة الأولى
SECONDARY_QUERIES = [
    "محل لحوم فاخرة الرياض",
    "مطعم فندقي الرياض",
    "موزع أغذية الرياض",
    "شركة تموين الرياض",
    "مخزن تبريد الرياض",
    "مورد خضار وفواكه الرياض",
    "كيتيرينج شركات الرياض",
    "محمصة قهوة الرياض",
    "متجر شوكولاتة الرياض",
    "مخبز وحلويات الرياض",
    "محل بقالة فاخرة الرياض",
    "مطعم سلسلة الرياض",
]

# ملغاة نهائيًا: صيدليات ومستشفيات وعيادات تجميل. الاستبعاد قانوني
# لا تفضيلي، فإبقاؤها في القائمة يهدر الميزانية على عملاء لا يمكن
# التعاقد معهم أصلًا.

SERPAPI_QUERIES = PRIMARY_QUERIES + SECONDARY_QUERIES


def select_queries(max_queries: int = 4, rotation: int = 0) -> list[str]:
    """
    اختر استعلامات الدورة: الشريحة الأولى تأخذ النصيب الأكبر دائمًا،
    والباقي يتناوب حتى لا تُهمَل الشرائح الأخرى.

    مع max_queries=4: ثلاثة استعلامات للوجبات الصحية + واحد متناوب.
    الاعتماد على SERPAPI_QUERIES[:4] وحده كان يعني تنقيبًا في نفس
    الأربعة كل يوم إلى الأبد.
    """
    if max_queries <= 0:
        return []
    n_primary = max(1, round(max_queries * 0.75))
    n_secondary = max_queries - n_primary

    def _rotate(pool: list[str], count: int, step: int) -> list[str]:
        if not pool or count <= 0:
            return []
        start = (step * count) % len(pool)
        return [pool[(start + i) % len(pool)] for i in range(min(count, len(pool)))]

    return _rotate(PRIMARY_QUERIES, n_primary, rotation) + \
           _rotate(SECONDARY_QUERIES, n_secondary, rotation)


# Riyadh city center coordinates — zoom 12 covers ~40km radius
_RIYADH_LL = "@24.7136,46.6753,12z"

async def fetch_places_serpapi(
    query: str,
    api_key: str,
    limit: int = 20,
) -> list[dict]:
    """Fetch Google Maps local results via SerpAPI — pinned to Riyadh."""
    params = {
        "engine":   "google_maps",
        "q":        query,
        "hl":       "ar",
        "gl":       "sa",
        "ll":       _RIYADH_LL,   # pin to Riyadh lat/lng
        "type":     "search",
        "api_key":  api_key,
        "num":      limit,
    }

    async with httpx.AsyncClient(timeout=45) as client:
        try:
            resp = await client.get(SERPAPI_URL, params=params)
            if resp.status_code != 200:
                logger.warning(f"SerpAPI {resp.status_code} [{query}]: {resp.text[:150]}")
                return []
            data = resp.json()
            return data.get("local_results") or []
        except Exception as exc:
            logger.error(f"SerpAPI fetch error [{query}]: {exc}")
            return []


def normalize_serpapi_place(raw: dict) -> dict:
    """Map SerpAPI local_result fields to the common place format."""
    phone = raw.get("phone") or ""
    # SerpAPI sometimes returns "+966 X XXX XXXX" — strip spaces
    phone = phone.replace(" ", "").replace("-", "")

    rating = raw.get("rating", 0.0)
    try:
        rating = float(rating)
    except (TypeError, ValueError):
        rating = 0.0

    reviews = raw.get("reviews", 0)
    try:
        reviews = int(reviews)
    except (TypeError, ValueError):
        reviews = 0

    return {
        "name":                   raw.get("title", ""),
        "formatted_phone_number": phone,
        "formatted_address":      raw.get("address", ""),
        "website":                raw.get("website", ""),
        "rating":                 rating,
        "user_ratings_total":     reviews,
        "types":                  [raw.get("type", "")],
        "place_id":               raw.get("place_id", ""),
        "_source":                "serpapi",
        "_raw":                   raw,
    }


async def run_serpapi_prospecting(
    api_key: str,
    gemini_api_key: str,
    crm,
    notify_callback=None,
    max_queries: int = 4,
) -> list[dict]:
    """
    Full SerpAPI prospecting cycle:
    1. Fetch places via SerpAPI for each query
    2. Deduplicate by phone vs. CRM
    3. Score with ICP engine
    4. Classify + draft message with Gemini (same as Outscraper engine)
    5. Save to CRM + notify owner

    Uses `max_queries` to limit API calls per run.
    """
    from processors.google_maps_engine import (
        classify_and_draft,
        build_daily_approval_list,
    )
    from processors.icp_engine import score_lead_icp, detect_buying_signals
    from models.lead import Lead as _Lead

    import asyncio

    # التناوب مشتق من رقم اليوم — يغطّي القائمة عبر الأيام بلا حالة مخزَّنة
    from datetime import date as _date
    queries = select_queries(max_queries, rotation=_date.today().toordinal())
    all_places: list[dict] = []

    # 1. Fetch all queries concurrently
    fetch_tasks = [fetch_places_serpapi(q, api_key) for q in queries]
    batches = await asyncio.gather(*fetch_tasks, return_exceptions=True)

    for batch in batches:
        if isinstance(batch, list):
            all_places.extend([normalize_serpapi_place(p) for p in batch])

    logger.info(f"SerpAPI: fetched {len(all_places)} places across {len(queries)} queries")

    # 2. Filter: must have phone + name
    valid_places = [
        p for p in all_places
        if p.get("name") and p.get("formatted_phone_number")
    ]
    logger.info(f"SerpAPI: {len(valid_places)} places with phone")

    if not valid_places:
        return []

    # 3. Deduplicate against CRM
    from crm.supabase_crm import SupabaseCRM
    if isinstance(crm, SupabaseCRM):
        deduped = []
        for place in valid_places:
            try:
                existing = await crm.search_lead(phone=place["formatted_phone_number"])
                if not existing:
                    deduped.append(place)
            except Exception:
                deduped.append(place)
        valid_places = deduped
        logger.info(f"SerpAPI: {len(valid_places)} new after CRM dedup")

    if not valid_places:
        return []

    # 4. Classify with Gemini (reuse google_maps_engine classify function)
    semaphore = asyncio.Semaphore(5)
    classify_tasks = [classify_and_draft(p, semaphore, gemini_api_key) for p in valid_places]
    raw_results = await asyncio.gather(*classify_tasks, return_exceptions=True)

    all_results: list[dict] = []
    for place, result in zip(valid_places, raw_results):
        if isinstance(result, Exception) or not result:
            continue

        # 5. ICP scoring
        _icp_score, _icp_segment, _signals = 0, None, []
        try:
            _tmp = _Lead(
                name=place.get("name", "unnamed"),
                phone=place.get("formatted_phone_number") or None,
                email="noreply@placeholder.com" if not place.get("formatted_phone_number") else None,
                raw_data={
                    "rating":        place.get("rating"),
                    "review_count":  place.get("user_ratings_total"),
                    "gemini_category": result.get("category", ""),
                },
            )
            _icp_score, _icp_segment = score_lead_icp(_tmp)
            _signals = detect_buying_signals(_tmp)
        except Exception as _e:
            logger.warning(f"SerpAPI ICP error [{place.get('name')}]: {_e}")

        # 6. Save to CRM
        try:
            lead_data = LeadCreate(
                name=place.get("name", ""),
                phone=place.get("formatted_phone_number", ""),
                source=LeadSource.SERPAPI_PROSPECTING,
                category="food_transport",
                score=result.get("score", 50),
                priority=result.get("priority", "medium"),
                icp_score=_icp_score,
                icp_segment=_icp_segment,
                buying_signals=_signals,
                raw_data={
                    "place":         {k: v for k, v in place.items() if k != "_raw"},
                    "draft_message": result.get("message", ""),
                    "reason":        result.get("reason", ""),
                    "gemini_category": result.get("category", ""),
                    "prospecting_source": "serpapi",
                    "collected_at":  datetime.now().isoformat(),
                },
            )
            await crm.create_lead(lead_data)
        except Exception as exc:
            logger.error(f"SerpAPI CRM save error [{place.get('name')}]: {exc}")

        all_results.append(result)

    logger.info(f"SerpAPI: saved {len(all_results)} new leads")

    # 7. Notify owner
    if notify_callback and all_results:
        try:
            msg = await build_daily_approval_list(all_results)
            await notify_callback(f"📡 *SerpAPI Prospecting*\n{msg}")
        except Exception as exc:
            logger.warning(f"SerpAPI notify error: {exc}")

    return all_results


# ─── Content Intelligence (SerpAPI for Social Media) ──────────────────────────

_INDUSTRY_NEWS_QUERIES = [
    "نقل مبرد السعودية",
    "Cold Chain لوجستيات الرياض",
    "أغذية فاخرة سلسلة التوريد السعودية",
]

_SAUDI_FOOD_TREND_QUERIES = [
    "مطاعم الرياض 2025",
    "محامص قهوة متخصصة السعودية",
    "مطابخ سحابية الرياض",
]


_TREND_KEYWORDS = [
    "نقل مبرد",
    "كيتيرينج",
    "توصيل مبرد",
    "مطابخ سحابية",
    "سلسلة التبريد",
]


async def fetch_saudi_google_trends(api_key: str, max_results: int = 5) -> list[str]:
    """
    جلب مستوى اهتمام السوق السعودي بمواضيع Cold Chain.
    يستخدم interest_over_time من Google Trends ويحوّله إلى رؤى نصية
    — الـ API لا يُعيد related_queries إلا عند استعلام واحد فقط بعض الأحيان.
    """
    if not api_key:
        return []

    params = {
        "engine": "google_trends",
        "q": ",".join(_TREND_KEYWORDS),
        "geo": "SA",
        "date": "today 3-m",
        "hl": "ar",
        "api_key": api_key,
    }
    async with httpx.AsyncClient(timeout=25) as client:
        try:
            resp = await client.get(SERPAPI_URL, params=params)
            if resp.status_code != 200:
                logger.warning(f"SerpAPI trends {resp.status_code}: {resp.text[:120]}")
                return []
            data = resp.json()

            timeline: list[dict] = data.get("interest_over_time", {}).get("timeline_data", [])
            if not timeline:
                return []

            # Accumulate interest per keyword across the 3-month timeline
            totals: dict[str, int] = {k: 0 for k in _TREND_KEYWORDS}
            for point in timeline:
                if point.get("partial_data"):
                    continue
                for v in point.get("values", []):
                    q = v.get("query", "")
                    if q in totals:
                        totals[q] += v.get("extracted_value", 0)

            ranked = sorted(totals.items(), key=lambda x: x[1], reverse=True)

            topics: list[str] = []
            for keyword, score in ranked:
                if score > 0:
                    topics.append(f"{keyword} (اهتمام: {score})")
                elif not topics:
                    # All zeros → at least include the top keyword as context
                    topics.append(f"{keyword} (بحث نادر في SA حالياً)")

            logger.info(f"SerpAPI trends: {len(topics)} SA topics extracted from interest_over_time")
            return topics[:max_results]

        except Exception as exc:
            logger.error(f"SerpAPI trends error: {exc}")
            return []


async def fetch_industry_news(api_key: str, max_results: int = 4) -> list[dict]:
    """
    جلب أخبار صناعة Cold Chain والأغذية في السعودية — engine: google_news
    Returns list of {title, source, date} dicts.
    """
    if not api_key:
        return []

    all_news: list[dict] = []
    queries = _INDUSTRY_NEWS_QUERIES[:2]

    fetch_tasks = []
    async with httpx.AsyncClient(timeout=20) as client:
        for query in queries:
            params = {
                "engine": "google_news",
                "q": query,
                "gl": "sa",
                "hl": "ar",
                "api_key": api_key,
            }
            try:
                resp = await client.get(SERPAPI_URL, params=params)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                for item in data.get("news_results", [])[:3]:
                    source = item.get("source", {})
                    all_news.append({
                        "title": item.get("title", ""),
                        "source": source.get("name", "") if isinstance(source, dict) else str(source),
                        "date": item.get("date", ""),
                    })
            except Exception as exc:
                logger.error(f"SerpAPI news error [{query}]: {exc}")

    unique: list[dict] = []
    seen_titles: set[str] = set()
    for item in all_news:
        title = item.get("title", "")
        if title and title not in seen_titles:
            seen_titles.add(title)
            unique.append(item)

    logger.info(f"SerpAPI news: fetched {len(unique)} industry headlines")
    return unique[:max_results]


async def fetch_food_trends(api_key: str, max_results: int = 3) -> list[str]:
    """
    جلب نتائج بحث عن ترندات الأغذية والمطاعم السعودية — engine: google
    يُستخدم لتغذية الـ AI بثقافة السوق الحالية.
    """
    if not api_key:
        return []

    results: list[str] = []
    async with httpx.AsyncClient(timeout=20) as client:
        for query in _SAUDI_FOOD_TREND_QUERIES[:2]:
            params = {
                "engine": "google",
                "q": query,
                "gl": "sa",
                "hl": "ar",
                "num": 5,
                "api_key": api_key,
            }
            try:
                resp = await client.get(SERPAPI_URL, params=params)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                for r in data.get("organic_results", [])[:2]:
                    snippet = r.get("snippet", "") or r.get("title", "")
                    if snippet:
                        results.append(snippet[:120])
            except Exception as exc:
                logger.error(f"SerpAPI food trends error [{query}]: {exc}")

    logger.info(f"SerpAPI food trends: fetched {len(results)} snippets")
    return results[:max_results]
