"""
Prospecting Engine — محرك البحث عن العملاء

Outbound prospecting: Claude searches for potential clients every morning.

Target segments:
- مصانع أغذية (Food Factories)
- مطاعم مركزية وCloud Kitchens
- شركات تموين (Catering)
- مستودعات تبريد (Cold Storage)
- Specialty Food Brands (شوكولاتة، جبن، بيكري)
- Food Suppliers B2B
- متاجر إلكترونية (Online Food Stores)
- صيدليات وموزعو أدوية (Pharma)
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)

PROSPECT_SEGMENTS = [
    {
        "id": "food_factories",
        "name_ar": "مصانع الأغذية",
        "keywords": ["food factory", "مصنع غذاء", "food manufacturing", "food processing", "مصنع مواد غذائية"],
        "cities": ["الرياض", "جدة", "الدمام", "القصيم"],
        "priority": "high",
        "expected_fleet": "5-20",
        "expected_budget": "30000-100000",
    },
    {
        "id": "cloud_kitchens",
        "name_ar": "المطابخ السحابية والمطاعم المركزية",
        "keywords": ["cloud kitchen", "مطبخ سحابي", "central kitchen", "مطبخ مركزي", "dark kitchen", "ghost kitchen"],
        "cities": ["الرياض", "جدة"],
        "priority": "high",
        "expected_fleet": "2-8",
        "expected_budget": "15000-50000",
    },
    {
        "id": "pharma_distributors",
        "name_ar": "موزعو الأدوية والمستلزمات الطبية",
        "keywords": ["pharma distributor", "موزع أدوية", "medical supplies", "مستلزمات طبية", "دواء توزيع"],
        "cities": ["الرياض", "جدة", "الدمام"],
        "priority": "high",
        "expected_fleet": "3-15",
        "expected_budget": "25000-80000",
    },
    {
        "id": "catering_companies",
        "name_ar": "شركات التموين",
        "keywords": ["catering company", "شركة تموين", "food catering", "تموين غذاء", "catering services"],
        "cities": ["الرياض", "جدة", "مكة", "المدينة", "الدمام"],
        "priority": "medium",
        "expected_fleet": "3-10",
        "expected_budget": "20000-60000",
    },
    {
        "id": "specialty_brands",
        "name_ar": "علامات متخصصة (شوكولاتة، جبن، بيكري)",
        "keywords": ["chocolate brand", "علامة شوكولاتة", "artisan cheese", "bakery brand", "specialty food brand", "gourmet food"],
        "cities": ["الرياض", "جدة"],
        "priority": "medium",
        "expected_fleet": "1-5",
        "expected_budget": "10000-30000",
    },
    {
        "id": "food_suppliers",
        "name_ar": "مزوّدو الخامات الغذائية B2B",
        "keywords": ["food supplier", "مزود خامات", "food ingredients supplier", "مزود مواد غذائية", "wholesale food", "جملة مواد غذائية"],
        "cities": ["الرياض", "جدة", "الدمام"],
        "priority": "medium",
        "expected_fleet": "5-15",
        "expected_budget": "20000-70000",
    },
    {
        "id": "online_food_stores",
        "name_ar": "المتاجر الإلكترونية للأغذية",
        "keywords": ["online food store", "متجر أغذية إلكتروني", "food e-commerce", "تجارة غذاء إلكترونية", "grocery delivery", "توصيل بقالة"],
        "cities": ["الرياض", "جدة"],
        "priority": "low",
        "expected_fleet": "2-8",
        "expected_budget": "10000-30000",
    },
    {
        "id": "cold_storage",
        "name_ar": "مستودعات التبريد",
        "keywords": ["cold storage", "مستودع تبريد", "refrigerated warehouse", "مستودع مبرد", "cold chain"],
        "cities": ["الرياض", "جدة", "الدمام", "الجبيل"],
        "priority": "medium",
        "expected_fleet": "5-20",
        "expected_budget": "30000-100000",
    },
]


PROSPECTING_PROMPT = """أنت وكيل استشاري متخصص في إيجاد فرص عمل لشركة Smart Field للنقل المبرد.

مهمتك اليوم: البحث عن {count} فرصة محتملة في قطاع **{segment_ar}**.

للكل فرصة، اكتشف أو افترض:
1. اسم الشركة المحتملة (يمكن أن تكون حقيقية أو نموذجية)
2. المدينة
3. نوع النشاط بالتفصيل
4. لماذا يحتاجون النقل المبرد
5. الأسطول التقريبي الذي قد يحتاجونه
6. الميزانية الشهرية التقديرية

أعطِ النتيجة بصيغة JSON array:
```json
[
  {{
    "company_name": "اسم الشركة",
    "city": "المدينة",
    "business_type": "نوع النشاط",
    "reason_needs_cold": "سبب الحاجة للتبريد",
    "estimated_fleet": 5,
    "estimated_budget": 35000,
    "contact_strategy": "كيف نتواصل معهم",
    "score": 75
  }}
]
```

ركّز على شركات في: {cities}
الكلمات المفتاحية: {keywords}

أعطِ {count} فرصة واقعية وقابلة للتواصل."""


class ProspectingEngine:
    """
    Daily outbound prospecting agent.
    يبحث عن عملاء محتملين كل صباح ويدخلهم في pipeline.
    """

    def __init__(self, config: Any, pipeline: Any) -> None:
        self.config = config
        self.pipeline = pipeline
        self._log = logger.bind(component="ProspectingEngine")

    async def run_morning_prospecting(self, prospects_per_segment: int = 3) -> dict:
        """
        Main prospecting job — runs every morning at 8:00 AM.
        يعمل كل صباح الساعة 8:00 ويولد فرصاً جديدة.
        """
        self._log.info("Morning prospecting started", segments=len(PROSPECT_SEGMENTS))
        results = {"total_prospects": 0, "segments_covered": 0, "errors": []}

        for segment in PROSPECT_SEGMENTS:
            try:
                prospects = await self._prospect_segment(segment, prospects_per_segment)
                results["total_prospects"] += len(prospects)
                results["segments_covered"] += 1

                # Feed into pipeline as outbound leads
                for p in prospects:
                    await self._submit_prospect(p, segment)

            except Exception as exc:
                self._log.error("Segment prospecting failed", segment=segment["id"], error=str(exc))
                results["errors"].append(f"{segment['id']}: {str(exc)[:100]}")

        self._log.info("Morning prospecting complete", **results)
        return results

    async def _prospect_segment(self, segment: dict, count: int) -> list[dict]:
        """Use Claude to generate prospects for a segment."""
        import anthropic

        prompt = PROSPECTING_PROMPT.format(
            count=count,
            segment_ar=segment["name_ar"],
            cities="، ".join(segment["cities"]),
            keywords="، ".join(segment["keywords"][:4]),
        )

        client = anthropic.AsyncAnthropic(api_key=self.config.ANTHROPIC_API_KEY)
        message = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )

        text = message.content[0].text
        # Extract JSON
        import re
        match = re.search(r'\[[\s\S]+\]', text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        return []

    async def _submit_prospect(self, prospect: dict, segment: dict) -> None:
        """Submit a discovered prospect to the lead pipeline."""
        from models.lead import LeadCreate, LeadSource

        try:
            lead_create = LeadCreate(
                name=prospect.get("company_name", "شركة محتملة"),
                company=prospect.get("company_name"),
                phone="+966500000000",  # placeholder — needs research
                source=LeadSource.MANUAL,
                cargo_type=prospect.get("business_type", segment["name_ar"]),
                fleet_size_needed=int(prospect.get("estimated_fleet") or 1),
                budget_monthly=float(prospect.get("estimated_budget") or 0),
                notes=f"[Outbound Prospecting] {prospect.get('reason_needs_cold', '')} | Strategy: {prospect.get('contact_strategy', '')}",
                raw_data={
                    "source_type": "outbound_prospecting",
                    "segment": segment["id"],
                    "prospect_score": prospect.get("score", 0),
                    "city": prospect.get("city", ""),
                    "contact_strategy": prospect.get("contact_strategy", ""),
                    "generated_at": datetime.utcnow().isoformat(),
                },
            )
            await self.pipeline.process(lead_create)
            self._log.info("Prospect submitted", company=lead_create.company, segment=segment["id"])

        except Exception as exc:
            self._log.warning("Failed to submit prospect", error=str(exc))

    async def get_prospecting_report(self) -> str:
        """Generate a summary report of today's prospecting."""
        return f"""📊 *تقرير Prospecting اليومي*
التاريخ: {datetime.now().strftime('%Y/%m/%d')}

القطاعات المستهدفة: {len(PROSPECT_SEGMENTS)}
• مصانع الأغذية 🏭
• المطابخ السحابية 🍳
• موزعو الأدوية 💊
• شركات التموين 🍱
• علامات متخصصة 🧁
• مزوّدو الخامات 📦
• متاجر إلكترونية 🛒
• مستودعات التبريد 🏬

سيتم إرسال التفاصيل في التقرير الصباحي."""
