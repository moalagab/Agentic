"""
SerpAPI Prospecting Engine — Google Maps local results via SerpAPI
مصدر ثانٍ للتنقيب بجانب Outscraper — يستخدم SerpAPI لاستخراج أماكن Google Maps
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
SERPAPI_QUERIES = [
    "صيدلية الرياض",
    "مستشفى خاص الرياض",
    "محل لحوم فاخرة الرياض",
    "مطعم فندقي الرياض",
    "موزع أغذية الرياض",
    "شركة تموين الرياض",
    "مخزن تبريد الرياض",
    "مطعم سلسلة الرياض",
    "مورد خضار وفواكه الرياض",
    "كيتيرينج شركات الرياض",
    "محل بقالة فاخرة الرياض",
    "عيادة تجميل الرياض",
]


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

    queries = SERPAPI_QUERIES[:max_queries]
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
