"""
Prospecting Engine — محرك البحث عن العملاء

يبحث يومياً عن 100+ فرصة محتملة في قطاعات النقل المبرد.

Target segments:
- مصانع الأغذية
- المطابخ السحابية والمطاعم المركزية
- شركات التموين
- موزعو الأدوية والمستلزمات الطبية
- سلاسل التجزئة والهايبرماركت
- مستودعات التبريد
- علامات غذائية متخصصة (شوكولاتة، جبن، بيكري)
- مزوّدو الخامات الغذائية B2B
- متاجر إلكترونية للأغذية
- مطاعم مركزية وفنادق
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# ─── Segments (13 segments × 8 prospects = 104/day) ──────────────────────────

PROSPECT_SEGMENTS = [
    {
        "id": "food_factories",
        "name_ar": "مصانع الأغذية",
        "description": "مصانع تصنيع وتعبئة المواد الغذائية المبردة والمجمدة",
        "keywords": ["مصنع غذاء", "food factory", "food processing", "تصنيع غذائي", "مصنع مواد غذائية"],
        "cities": ["الرياض", "جدة", "الدمام", "القصيم", "الجبيل"],
        "priority": "high",
        "expected_fleet": "5-20",
        "expected_budget_sar": "30000-100000",
        "score_base": 75,
    },
    {
        "id": "pharma_distributors",
        "name_ar": "موزعو الأدوية والمستلزمات الطبية",
        "description": "شركات توزيع أدوية ولقاحات ومستلزمات طبية حساسة للحرارة",
        "keywords": ["موزع أدوية", "pharma distributor", "medical supplies", "توزيع أدوية", "مستودع دواء"],
        "cities": ["الرياض", "جدة", "الدمام", "المدينة"],
        "priority": "high",
        "expected_fleet": "3-15",
        "expected_budget_sar": "25000-80000",
        "score_base": 80,
    },
    {
        "id": "cloud_kitchens",
        "name_ar": "المطابخ السحابية والمطاعم المركزية",
        "description": "مطابخ إنتاج مركزي تخدم فروع متعددة",
        "keywords": ["cloud kitchen", "مطبخ سحابي", "central kitchen", "مطبخ مركزي", "dark kitchen"],
        "cities": ["الرياض", "جدة", "الدمام"],
        "priority": "high",
        "expected_fleet": "2-8",
        "expected_budget_sar": "15000-50000",
        "score_base": 70,
    },
    {
        "id": "catering_companies",
        "name_ar": "شركات التموين",
        "description": "شركات تموين مؤسسي وطيران وفعاليات",
        "keywords": ["شركة تموين", "catering company", "تموين طيران", "تموين مؤسسي", "food catering"],
        "cities": ["الرياض", "جدة", "الدمام", "مكة", "المدينة"],
        "priority": "high",
        "expected_fleet": "3-12",
        "expected_budget_sar": "20000-70000",
        "score_base": 72,
    },
    {
        "id": "retail_chains",
        "name_ar": "سلاسل التجزئة والهايبرماركت",
        "description": "سلاسل سوبرماركت وهايبرماركت تحتاج توزيع مبرد لفروعها",
        "keywords": ["hypermarket", "هايبرماركت", "supermarket", "سوبرماركت", "سلسلة تجزئة", "retail chain"],
        "cities": ["الرياض", "جدة", "الدمام", "مكة", "القصيم"],
        "priority": "high",
        "expected_fleet": "5-25",
        "expected_budget_sar": "40000-150000",
        "score_base": 78,
    },
    {
        "id": "cold_storage",
        "name_ar": "مستودعات التبريد",
        "description": "شركات تشغيل مستودعات مبردة تحتاج نقل بين المستودعات والعملاء",
        "keywords": ["cold storage", "مستودع تبريد", "مستودع مبرد", "refrigerated warehouse", "cold chain"],
        "cities": ["الرياض", "جدة", "الدمام", "الجبيل"],
        "priority": "medium",
        "expected_fleet": "5-20",
        "expected_budget_sar": "30000-100000",
        "score_base": 68,
    },
    {
        "id": "food_suppliers",
        "name_ar": "مزوّدو الخامات الغذائية B2B",
        "description": "موردو خامات ومكونات غذائية للمطاعم والمصانع",
        "keywords": ["food supplier", "مزود خامات", "food ingredients", "مزود مواد غذائية", "wholesale food"],
        "cities": ["الرياض", "جدة", "الدمام"],
        "priority": "medium",
        "expected_fleet": "3-15",
        "expected_budget_sar": "20000-70000",
        "score_base": 65,
    },
    {
        "id": "specialty_brands",
        "name_ar": "العلامات الغذائية المتخصصة",
        "description": "شوكولاتة، جبن حرفي، بيكري راقٍ، منتجات ألبان متخصصة",
        "keywords": ["artisan chocolate", "شوكولاتة", "جبن متخصص", "artisan cheese", "bakery brand", "gourmet food"],
        "cities": ["الرياض", "جدة"],
        "priority": "medium",
        "expected_fleet": "1-5",
        "expected_budget_sar": "8000-30000",
        "score_base": 60,
    },
    {
        "id": "online_food",
        "name_ar": "المتاجر الإلكترونية للأغذية",
        "description": "متاجر بيع غذاء طازج ومجمد عبر الإنترنت",
        "keywords": ["online grocery", "توصيل بقالة", "grocery delivery", "متجر طازج أونلاين", "food e-commerce"],
        "cities": ["الرياض", "جدة"],
        "priority": "medium",
        "expected_fleet": "2-8",
        "expected_budget_sar": "10000-35000",
        "score_base": 58,
    },
    {
        "id": "hotels_hospitality",
        "name_ar": "الفنادق والضيافة",
        "description": "فنادق 4 و5 نجوم تحتاج توريد مبرد منتظم",
        "keywords": ["فندق", "hotel", "resort", "منتجع", "hospitality", "ضيافة"],
        "cities": ["الرياض", "جدة", "مكة", "المدينة", "أبها"],
        "priority": "medium",
        "expected_fleet": "2-10",
        "expected_budget_sar": "15000-60000",
        "score_base": 63,
    },
    {
        "id": "meat_poultry",
        "name_ar": "تجار اللحوم والدواجن",
        "description": "موزعون وتجار لحوم ودواجن بالجملة",
        "keywords": ["تاجر لحوم", "meat trader", "دواجن", "poultry", "butcher", "جزار", "slaughterhouse"],
        "cities": ["الرياض", "جدة", "الدمام", "القصيم", "تبوك"],
        "priority": "high",
        "expected_fleet": "3-15",
        "expected_budget_sar": "20000-80000",
        "score_base": 73,
    },
    {
        "id": "seafood",
        "name_ar": "موزعو المأكولات البحرية",
        "description": "تجار وموزعو أسماك ومأكولات بحرية طازجة ومجمدة",
        "keywords": ["أسماك", "seafood", "مأكولات بحرية", "fish market", "سمك", "سوق السمك"],
        "cities": ["جدة", "الدمام", "الرياض", "ينبع"],
        "priority": "medium",
        "expected_fleet": "2-10",
        "expected_budget_sar": "15000-50000",
        "score_base": 66,
    },
    {
        "id": "dairy_producers",
        "name_ar": "منتجو ومورّدو الألبان",
        "description": "شركات إنتاج وتوزيع منتجات الألبان والأجبان",
        "keywords": ["منتجات ألبان", "dairy products", "أجبان", "cheese producer", "laban", "لبن"],
        "cities": ["الرياض", "القصيم", "الدمام", "جدة"],
        "priority": "medium",
        "expected_fleet": "3-12",
        "expected_budget_sar": "18000-60000",
        "score_base": 67,
    },
]

# ─── Prompts ──────────────────────────────────────────────────────────────────

PROSPECTING_PROMPT = """\
أنت خبير في تطوير الأعمال لشركة سمارت فيلد للنقل المبرد بالمملكة العربية السعودية.

مهمتك: اذكر {count} شركة أو مؤسسة محتملة في قطاع **{segment_ar}** قد تحتاج خدمات نقل مبرد.

لكل شركة، أعطِ:
1. اسم الشركة (أسماء حقيقية معروفة في السوق السعودي إن أمكن، أو نموذجية)
2. المدينة
3. وصف النشاط ولماذا يحتاج نقلاً مبرداً
4. تقدير عدد الشاحنات المطلوبة شهرياً
5. تقدير الميزانية الشهرية بالريال
6. أفضل طريقة للتواصل معهم (واتساب، LinkedIn، زيارة ميدانية، إلخ)
7. درجة احتمالية الاهتمام (0-100)

القطاعات المستهدفة في: {cities}
كلمات دالة على القطاع: {keywords}

أعطِ النتيجة حصراً بصيغة JSON array (بدون أي نص إضافي):
[
  {{
    "company": "اسم الشركة",
    "city": "المدينة",
    "activity": "وصف النشاط",
    "cold_need": "سبب الحاجة للتبريد",
    "fleet_est": 5,
    "budget_sar": 35000,
    "outreach": "طريقة التواصل",
    "score": 75
  }}
]

أعطِ {count} فرصة متنوعة ومختلفة الأحجام (صغيرة ومتوسطة وكبيرة).\
"""


class ProspectingEngine:
    """
    Daily outbound prospecting agent.
    يبحث عن 100+ عميل محتمل كل صباح ويدخلهم في pipeline.
    """

    def __init__(self, config: Any, pipeline: Any) -> None:
        self.config = config
        self.pipeline = pipeline
        self._log = logger.bind(component="ProspectingEngine")

    async def run_morning_prospecting(self, target_total: int = 104) -> dict:
        """
        Main daily prospecting job — runs every morning at 8:00 AM.
        Generates target_total prospects spread across all segments.
        """
        per_segment = max(4, target_total // len(PROSPECT_SEGMENTS))
        self._log.info("Morning prospecting started", segments=len(PROSPECT_SEGMENTS), per_segment=per_segment)

        results = {
            "total_prospects": 0,
            "segments_covered": 0,
            "added_to_crm": 0,
            "errors": [],
            "date": datetime.utcnow().strftime("%Y-%m-%d"),
        }

        # Run segments in parallel batches of 3 to avoid rate limits
        segment_batches = [
            PROSPECT_SEGMENTS[i:i+3]
            for i in range(0, len(PROSPECT_SEGMENTS), 3)
        ]

        for batch in segment_batches:
            batch_tasks = [
                self._prospect_segment(segment, per_segment)
                for segment in batch
            ]
            batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)

            for segment, prospects in zip(batch, batch_results):
                if isinstance(prospects, Exception):
                    self._log.error("Segment failed", segment=segment["id"], error=str(prospects))
                    results["errors"].append(f"{segment['id']}: {str(prospects)[:80]}")
                    continue

                results["total_prospects"] += len(prospects)
                results["segments_covered"] += 1

                # Submit to pipeline
                for p in prospects:
                    ok = await self._submit_prospect(p, segment)
                    if ok:
                        results["added_to_crm"] += 1

            await asyncio.sleep(2)  # Rate limit buffer between batches

        self._log.info("Morning prospecting complete", **{k: v for k, v in results.items() if k != "errors"})
        return results

    async def _prospect_segment(self, segment: dict, count: int) -> list[dict]:
        """Use Claude to generate prospects for a specific segment."""
        import anthropic

        prompt = PROSPECTING_PROMPT.format(
            count=count,
            segment_ar=segment["name_ar"],
            cities="، ".join(segment["cities"]),
            keywords="، ".join(segment["keywords"][:5]),
        )

        client = anthropic.AsyncAnthropic(api_key=self.config.ANTHROPIC_API_KEY)
        message = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=3000,
            messages=[{"role": "user", "content": prompt}],
        )

        text = message.content[0].text.strip()

        # Extract JSON array
        match = re.search(r'\[[\s\S]+\]', text)
        if match:
            try:
                prospects = json.loads(match.group())
                self._log.info("Segment prospected", segment=segment["id"], count=len(prospects))
                return prospects
            except json.JSONDecodeError as e:
                self._log.warning("JSON parse failed", segment=segment["id"], error=str(e))

        return []

    async def _submit_prospect(self, prospect: dict, segment: dict) -> bool:
        """Submit a discovered prospect to the lead pipeline."""
        from models.lead import LeadCreate, LeadSource

        try:
            fleet = int(prospect.get("fleet_est") or 1)
            budget = float(prospect.get("budget_sar") or 0)

            lead_create = LeadCreate(
                name=prospect.get("company", "شركة محتملة"),
                company=prospect.get("company"),
                phone="+966500000000",  # placeholder — needs manual research or web lookup
                source=LeadSource.MANUAL,
                cargo_type=prospect.get("activity", segment["name_ar"]),
                fleet_size_needed=fleet,
                budget_monthly=budget,
                notes=(
                    f"[Prospecting: {segment['name_ar']}] "
                    f"{prospect.get('cold_need', '')} | "
                    f"مدينة: {prospect.get('city', '')} | "
                    f"تواصل: {prospect.get('outreach', '')}"
                ),
                raw_data={
                    "source_type": "outbound_prospecting",
                    "segment_id": segment["id"],
                    "segment_ar": segment["name_ar"],
                    "prospect_score": prospect.get("score", segment["score_base"]),
                    "city": prospect.get("city", ""),
                    "outreach_strategy": prospect.get("outreach", ""),
                    "cold_need": prospect.get("cold_need", ""),
                    "generated_at": datetime.utcnow().isoformat(),
                },
            )
            await self.pipeline.process(lead_create)
            self._log.info("Prospect submitted", company=lead_create.company, city=prospect.get("city"))
            return True

        except Exception as exc:
            self._log.warning("Failed to submit prospect", company=prospect.get("company"), error=str(exc))
            return False

    async def get_prospecting_report(self, results: dict | None = None) -> str:
        """Generate a concise report of today's prospecting."""
        if results:
            total = results.get("total_prospects", 0)
            added = results.get("added_to_crm", 0)
            segments = results.get("segments_covered", 0)
            errors = len(results.get("errors", []))
            date = results.get("date", datetime.now().strftime("%Y-%m-%d"))
            return (
                f"*Prospecting اليومي — {date}*\n"
                f"━━━━━━━━━━━━━━\n"
                f"فرص مكتشفة: {total}\n"
                f"أضيفت للـ CRM: {added}\n"
                f"قطاعات مغطاة: {segments}/{len(PROSPECT_SEGMENTS)}\n"
                + (f"أخطاء: {errors}\n" if errors else "")
            )

        segments_ar = "\n".join(f"  · {s['name_ar']}" for s in PROSPECT_SEGMENTS)
        return (
            f"*خطة Prospecting اليومي*\n"
            f"━━━━━━━━━━━━━━\n"
            f"الهدف: 100+ فرصة / يوم\n"
            f"القطاعات ({len(PROSPECT_SEGMENTS)}):\n"
            f"{segments_ar}"
        )
