"""
Content Engine — محرك المحتوى (Layer 7 - RevOS v6)

يحوّل بيانات المبيعات والسوق إلى محتوى يُدر عملاء:
- منشورات X (Twitter) — 7 أسبوعياً
- ريلز Instagram — 3 أسبوعياً
- منشورات Instagram — 2 أسبوعياً
- منشورات LinkedIn — حسب الطلب
- رسائل بريد / واتساب للمتابعة

كل محتوى يُرفع على Buffer كـ draft ويُرسل إشعار للمالك.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any, Optional

import httpx
import structlog
from agent.ai_client import get_text

logger = structlog.get_logger(__name__)

KNOWLEDGE_DIR = Path(__file__).parent.parent / "knowledge"

BUFFER_API = "https://api.bufferapp.com/1"
BUFFER_GQL = "https://api.buffer.com/graphql"

# Saudi Arabia WOEID for X trending topics (Twitter v1.1)
_SA_WOEID = 23424938


def _load_brand_guidelines() -> str:
    """Load brand guidelines from knowledge file at startup."""
    brand_file = KNOWLEDGE_DIR / "smart_field_brand_guidelines.md"
    try:
        return brand_file.read_text(encoding="utf-8")
    except Exception:
        return ""

_BRAND_GUIDELINES = _load_brand_guidelines()

CONTENT_SYSTEM_PROMPT = f"""\
أنت خبير تسويق B2B متخصص في قطاع اللوجستيات والنقل المبرد في المملكة العربية السعودية.
تعمل لحساب شركة Smart Field — متخصصة في النقل المبرد في الرياض.

━━━━━━━━━━━━━━━━━━━━
BRAND GUIDELINES (مرجع إلزامي — لا تتجاوزه أبداً):
{_BRAND_GUIDELINES}
━━━━━━━━━━━━━━━━━━━━

قواعد إلزامية:
- واثق + إنساني — يتكلم عن مشاكل السوق الحقيقية، لا عن الشركة
- لا ادعاءات بدون دليل تشغيلي — "المصداقية قبل الادعاء"
- لا محتوى دوائي أو طبي أبداً
- لا أرقام عملاء أو إحصائيات مختلقة
- لا corporate فارغ — ابتعد عن كل جملة في قسم "الصوت الخاطئ"
- لا B2C — الجمهور دايماً مدير تشغيل أو مدير مشتريات
- كل رسالة تحتوي رقماً واحداً محدداً كدليل
اللغة: عربية أساساً مع مصطلحات تقنية إنجليزية طبيعية (Cold Chain, B2B, Dashboard).
"""


# ─── Saudi Calendar ────────────────────────────────────────────────────────────

def _load_saudi_occasions() -> list[dict]:
    """Load upcoming Saudi occasions from knowledge/saudi_calendar.md."""
    cal_file = KNOWLEDGE_DIR / "saudi_calendar.md"
    occasions = []
    if not cal_file.exists():
        return occasions
    try:
        content = cal_file.read_text(encoding="utf-8")
        today = date.today()
        # Simple extraction: lines with | that have dates
        for line in content.splitlines():
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 3 and parts[1] and parts[2]:
                name = parts[1]
                date_hint = parts[2]
                occasions.append({"name": name, "date_hint": date_hint})
    except Exception:
        pass
    return occasions


def _get_upcoming_occasions(days_ahead: int = 14) -> list[str]:
    """Return names of occasions within the next N days (rough match on month)."""
    occasions = _load_saudi_occasions()
    today = date.today()
    upcoming = []
    for occ in occasions:
        dh = occ.get("date_hint", "")
        name = occ.get("name", "")
        if not dh or not name or "—" in name:
            continue
        # Check month match (MM-DD format)
        try:
            month_day = dh.strip().split("-")
            if len(month_day) == 2:
                m, d = int(month_day[0]), int(month_day[1])
                target = date(today.year, m, d)
                if target < today:
                    target = date(today.year + 1, m, d)
                if 0 <= (target - today).days <= days_ahead:
                    upcoming.append(name)
        except Exception:
            pass
    return upcoming


# ─── X Trending Topics ─────────────────────────────────────────────────────────

async def _get_x_trending(bearer_token: str, max_trends: int = 5) -> list[str]:
    """Fetch Saudi trending topics via X API v1.1."""
    if not bearer_token:
        return []
    url = f"https://api.twitter.com/1.1/trends/place.json?id={_SA_WOEID}"
    headers = {"Authorization": f"Bearer {bearer_token}"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url, headers=headers)
            if r.status_code != 200:
                logger.warning("x_trends.failed", status=r.status_code)
                return []
            data = r.json()
            trends = data[0].get("trends", []) if data else []
            # Filter: skip Twitter internal trends (#xxx or empty names)
            names = [
                t["name"] for t in trends
                if t.get("name") and not t["name"].startswith("#") or len(t["name"]) > 3
            ]
            return names[:max_trends]
    except Exception as exc:
        logger.warning("x_trends.error", error=str(exc))
        return []


# ─── Buffer Integration (GraphQL API) ─────────────────────────────────────────

async def upload_to_buffer(
    access_token: str,
    channel_id: str,
    text: str,
    scheduled_at: Optional[str] = None,
) -> dict:
    """
    Upload a single post to Buffer via GraphQL API (OIDC token).
    Creates an Idea draft — appears in Buffer's Ideas board for review.
    """
    if not access_token or not channel_id:
        return {"error": "Buffer not configured"}

    mutation = """
    mutation CreatePost($input: CreatePostInput!) {
      createPost(input: $input) {
        ... on PostActionSuccess {
          post { id status }
        }
      }
    }
    """
    variables: dict = {
        "input": {
            "channelId": channel_id,
            "text": text,
            "schedulingType": "automatic",
            "mode": "addToQueue" if not scheduled_at else "customScheduled",
        }
    }
    if scheduled_at:
        variables["input"]["dueAt"] = scheduled_at

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                BUFFER_GQL,
                json={"query": mutation, "variables": variables},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
            )
            result = r.json()
            post_data = result.get("data", {}).get("createPost", {}).get("post", {})
            if post_data.get("id"):
                logger.info("buffer.post_queued", channel=channel_id, post_id=post_data["id"])
                return {"success": True, "id": post_data["id"]}
            errors = result.get("errors", [{"message": "unknown"}])
            logger.warning("buffer.create_failed", errors=errors)
            return {"error": str(errors)}
    except Exception as exc:
        logger.error("buffer.upload_error", error=str(exc))
        return {"error": str(exc)}


async def _upload_batch_to_buffer(
    access_token: str,
    channel_id: str,
    posts: list[str],
    start_day_offset: int = 1,
) -> int:
    """Upload a list of posts to Buffer, one per day starting from tomorrow."""
    if not access_token or not channel_id:
        return 0
    uploaded = 0
    for i, post in enumerate(posts):
        post_date = datetime.utcnow() + timedelta(days=start_day_offset + i)
        scheduled_at = post_date.strftime("%Y-%m-%dT09:00:00+03:00")
        result = await upload_to_buffer(access_token, channel_id, post, scheduled_at)
        if "error" not in result:
            uploaded += 1
        await asyncio.sleep(0.5)
    return uploaded


# ─── Content Engine Class ──────────────────────────────────────────────────────

class ContentEngine:
    """
    AI-powered content generation engine.
    يولّد محتوى أسبوعي لـ X وInstagram ويرفعه على Buffer كـ drafts.
    يستخدم SerpAPI (اختياري) لجلب Saudi Google Trends وأخبار Cold Chain كسياق إضافي.
    """

    def __init__(self, gemini_api_key: str, serpapi_api_key: str = "") -> None:
        self.api_key = gemini_api_key
        self.serpapi_key = serpapi_api_key
        self._log = logger.bind(component="ContentEngine")

    # ── Weekly Social Content (NEW — Buffer integration) ────────────────────────

    async def generate_and_upload_weekly_content(
        self,
        buffer_token: str = "",
        x_channel_id: str = "",
        instagram_channel_id: str = "",
        tiktok_channel_id: str = "",
        x_bearer_token: str = "",
        pipeline_data: Optional[dict] = None,
    ) -> dict:
        """
        Main weekly entry point — called Saturday 7 PM.
        1. Fetch X trending topics
        2. Check upcoming Saudi occasions
        3. Generate X posts (7) + IG Reels (3) + IG Posts (2)
        4. Upload all to Buffer as drafts
        5. Return summary
        """
        self._log.info("content.weekly_generation_start")

        # Context gathering — X Trends + SerpAPI + Saudi Calendar
        trends = await _get_x_trending(x_bearer_token)
        occasions = _get_upcoming_occasions(days_ahead=14)

        google_trends: list[str] = []
        industry_news: list[dict] = []
        food_snippets: list[str] = []
        if self.serpapi_key:
            from processors.serpapi_engine import (
                fetch_saudi_google_trends,
                fetch_industry_news,
                fetch_food_trends,
            )
            google_trends, industry_news, food_snippets = await asyncio.gather(
                fetch_saudi_google_trends(self.serpapi_key),
                fetch_industry_news(self.serpapi_key),
                fetch_food_trends(self.serpapi_key),
                return_exceptions=False,
            )
            self._log.info(
                "content.serpapi_context_fetched",
                google_trends=len(google_trends),
                news=len(industry_news),
                snippets=len(food_snippets),
            )

        context = self._build_context(
            trends, occasions, pipeline_data,
            google_trends=google_trends,
            industry_news=industry_news,
            food_snippets=food_snippets,
        )

        # Generate content in parallel
        x_posts, ig_reels, ig_posts, tiktok_scripts = await asyncio.gather(
            self._generate_x_posts(7, context),
            self._generate_instagram_reels(3, context),
            self._generate_instagram_posts(2, context),
            self._generate_tiktok_scripts(3, context),
        )

        summary = {
            "x_posts": len(x_posts),
            "ig_reels": len(ig_reels),
            "ig_posts": len(ig_posts),
            "tiktok_scripts": len(tiktok_scripts),
            "buffer_uploads": 0,
            "trends_used": trends[:3],
            "google_trends_used": google_trends[:3],
            "industry_news_count": len(industry_news),
            "occasions_used": occasions,
        }

        # Upload to Buffer
        if buffer_token:
            if x_channel_id and x_posts:
                uploaded = await _upload_batch_to_buffer(buffer_token, x_channel_id, x_posts)
                summary["buffer_uploads"] += uploaded
                self._log.info("content.x_uploaded", count=uploaded)

            if instagram_channel_id and (ig_reels or ig_posts):
                uploaded = await _upload_batch_to_buffer(buffer_token, instagram_channel_id, ig_reels + ig_posts)
                summary["buffer_uploads"] += uploaded
                self._log.info("content.ig_uploaded", count=uploaded)

            if tiktok_channel_id and tiktok_scripts:
                uploaded = await _upload_batch_to_buffer(buffer_token, tiktok_channel_id, tiktok_scripts)
                summary["buffer_uploads"] += uploaded
                self._log.info("content.tiktok_uploaded", count=uploaded)
        else:
            self._save_content_locally(x_posts, ig_reels, ig_posts)

        self._log.info("content.weekly_generation_done", **{k: v for k, v in summary.items() if isinstance(v, int)})
        return summary

    def _build_context(
        self,
        trends: list[str],
        occasions: list[str],
        pipeline_data: Optional[dict],
        google_trends: Optional[list[str]] = None,
        industry_news: Optional[list[dict]] = None,
        food_snippets: Optional[list[str]] = None,
    ) -> str:
        parts = []

        # X Trending
        if trends:
            parts.append(f"ترندات X السعودية: {', '.join(trends[:3])}")

        # Google Trends (SerpAPI)
        if google_trends:
            parts.append(f"ترندات Google السعودية: {', '.join(google_trends[:3])}")

        # Industry News (SerpAPI)
        if industry_news:
            headlines = [n.get("title", "") for n in industry_news[:2] if n.get("title")]
            if headlines:
                parts.append(f"أخبار الصناعة: {' / '.join(headlines)}")

        # Food & Restaurant Trends (SerpAPI)
        if food_snippets:
            parts.append(f"ترندات السوق: {food_snippets[0][:100]}")

        # Saudi Occasions
        if occasions:
            parts.append(f"مناسبات قادمة: {', '.join(occasions)}")

        # Pipeline Data
        if pipeline_data:
            total = pipeline_data.get("total_leads", 0)
            if total:
                parts.append(f"لدينا {total} عميل محتمل هذا الأسبوع")

        return " | ".join(parts) if parts else "أسبوع عمل عادي"

    # ── X (Twitter) Posts ───────────────────────────────────────────────────────

    async def _generate_x_posts(self, count: int, context: str) -> list[str]:
        """Generate count X posts — max 280 chars, Arabic."""
        pillars = [
            "عمليات حقيقية من الميدان",
            "محتوى توعوي عن سلسلة التبريد",
            "محتوى إنساني — خلف الكواليس",
            "محتوى سعودي محلي مرتبط بالسوق",
            "محتوى تقني — تقنيات النقل المبرد",
            "ثقة بالبراند — قصة نجاح مختصرة",
            "سؤال يثير تفاعل الجمهور",
        ]

        posts = []
        for i in range(count):
            pillar = pillars[i % len(pillars)]
            prompt = f"""اكتب تغريدة (X Post) بالعربي عن Smart Field للنقل المبرد في الرياض.

المحور: {pillar}
السياق الأسبوعي: {context}

القواعد:
- أقصى 270 حرف (بدون الهاشتاقات)
- 2-3 هاشتاقات سعودية مناسبة في النهاية
- نبرة واثقة وإنسانية — لا corporate فارغ
- لا تبدأ بـ "سمارت فيلد" مباشرة — ابدأ بفكرة أو سؤال
- لا ادعاءات بدون دليل

اكتب التغريدة فقط بدون أي شرح."""

            try:
                post = await get_text(self.api_key, CONTENT_SYSTEM_PROMPT, prompt, max_tokens=200)
                post = post.strip()
                if post:
                    posts.append(post)
            except Exception as exc:
                self._log.warning("content.x_post_failed", pillar=pillar, error=str(exc))

        self._log.info("content.x_posts_generated", count=len(posts))
        return posts

    # ── Instagram Reels ─────────────────────────────────────────────────────────

    async def _generate_instagram_reels(self, count: int, context: str) -> list[str]:
        """Generate Reel scripts — hook + 30-60s narrative + CTA."""
        topics = [
            "يوم في حياة سائق شاحنة مبردة في الرياض",
            "لماذا 80% من المطاعم تخسر بسبب النقل الخاطئ",
            "كيف نضمن وصول الشوكولاتة كما خرجت من المصنع",
        ]

        scripts = []
        for i in range(min(count, len(topics))):
            topic = topics[i]
            prompt = f"""اكتب سكريبت Instagram Reel (30-60 ثانية) لـ Smart Field.

الموضوع: {topic}
السياق: {context}

الهيكل:
HOOK: [جملة صادمة أو سؤال — 3 ثوانٍ الأولى]
SCRIPT: [السرد الرئيسي — بسيط، حقيقي، بدون مبالغة]
CTA: [جملة ختامية واحدة]
HASHTAGS: [5-8 هاشتاقات]

اكتب السكريبت بالعربي فقط."""

            try:
                script = await get_text(self.api_key, CONTENT_SYSTEM_PROMPT, prompt, max_tokens=400)
                scripts.append(script.strip())
            except Exception as exc:
                self._log.warning("content.reel_failed", topic=topic, error=str(exc))

        self._log.info("content.reels_generated", count=len(scripts))
        return scripts

    # ── Instagram Posts ─────────────────────────────────────────────────────────

    async def _generate_instagram_posts(self, count: int, context: str) -> list[str]:
        """Generate Instagram post captions — 3-5 lines + hashtags."""
        types = ["قصة نجاح مختصرة", "محتوى توعوي عن سلسلة التبريد"]

        captions = []
        for i in range(min(count, len(types))):
            ptype = types[i]
            prompt = f"""اكتب كابشن Instagram لـ Smart Field للنقل المبرد.

النوع: {ptype}
السياق: {context}

القواعد:
- 3-5 أسطر — لا أكثر
- نبرة Brand: واثقة + إنسانية
- 5-8 هاشتاقات سعودية في النهاية
- لا تبدأ بـ "مرحباً" أو "أهلاً"
- لا ادعاءات مبالغ فيها

اكتب الكابشن فقط."""

            try:
                caption = await get_text(self.api_key, CONTENT_SYSTEM_PROMPT, prompt, max_tokens=250)
                captions.append(caption.strip())
            except Exception as exc:
                self._log.warning("content.ig_post_failed", type=ptype, error=str(exc))

        self._log.info("content.ig_posts_generated", count=len(captions))
        return captions

    # ── TikTok Scripts ──────────────────────────────────────────────────────────

    async def _generate_tiktok_scripts(self, count: int, context: str) -> list[str]:
        """Generate TikTok video scripts — 15-60s, hook-driven, vertical format."""
        topics = [
            "درجة حرارة واحدة غلط — كيف تتلف شحنة كاملة",
            "يوم في حياة سائق نقل مبرد بالرياض",
            "الفرق بين ناقل عادي وناقل مبرد محترف",
        ]

        scripts = []
        for i in range(min(count, len(topics))):
            topic = topics[i]
            prompt = f"""اكتب سكريبت TikTok (15-60 ثانية) لحساب @smartclog1 عن Smart Field للنقل المبرد.

الموضوع: {topic}
السياق الأسبوعي: {context}

القواعد:
- HOOK قوي في أول 2 ثانية — جملة واحدة صادمة أو سؤال مثير
- أسلوب خلف الكواليس — حقيقي ومباشر، لا مصطنع
- نبرة شبابية لكن مهنية — تناسب TikTok السعودي
- رقم واحد محدد كدليل (مثل: 90 دقيقة، درجة واحدة، 160 ريال)
- CTA في النهاية: متابعة أو تعليق أو مشاركة

الهيكل:
HOOK: [أول جملة — 2 ثانية]
SCENE 1: [15 ثانية]
SCENE 2: [15 ثانية]
SCENE 3: [15 ثانية — اختياري]
CTA: [جملة ختامية]
CAPTION: [كابشن TikTok مع هاشتاقات]

اكتب بالعربي فقط."""

            try:
                script = await get_text(self.api_key, CONTENT_SYSTEM_PROMPT, prompt, max_tokens=450)
                scripts.append(script.strip())
            except Exception as exc:
                self._log.warning("content.tiktok_failed", topic=topic, error=str(exc))

        self._log.info("content.tiktok_scripts_generated", count=len(scripts))
        return scripts

    def _save_content_locally(
        self, x_posts: list, ig_reels: list, ig_posts: list
    ) -> None:
        """Save generated content to a local markdown file when Buffer is not configured."""
        output_dir = KNOWLEDGE_DIR.parent / "generated_content"
        output_dir.mkdir(exist_ok=True)
        week = date.today().strftime("%Y-W%V")
        path = output_dir / f"content_{week}.md"
        lines = [
            f"# محتوى الأسبوع — {week}\n",
            "## X Posts\n",
        ]
        for i, post in enumerate(x_posts, 1):
            lines.append(f"### تغريدة {i}\n{post}\n")
        lines.append("## Instagram Reels\n")
        for i, script in enumerate(ig_reels, 1):
            lines.append(f"### ريل {i}\n{script}\n")
        lines.append("## Instagram Posts\n")
        for i, caption in enumerate(ig_posts, 1):
            lines.append(f"### منشور {i}\n{caption}\n")
        path.write_text("\n".join(lines), encoding="utf-8")
        self._log.info("content.saved_locally", path=str(path))

    # ─── LinkedIn Posts (existing, unchanged) ──────────────────────────────────

    async def generate_linkedin_post(
        self,
        content_type: str = "insight",
        data: Optional[dict] = None,
    ) -> str:
        prompts = {
            "insight":    self._linkedin_insight_prompt(data),
            "win":        self._linkedin_win_prompt(data),
            "objection":  self._linkedin_objection_prompt(data),
            "stats":      self._linkedin_stats_prompt(data),
            "cold_chain": self._linkedin_education_prompt(data),
        }
        prompt = prompts.get(content_type, prompts["insight"])
        post = await get_text(self.api_key, CONTENT_SYSTEM_PROMPT, prompt, max_tokens=600)
        self._log.info("content.linkedin_generated", type=content_type, length=len(post))
        return post

    def _linkedin_insight_prompt(self, data: Optional[dict]) -> str:
        topic = data.get("topic", "سلسلة التبريد") if data else "سلسلة التبريد"
        return f"""اكتب منشور LinkedIn احترافي (200-250 كلمة) عن: {topic}
الهيكل: Hook قوي → 3-4 نقاط عملية → ختام يدعو للتفاعل.
لا هاشتاقات أكثر من 3. لا تبدأ بـ "مرحباً"."""

    def _linkedin_win_prompt(self, data: Optional[dict]) -> str:
        client_type = (data or {}).get("client_type", "سلسلة مطاعم")
        result      = (data or {}).get("result", "توفير 15% من تكاليف النقل")
        value       = (data or {}).get("monthly_value", "")
        return f"""اكتب قصة نجاح موجزة لـ LinkedIn — عميل من قطاع: {client_type}
النتيجة: {result}. {'القيمة: ' + value if value else ''}
أسلوب: التحدي → الحل → النتيجة. 200 كلمة. لا تذكر اسم العميل."""

    def _linkedin_objection_prompt(self, data: Optional[dict]) -> str:
        objection = (data or {}).get("objection", "أسعاركم غالية")
        return f"""اعتراض شائع: "{objection}"
اكتب منشور LinkedIn يعالجه بأسلوب تعليمي وليس دفاعي. 180-220 كلمة."""

    def _linkedin_stats_prompt(self, data: Optional[dict]) -> str:
        return """اكتب منشور LinkedIn مبني على إحصائيات واقعية عن سوق النقل المبرد في السعودية.
حجم السوق + تأثير الهدر + الفرق بين الناقل الاحترافي والعادي. 200-250 كلمة."""

    def _linkedin_education_prompt(self, data: Optional[dict]) -> str:
        topic = (data or {}).get("topic", "أهمية الـ Cold Chain")
        return f"""اكتب منشور تعليمي عن: {topic}
الهدف: بناء الثقة — ليس البيع المباشر. 180-220 كلمة. أسلوب خبير."""

    # ─── Email / WhatsApp (existing, unchanged) ────────────────────────────────

    async def generate_email_sequence(
        self,
        lead_name: str,
        company: Optional[str],
        segment: str,
        sequence_step: int = 1,
    ) -> dict[str, str]:
        step_prompts = {
            1: f"اكتب Cold Email أول تواصل لـ {lead_name} ({company or segment}). الهدف: كسر الجليد. 120-150 كلمة.\nFormat:\nSUBJECT: ...\nBODY:\n...",
            2: f"اكتب Follow-up Email #2 بعد 3 أيام لـ {company or segment}. أضف قيمة. 80-100 كلمة.\nFormat:\nSUBJECT: ...\nBODY:\n...",
            3: f"اكتب Break-up Email لـ {company or segment}. أغلق باحترام مع إبقاء الخيار مفتوحاً. 60-80 كلمة.\nFormat:\nSUBJECT: ...\nBODY:\n...",
        }
        prompt = step_prompts.get(sequence_step, step_prompts[1])
        text = await get_text(self.api_key, CONTENT_SYSTEM_PROMPT, prompt, max_tokens=400)

        subject, body = "", text
        if "SUBJECT:" in text:
            parts = text.split("BODY:", 1)
            subject = parts[0].replace("SUBJECT:", "").strip()
            body = parts[1].strip() if len(parts) > 1 else text

        return {"subject": subject, "body": body, "step": sequence_step}

    async def generate_whatsapp_reactivation(
        self,
        lead_name: str,
        days_inactive: int,
        last_context: Optional[str] = None,
    ) -> str:
        context_text = f"آخر محادثة: {last_context}" if last_context else ""
        prompt = f"""رسالة واتساب قصيرة لإعادة تفعيل محادثة مع: {lead_name} ({days_inactive} يوم صمت).
{context_text}
أسلوب: دافئ، غير مُلح، يضيف قيمة. 2-3 جمل. لا تبدأ بـ "مرحباً" مجردة."""
        return await get_text(self.api_key, CONTENT_SYSTEM_PROMPT, prompt, max_tokens=150)

    async def generate_objection_script(self, objection: str) -> dict[str, str]:
        prompt = f"""سكريبت رد على اعتراض: "{objection}"
1. اعترف بالمخاوف أولاً
2. منظور مختلف بالأرقام
3. سؤال مفتوح ختامي

Format:
ACKNOWLEDGE: ...
REFRAME: ...
QUESTION: ..."""
        text = await get_text(self.api_key, CONTENT_SYSTEM_PROMPT, prompt, max_tokens=300)
        result = {"objection": objection, "full_script": text}
        for key in ["ACKNOWLEDGE", "REFRAME", "QUESTION"]:
            if f"{key}:" in text:
                part = text.split(f"{key}:", 1)[1].split("\n")[0].strip()
                result[key.lower()] = part
        return result

    async def generate_weekly_content_plan(
        self,
        pipeline_data: Optional[dict] = None,
    ) -> list[dict]:
        """Generate 5-post LinkedIn plan + trigger social content upload."""
        week_days    = ["الأحد", "الإثنين", "الثلاثاء", "الأربعاء", "الخميس"]
        content_types = ["insight", "win", "objection", "cold_chain", "stats"]

        context = ""
        if pipeline_data:
            total = pipeline_data.get("total_leads", 0)
            top   = pipeline_data.get("top_category", "")
            if total:
                context = f"لدينا {total} عميل محتمل"
            if top:
                context += f", القطاع: {top}"

        plan = []
        for i, (day, ctype) in enumerate(zip(week_days, content_types)):
            data = {"topic": context} if context and ctype == "insight" else None
            post = await self.generate_linkedin_post(content_type=ctype, data=data)
            plan.append({
                "day": day,
                "day_number": i + 1,
                "content_type": ctype,
                "post": post,
                "revenue_goal": self._revenue_goal_for_type(ctype),
            })

        self._log.info("content.weekly_plan_generated", posts=len(plan))
        return plan

    def _revenue_goal_for_type(self, content_type: str) -> str:
        return {
            "insight":    "بناء الثقة — تحويل المتابع لعميل",
            "win":        "إثبات القيمة — تسريع الإغلاق",
            "objection":  "إزالة عوائق الشراء",
            "cold_chain": "جذب عملاء جدد",
            "stats":      "خلق الوعي بالحاجة للخدمة",
        }.get(content_type, "بناء الوعي بالعلامة")
