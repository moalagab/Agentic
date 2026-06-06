"""
Content Engine — محرك المحتوى (Layer 7 - RevOS v6)

يحوّل بيانات المبيعات والسوق إلى محتوى يُدر عملاء:
- منشورات LinkedIn
- رسائل بريد إلكتروني (تسلسل)
- رسائل WhatsApp للمتابعة
- سكريبتات معالجة الاعتراضات
- دراسات حالة
- صفحات هبوط

كل محتوى يُربط بتأثير إيرادي مباشر.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import structlog
from agent.ai_client import get_text

logger = structlog.get_logger(__name__)


CONTENT_SYSTEM_PROMPT = """\
أنت خبير تسويق B2B متخصص في قطاع اللوجستيات والنقل المبرد في المملكة العربية السعودية.
تعمل لحساب شركة سمارت فيلد — رائدة النقل المبرد في المملكة.

أسلوبك: احترافي، موثوق، يعكس خبرة حقيقية. لا مبالغة ولا وعود فارغة.
اللغة: عربية فصحى مبسّطة مناسبة لبيئة الأعمال السعودية.
"""


class ContentEngine:
    """
    AI-powered content generation engine mapped to revenue impact.
    يولّد محتوى هادفاً مرتبطاً بأهداف إيرادية محددة.
    """

    def __init__(self, gemini_api_key: str) -> None:
        self.api_key = gemini_api_key
        self._log = logger.bind(component="ContentEngine")

    # ─── LinkedIn Posts ───────────────────────────────────────────────────────

    async def generate_linkedin_post(
        self,
        content_type: str = "insight",
        data: Optional[dict] = None,
    ) -> str:
        """
        Generate a LinkedIn post.

        content_type options:
          - insight: industry insight / tip
          - win: customer success story (anonymized)
          - objection: common objection + our answer
          - stats: industry statistics
          - cold_chain: cold chain education
        """
        prompts = {
            "insight": self._linkedin_insight_prompt(data),
            "win": self._linkedin_win_prompt(data),
            "objection": self._linkedin_objection_prompt(data),
            "stats": self._linkedin_stats_prompt(data),
            "cold_chain": self._linkedin_education_prompt(data),
        }

        prompt = prompts.get(content_type, prompts["insight"])

        post = await get_text(self.api_key, CONTENT_SYSTEM_PROMPT, prompt, max_tokens=600)
        self._log.info("content.linkedin_generated", type=content_type, length=len(post))
        return post

    def _linkedin_insight_prompt(self, data: Optional[dict]) -> str:
        topic = data.get("topic", "سلسلة التبريد") if data else "سلسلة التبريد"
        return f"""اكتب منشور LinkedIn احترافي (200-250 كلمة) عن: {topic}

الهيكل:
- Hook قوي (جملة أولى تشد الانتباه)
- 3-4 نقاط عملية / قابلة للتطبيق
- ختام يدعو للتفاعل أو الاستفسار

لا تضع هاشتاقات أكثر من 3. لا تبدأ بـ "مرحباً" أو "أهلاً"."""

    def _linkedin_win_prompt(self, data: Optional[dict]) -> str:
        if data:
            client_type = data.get("client_type", "عميل في قطاع الأغذية")
            result = data.get("result", "تقليل التكاليف وزيادة الكفاءة")
            value = data.get("monthly_value", "")
        else:
            client_type = "سلسلة مطاعم"
            result = "توفير 15% من تكاليف النقل وتحسين طزاجة المنتج"
            value = "35,000 ريال/شهر"

        return f"""اكتب قصة نجاح (case study) موجزة لـ LinkedIn عن عميل من قطاع: {client_type}
النتيجة المحققة: {result}
{'القيمة الشهرية: ' + value if value else ''}

الأسلوب: قصة حقيقية مختصرة — التحدي، الحل، النتيجة.
لا تذكر اسم العميل — قل "أحد عملائنا في ..."
200 كلمة تقريباً."""

    def _linkedin_objection_prompt(self, data: Optional[dict]) -> str:
        objection = data.get("objection", "أسعاركم غالية") if data else "أسعاركم غالية"
        return f"""اعتراض شائع نسمعه: "{objection}"

اكتب منشور LinkedIn يعالج هذا الاعتراض بأسلوب تعليمي وليس دفاعي.
أظهر القيمة الحقيقية وراء السعر. استخدم أرقاماً واقعية إن أمكن.
180-220 كلمة."""

    def _linkedin_stats_prompt(self, data: Optional[dict]) -> str:
        return """اكتب منشور LinkedIn مبني على إحصائيات واقعية عن سوق النقل المبرد في السعودية:
- حجم السوق ونموه
- تأثير الهدر في سلسلة التبريد على الشركات
- الفرق بين الناقل الاحترافي والعادي

استند لأرقام منطقية ومعقولة. لا تختلق أرقاماً غير واقعية.
200-250 كلمة."""

    def _linkedin_education_prompt(self, data: Optional[dict]) -> str:
        topic = data.get("topic", "أهمية الـ Cold Chain في الأدوية") if data else "أهمية الـ Cold Chain في الأدوية"
        return f"""اكتب منشور تعليمي عن: {topic}
الهدف: بناء الثقة وتعليم القارئ — وليس البيع المباشر.
180-220 كلمة. أسلوب خبير، ليس مروّج."""

    # ─── Email Sequences ──────────────────────────────────────────────────────

    async def generate_email_sequence(
        self,
        lead_name: str,
        company: Optional[str],
        segment: str,
        sequence_step: int = 1,
    ) -> dict[str, str]:
        """Generate a single email in a drip sequence (3-step)."""
        step_prompts = {
            1: f"""اكتب بريد إلكتروني أول تواصل (Cold Email) لـ:
الاسم: {lead_name}
الشركة: {company or 'غير محدد'}
القطاع: {segment}

الهدف: كسر الجليد وإثارة الفضول — ليس البيع مباشرة.
أسلوب: احترافي، مباشر، مخصص لهم.
الطول: 120-150 كلمة.
أضف سطر موضوع جذاب.

Format:
SUBJECT: ...
BODY:
...""",
            2: f"""اكتب بريد متابعة (Follow-up Email #2) بعد 3 أيام من عدم الرد لـ:
الشركة: {company or segment}
القطاع: {segment}

الهدف: إضافة قيمة (tip مفيد أو إحصائية ذات صلة) ثم دعوة للحوار.
الطول: 80-100 كلمة.
أضف سطر موضوع.

Format:
SUBJECT: ...
BODY:
...""",
            3: f"""اكتب بريد أخير (Break-up Email) بعد أسبوع من عدم الرد لـ:
الشركة: {company or segment}
القطاع: {segment}

الهدف: إغلاق الباب بشكل محترم مع إبقاء الخيار مفتوحاً مستقبلاً.
الطول: 60-80 كلمة.

Format:
SUBJECT: ...
BODY:
...""",
        }

        prompt = step_prompts.get(sequence_step, step_prompts[1])

        text = await get_text(self.api_key, CONTENT_SYSTEM_PROMPT, prompt, max_tokens=400)

        # Parse subject and body
        subject = ""
        body = text
        if "SUBJECT:" in text:
            parts = text.split("BODY:", 1)
            subject_line = parts[0].replace("SUBJECT:", "").strip()
            subject = subject_line
            body = parts[1].strip() if len(parts) > 1 else text

        self._log.info("content.email_generated", step=sequence_step, company=company)
        return {"subject": subject, "body": body, "step": sequence_step}

    # ─── WhatsApp Follow-up Messages ─────────────────────────────────────────

    async def generate_whatsapp_reactivation(
        self,
        lead_name: str,
        days_inactive: int,
        last_context: Optional[str] = None,
    ) -> str:
        """Generate a personalized WhatsApp reactivation message."""
        context_text = f"آخر محادثة: {last_context}" if last_context else ""

        prompt = f"""اكتب رسالة واتساب قصيرة لإعادة تفعيل محادثة مع:
الاسم: {lead_name}
أيام الصمت: {days_inactive} يوم
{context_text}

الأسلوب: دافئ، غير مُلحّ، يُضيف قيمة حقيقية.
الطول: 2-3 جمل فقط.
لا تبدأ بـ "مرحباً" مجردة — كن مباشراً."""

        return await get_text(self.api_key, CONTENT_SYSTEM_PROMPT, prompt, max_tokens=150)

    # ─── Objection Handling Scripts ───────────────────────────────────────────

    async def generate_objection_script(self, objection: str) -> dict[str, str]:
        """Generate a sales script for handling a specific objection."""
        prompt = f"""اكتب سكريبت رد احترافي على الاعتراض التالي في محادثة مبيعات:
"{objection}"

الرد يجب:
1. يعترف بالمخاوف أولاً (لا يرفضها)
2. يقدم منظوراً مختلفاً بالأرقام/الحقائق
3. ينتهي بسؤال مفتوح

Format:
ACKNOWLEDGE: [جملة الاعتراف]
REFRAME: [إعادة التأطير مع الأرقام]
QUESTION: [السؤال الختامي]"""

        text = await get_text(self.api_key, CONTENT_SYSTEM_PROMPT, prompt, max_tokens=300)
        result = {"objection": objection, "full_script": text}

        for key in ["ACKNOWLEDGE", "REFRAME", "QUESTION"]:
            if f"{key}:" in text:
                part = text.split(f"{key}:", 1)[1].split("\n")[0].strip()
                result[key.lower()] = part

        return result

    # ─── Weekly Content Calendar ──────────────────────────────────────────────

    async def generate_weekly_content_plan(
        self,
        pipeline_data: Optional[dict] = None,
    ) -> list[dict]:
        """
        Generate a 5-post LinkedIn content plan for the week.
        Uses pipeline data to make content relevant to current sales context.
        """
        week_days = ["الأحد", "الإثنين", "الثلاثاء", "الأربعاء", "الخميس"]
        content_types = ["insight", "win", "objection", "cold_chain", "stats"]

        # Build context from pipeline data
        context = ""
        if pipeline_data:
            total = pipeline_data.get("total_leads", 0)
            top_category = pipeline_data.get("top_category", "")
            if total:
                context = f"لدينا {total} عميل محتمل هذا الأسبوع"
            if top_category:
                context += f", القطاع الأكثر نشاطاً: {top_category}"

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
        goals = {
            "insight": "بناء الثقة — تحويل المتابع لعميل",
            "win": "إثبات القيمة — تسريع الإغلاق",
            "objection": "إزالة عوائق الشراء",
            "cold_chain": "جذب عملاء صيدلانيين / جدد",
            "stats": "خلق الوعي بالحاجة للخدمة",
        }
        return goals.get(content_type, "بناء الوعي بالعلامة")
