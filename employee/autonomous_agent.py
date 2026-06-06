"""
Autonomous AI Employee for Smartfield.
الوكيل المستقل - يعمل كموظف محترف بدون تدخل بشري

Capabilities:
- Receives and responds to WhatsApp messages from leads AND the business owner
- Proactively follows up on cold leads
- Sends daily/weekly reports automatically
- Executes owner commands via WhatsApp ("أرسل عرض لعميل X")
- Logs every action it takes
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

import structlog
from agent.ai_client import get_text, run_agentic_loop

from agent.knowledge_base import get_kb_text, get_scoring_context
from agent.prompts import SYSTEM_PROMPT_AR
from employee import memory as mem
from employee.report_generator import (
    build_daily_report,
    build_follow_up_message,
    build_greeting_followup_message,
    build_new_lead_alert,
)
from models.lead import LeadCreate, LeadSource
from processors.cpq_engine import CPQEngine

# ─── Conversation stage definitions ──────────────────────────────────────────
STAGE_NEW = "new"
STAGE_DISCOVERY = "discovery"
STAGE_QUALIFY = "qualify"
STAGE_CLOSING = "closing"
STAGE_ACTIVE = "active"

if TYPE_CHECKING:
    from channels.telegram import TelegramHandler
    from config import Settings
    from crm.base import BaseCRM
    from notifications.whatsapp import WhatsAppNotifier
    from processors.pipeline import LeadPipeline

logger = structlog.get_logger(__name__)

GEMINI_MODEL = "gemini-flash-latest"

# ─── Employee system prompt ───────────────────────────────────────────────────

_KB_TEXT = get_kb_text()
_SCORE_CTX = get_scoring_context()

EMPLOYEE_SYSTEM_PROMPT = f"""\
## هويتك
اسمك **محمد**. أنت وكيل خدمة عملاء ذكي تمثّل شركة **Smart Field** للنقل المبرد في الرياض.
شعار الشركة: "الدقة في كل درجة". ميزتنا الرئيسية: **تقرير حراري موثّق مع كل تسليم**.
عند تعريف نفسك: "معك محمد من Smart Field" — لا تقل سمارت ولا سمارت فيلد.

{_KB_TEXT}


## شخصيتك
- احترافي، ودود، مباشر
- تتكيّف مع أسلوب العميل (رسمي أو عامّي)
- ردودك قصيرة ومركّزة — جملتان إلى أربع كحد أقصى، بلا حشو
- لا تكرر نفس الترحيب، ولا تكرر معلومة قلتها قبل

## قاعدة الترحيب
- الرسالة الأولى فقط: رحّب بإيجاز ثم أجب مباشرة
- الرسائل التالية: بلا ترحيب — استمر في الموضوع مباشرة

## خدماتنا
- نقل مبرد B2B داخل الرياض — يبدأ من 160 ريال للرحلة
- نخدم: المطاعم، المطابخ المركزية، محامص القهوة، المخابز، ورش الحلويات، الموردين الغذائيين، شركات التموين
- ميزتنا الأساسية: تقرير حراري موثّق + استجابة طوارئ خلال 90 دقيقة + ضبط دقيق لدرجة حرارة كل منتج

## مسارات الخدمة

### إذا سأل عن السعر:
1. اسأل: داخل الرياض أو خارجها؟ ونوع البضاعة ودرجة التبريد المطلوبة
2. داخل الرياض: السعر يبدأ من 160 ريال، ويُحدَّد نهائيًا حسب المسافة والتكرار
3. خارج الرياض: "نقيّم الرحلات خارج الرياض حسب توفّر الجدولة — خذ تفاصيلك وفريقنا يأكد لك الإمكانية والسعر خلال ساعات." (لا تلتزم بسعر أو بتنفيذ مؤكد)
4. اربط السعر بالقيمة: "السعر يشمل تقرير حراري موثّق مع التسليم"

### إذا أراد حجز رحلة:
اجمع هذه البيانات (سؤال واحد أو سؤالان في كل رسالة، لا تستجوبه):
- نوع المنشأة والمنتج
- نقطة الانطلاق والوجهة
- التاريخ والوقت المطلوب
- درجة الحرارة المطلوبة
ثم أكّد التفاصيل في رسالة واحدة، وأخبره أن الفريق سيتواصل لتثبيت الحجز.

### إذا كان طلب طارئ (ثلاجة تعطّلت / شحنة بخطر):
1. أبدِ الجدية فورًا: "وصلني، نتحرك بأسرع وقت"
2. اطلب: الموقع، نوع البضاعة، درجة الحرارة المطلوبة
3. أخبره: "نستجيب للطوارئ خلال 90 دقيقة داخل نطاقنا — الفريق يتواصل معك الحين"

### إذا سأل سؤالًا عامًا عن الشركة:
أجب بإيجاز ثم وجّهه لخطوة (حجز أو استفسار سعر)

### إذا اشتكى أو واجه مشكلة:
1. تفهّم بجملة واحدة (بلا مبالغة)
2. اطلب تفاصيل المشكلة
3. أخبره أن الفريق سيتواصل خلال ساعتين

### إذا اعترض على السعر:
لا تجادل. قل: "النقل المبرد تأمين على بضاعتك — شحنة تتلف تكلّف أضعاف فرق السعر. والتقرير الحراري يحميك أمام أي مساءلة." ثم اعرض رحلة تجريبية.

## التقفيل
في نهاية أي محادثة مكتملة، رسالة واحدة:
- تأكيد ما اتُّفق عليه
- الخطوة التالية بوضوح
- شكر مختصر

## قواعد صارمة (لا تتجاوزها أبدًا)
- لا تخترع أسعارًا أو أرقامًا أو قدرات غير مذكورة هنا. إذا لا تعرف، قل: "أتأكد لك من الفريق وأرد عليك"
- لا تلتزم بسعر للرحلات خارج الرياض قبل أخذ التفاصيل وتأكيد الفريق
- لا تدّعِ عملاء أو عددهم أو حجم الشركة. إذا سُئلت: "نختار عملاءنا الأوائل بعناية، ويسعدنا تكون منهم"
- لا تقبل طلبات نقل أدوية أو لقاحات (خارج تخصصنا) — اعتذر ووجّهه لمختص
- لا توصيل أفراد (B2C) — خدمتنا للمنشآت فقط
- لا تتجاوز 3 رسائل قبل أن تصل لخطوة واضحة
- إذا كان الطلب غير واضح، اسأل سؤالًا واحدًا فقط لتوضيحه
- إذا خرج العميل عن نطاق خدمتنا أو طلب ما لا تقدر عليه، حوّله للفريق البشري بدل أن تخمّن
- تحدّث بالعربية دائمًا ما لم يبدأ العميل بالإنجليزية
- لا تستخدم رموزًا تعبيرية بإفراط — رمز واحد كل عدة رسائل يكفي

## متى تسجّل العميل
عندما تجمع: الاسم + (نوع البضاعة أو المسار أو نوع المنشأة) — سجّله في الخلفية بدون إخبار العميل.
عند اكتمال أي حجز أو طلب جاد، لخّص داخلياً بهذا الشكل:
[نوع المنشأة | المنتج | الانطلاق | الوجهة | التاريخ/الوقت | درجة الحرارة | داخل/خارج الرياض | طارئ: نعم/لا]

## مع صاحب العمل
نفّذ الأمر مباشرة، أبلغ بالنتيجة بإيجاز.

## تذكير الهوية (مهم جداً)
- اسمك **محمد** في كل الردود — لا "سمارت"، لا "Smart Field Bot"
- الشركة: **Smart Field** (وليس سمارت فيلد باللاتيني بالعربية)
- عند أول تعريف: "معك محمد من Smart Field"
- السعر داخل الرياض يبدأ من 160 ريال — لا تذكر الأسعار الأعلى إلا إذا سأل خارج الرياض
"""

OWNER_COMMAND_TOOLS = [
    {
        "name": "send_proposal_to_lead",
        "description": "أرسل رسالة عرض خدمات لعميل محدد عبر واتساب",
        "input_schema": {
            "type": "object",
            "properties": {
                "lead_phone": {"type": "string", "description": "رقم هاتف العميل"},
                "lead_name": {"type": "string", "description": "اسم العميل"},
                "cargo_type": {"type": "string", "description": "نوع البضاعة إن عُرف"},
            },
            "required": ["lead_phone", "lead_name"],
        },
    },
    {
        "name": "get_pipeline_report",
        "description": "احصل على تقرير خط المبيعات الحالي",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "list_recent_leads",
        "description": "احصل على قائمة بأحدث العملاء المحتملين",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "عدد العملاء", "default": 5}
            },
            "required": [],
        },
    },
    {
        "name": "force_follow_up",
        "description": "أجبر متابعة فورية لعميل محدد",
        "input_schema": {
            "type": "object",
            "properties": {
                "lead_phone": {"type": "string"},
                "lead_name": {"type": "string"},
            },
            "required": ["lead_phone", "lead_name"],
        },
    },
    {
        "name": "add_lead_from_conversation",
        "description": "أضف عميلاً محتملاً جديداً من معلومات المحادثة",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "phone": {"type": "string"},
                "company": {"type": "string"},
                "cargo_type": {"type": "string"},
                "route": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["name", "phone"],
        },
    },
]


class AutonomousEmployee:
    """
    Autonomous AI employee that acts independently on behalf of Smartfield.
    Handles conversations, follow-ups, reports, and owner commands.
    """

    def __init__(
        self,
        config: "Settings",
        pipeline: "LeadPipeline",
        crm: "BaseCRM",
        notifier: "WhatsAppNotifier",
        telegram: "TelegramHandler | None" = None,
    ):
        self.config = config
        self.pipeline = pipeline
        self.crm = crm
        self.notifier = notifier
        self.telegram = telegram
        self.cpq = CPQEngine()
        self._owner_phones: set[str] = set(
            p.strip() for p in (config.SALES_TEAM_WHATSAPP or [])
        )
        self._owner_telegram_ids: set[str] = set(
            str(i).strip() for i in (config.TELEGRAM_OWNER_CHAT_IDS or [])
        )

    # ─── Public API ───────────────────────────────────────────────────────────

    async def handle_incoming_whatsapp(self, phone: str, message: str) -> str:
        """
        Main entry point for all incoming WhatsApp messages.
        Automatically determines if sender is owner or lead and routes accordingly.
        """
        is_owner = self._is_owner(phone)
        logger.info("employee.incoming_message", phone=phone, is_owner=is_owner)

        mem.save_message(phone, "user", message)
        history = mem.get_conversation_history(phone)

        if is_owner:
            response = await self._handle_owner_command(phone, message, history)
        else:
            response = await self._handle_lead_conversation(phone, message, history)

        mem.save_message(phone, "assistant", response)
        mem.log_action(
            "whatsapp_response",
            f"رد على {'المالك' if is_owner else 'عميل'} {phone}",
            response[:100],
        )
        return response

    async def run_proactive_follow_ups(self) -> int:
        """
        Check for overdue follow-ups and send messages automatically.
        Returns the number of follow-ups sent.
        """
        due = mem.get_due_follow_ups()
        sent = 0
        for fu in due:
            try:
                phone = fu["lead_phone"]

                # Check if they replied since the follow-up was scheduled
                # (msg_count > 2 means they engaged after the greeting)
                msg_count = mem.get_message_count(phone)
                profile = mem.get_lead_profile(phone)

                is_greeting_only = fu.get("stage", "").startswith("greeting_only")

                # Skip if they already engaged (more than the greeting exchange)
                # or already registered in CRM
                if profile.get("crm_registered") or msg_count > 2:
                    mem.mark_follow_up_done(fu["id"], notes="عميل تفاعل — لا حاجة للمتابعة")
                    continue

                if is_greeting_only:
                    # Greeting-only contact: no name collected — warm, open message
                    msg = build_greeting_followup_message(fu["attempts"])
                    await self._send_whatsapp(phone, msg)
                    # One more attempt after 48h, then stop
                    next_days = 2 if fu["attempts"] == 0 else None
                    mem.mark_follow_up_done(fu["id"], next_days=next_days, notes="تم الإرسال تلقائياً")
                    mem.log_action(
                        "greeting_follow_up",
                        f"متابعة تحية مع {phone} (محاولة {fu['attempts']+1})",
                        "تم الإرسال",
                        fu["lead_id"],
                    )
                else:
                    # Named/qualified lead follow-up
                    lead_name = fu["lead_name"] or "عزيزي العميل"
                    if fu["attempts"] == 0 and self.config.TWILIO_FOLLOWUP_TEMPLATE_SID:
                        sent_ok = await self._send_whatsapp_template(
                            phone=phone,
                            content_sid=self.config.TWILIO_FOLLOWUP_TEMPLATE_SID,
                            variables={"1": lead_name, "2": "سمارت فيلد"},
                        )
                        if not sent_ok:
                            msg = build_follow_up_message(lead_name, fu["attempts"])
                            await self._send_whatsapp(phone, msg)
                    else:
                        msg = build_follow_up_message(lead_name, fu["attempts"])
                        await self._send_whatsapp(phone, msg)
                    next_days = None if fu["attempts"] >= 2 else (3 if fu["attempts"] == 0 else 7)
                    mem.mark_follow_up_done(fu["id"], next_days=next_days, notes="تم الإرسال تلقائياً")
                    mem.log_action(
                        "proactive_follow_up",
                        f"متابعة تلقائية مع {fu['lead_name']} (محاولة {fu['attempts']+1})",
                        "تم الإرسال",
                        fu["lead_id"],
                    )

                sent += 1
                await asyncio.sleep(1)
            except Exception as exc:
                logger.error("employee.follow_up_failed", lead_id=fu["lead_id"], error=str(exc))
        logger.info("employee.follow_ups_sent", count=sent)
        return sent

    async def send_daily_report(self) -> bool:
        """Generate and send daily report to all owner phones."""
        try:
            stats = await self._get_pipeline_stats()
            report = build_daily_report(stats)
            for phone in self._owner_phones:
                await self._send_whatsapp(phone, report)
            mem.log_action("daily_report", "تم إرسال التقرير اليومي", "نجح")
            logger.info("employee.daily_report_sent")
            return True
        except Exception as exc:
            logger.error("employee.daily_report_failed", error=str(exc))
            return False

    async def notify_new_lead(self, lead_dict: dict, processed_dict: dict):
        """Notify owners of a new lead and schedule follow-up."""
        alert = build_new_lead_alert(lead_dict, processed_dict)

        # Send via Telegram if configured (priority)
        if self.telegram and self._owner_telegram_ids:
            for chat_id in self._owner_telegram_ids:
                await self.telegram.send_message(chat_id, alert)
        else:
            # Fallback to WhatsApp
            for phone in self._owner_phones:
                await self._send_whatsapp(phone, alert)

        lead_phone = lead_dict.get("phone", "")
        if lead_phone:
            mem.schedule_follow_up(
                lead_id=lead_dict.get("id", lead_phone),
                lead_name=lead_dict.get("name", ""),
                lead_phone=lead_phone,
                crm_id=lead_dict.get("crm_id"),
                days_until=1,
            )
        mem.log_action("new_lead_notified", f"إشعار بعميل جديد: {lead_dict.get('name')}", "نجح")

    # ─── Owner commands ───────────────────────────────────────────────────────

    async def _handle_owner_command(
        self, phone: str, message: str, history: list[dict]
    ) -> str:
        """Process a command from the business owner using Gemini with tools."""
        system = EMPLOYEE_SYSTEM_PROMPT + "\n\nأنت تتحدث الآن مع **صاحب العمل**."

        async def _owner_tool_exec(name: str, inputs: dict) -> dict:
            return await self._execute_owner_tool(name, inputs)

        try:
            result = await run_agentic_loop(
                api_key=self.config.GEMINI_API_KEY,
                system=system,
                user_message=message,
                tools=OWNER_COMMAND_TOOLS,
                tool_executor=_owner_tool_exec,
                max_iterations=5,
                model=GEMINI_MODEL,
            )
            return result["final_text"] or "تم تنفيذ الأمر."
        except Exception as exc:
            logger.error("employee.owner_command_failed", error=str(exc))
            return "حدث خطأ أثناء التنفيذ. حاول مجدداً."

    async def _execute_owner_tool(self, name: str, inputs: dict) -> dict:
        """Execute a tool requested by Claude for owner commands."""
        try:
            if name == "send_proposal_to_lead":
                msg = self._build_proposal(inputs.get("lead_name", ""), inputs.get("cargo_type"))
                await self._send_whatsapp(inputs["lead_phone"], msg)
                mem.log_action("proposal_sent", f"عرض أُرسل لـ {inputs['lead_name']}", "نجح")
                return {"success": True, "message": f"تم إرسال العرض لـ {inputs['lead_name']}"}

            elif name == "get_pipeline_report":
                stats = await self._get_pipeline_stats()
                return {"success": True, "stats": stats}

            elif name == "list_recent_leads":
                leads = await self._get_recent_leads(inputs.get("limit", 5))
                return {"success": True, "leads": leads}

            elif name == "force_follow_up":
                msg = build_follow_up_message(inputs.get("lead_name", ""), 0)
                await self._send_whatsapp(inputs["lead_phone"], msg)
                mem.log_action("forced_follow_up", f"متابعة مُجبرة لـ {inputs['lead_name']}", "نجح")
                return {"success": True, "message": "تمت المتابعة"}

            elif name == "add_lead_from_conversation":
                lead = LeadCreate(
                    name=inputs.get("name", ""),
                    phone=inputs.get("phone", ""),
                    company=inputs.get("company"),
                    cargo_type=inputs.get("cargo_type"),
                    route=inputs.get("route"),
                    notes=inputs.get("notes"),
                    source=LeadSource.WHATSAPP,
                )
                processed = await self.pipeline.process(lead)
                return {"success": True, "lead_id": str(processed.id), "score": processed.score}

            return {"success": False, "error": f"أداة غير معروفة: {name}"}
        except Exception as exc:
            logger.error("employee.owner_tool_failed", tool=name, error=str(exc))
            return {"success": False, "error": str(exc)}

    # ─── Lead conversation ────────────────────────────────────────────────────

    async def _handle_lead_conversation(
        self, phone: str, message: str, history: list[dict]
    ) -> str:
        """
        Fast, natural lead conversation handler.
        Single API call with haiku — CRM registration fires in background.
        """
        profile = mem.get_lead_profile(phone)
        msg_count = mem.get_message_count(phone)

        # Build a minimal 1-3 line context — trust Claude to handle the flow
        ctx_parts = []
        if msg_count == 0:
            ctx_parts.append("أول رسالة من هذا العميل.")
        summary = self._profile_summary(profile)
        if summary != "لا شيء بعد":
            ctx_parts.append(f"ما جُمع: {summary}")
        if profile.get("crm_registered"):
            ctx_parts.append("العميل مسجّل بالفعل — أجب مباشرة.")
        ctx = "\n".join(ctx_parts) if ctx_parts else "محادثة جديدة."

        extract_tool = {
            "name": "register_lead",
            "description": (
                "سجّل العميل في النظام عندما تجمع: الاسم + (نوع البضاعة أو المسار). "
                "استدعِ بهدوء في الخلفية بدون إخبار العميل."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "company": {"type": "string"},
                    "cargo_type": {"type": "string"},
                    "route_from": {"type": "string"},
                    "route_to": {"type": "string"},
                    "fleet_size": {"type": "string"},
                    "budget": {"type": "string"},
                    "timeline": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": ["name"],
            },
        }

        quote_tool = {
            "name": "calculate_quote",
            "description": (
                "احسب عرض سعر فوري للعميل. استخدمها فور أن يطلب العميل سعراً أو عرضاً، "
                "أو عندما تجمع: المسار + نوع البضاعة."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "route_from": {"type": "string", "description": "مدينة الإرسال"},
                    "route_to": {"type": "string", "description": "مدينة الاستلام"},
                    "vehicle_type": {
                        "type": "string",
                        "enum": ["small_van", "medium_truck", "large_truck", "reefer_trailer"],
                        "description": "small_van=فان / medium_truck=شاحنة متوسطة / large_truck=شاحنة كبيرة / reefer_trailer=مقطورة",
                    },
                    "temperature_zone": {
                        "type": "string",
                        "enum": ["chilled", "frozen", "pharma"],
                        "description": "chilled=مبرد +2/+8 / frozen=مجمد -18/-25 / pharma=صيدلاني",
                    },
                    "frequency_per_month": {
                        "type": "integer",
                        "description": "عدد الرحلات في الشهر (افتراضي 1)",
                    },
                },
                "required": ["route_from", "route_to"],
            },
        }

        system = EMPLOYEE_SYSTEM_PROMPT + f"\n\n{ctx}"
        quote_result: str | None = None

        async def _conv_tool_exec(name: str, inputs: dict) -> dict:
            nonlocal quote_result
            if name == "register_lead":
                asyncio.create_task(
                    self._register_lead_from_chat(phone, inputs, profile)
                )
                return {"status": "تم تسجيل العميل."}
            elif name == "calculate_quote":
                try:
                    q = self.cpq.calculate(
                        route_from=inputs.get("route_from", ""),
                        route_to=inputs.get("route_to", ""),
                        vehicle_type=inputs.get("vehicle_type", "medium_truck"),
                        temperature_zone=inputs.get("temperature_zone", "chilled"),
                        frequency_per_month=int(inputs.get("frequency_per_month", 1)),
                    )
                    quote_result = q.quote_summary_ar
                    return {"quote": quote_result}
                except Exception as exc:
                    logger.error("cpq.calculation_failed", error=str(exc))
                    return {"quote": "لم أتمكن من حساب السعر الآن."}
            return {"error": f"unknown tool: {name}"}

        try:
            loop_result = await run_agentic_loop(
                api_key=self.config.GEMINI_API_KEY,
                system=system,
                user_message=message,
                tools=[extract_tool, quote_tool],
                tool_executor=_conv_tool_exec,
                max_iterations=3,
                model=GEMINI_MODEL,
            )
            text_response = loop_result["final_text"] or "كيف أقدر أساعدك؟"
        except Exception as exc:
            logger.error("employee.lead_conversation_failed", error=str(exc))
            text_response = "تفضل، كيف أقدر أخدمك؟"

        # Update profile keywords from raw message (no extra API call)
        await self._update_profile_from_message(phone, message, profile, False)

        # After the very first message — schedule a 6h follow-up in case they go silent
        # Only if no useful data collected yet (greeting-only contact)
        if msg_count == 0 and not profile.get("crm_registered"):
            mem.schedule_follow_up(
                lead_id=f"wa:{phone}",
                lead_name="",
                lead_phone=phone,
                crm_id=None,
                hours_until=6,
                stage="greeting_only",
            )

        if not text_response:
            text_response = "وصلت رسالتكم، سنتواصل معكم قريباً."
        return text_response

    def _resolve_stage(self, stage: str, profile: dict, msg_count: int) -> str:
        """Determine the actual stage based on current profile completeness."""
        if profile.get("crm_registered"):
            return STAGE_ACTIVE

        has_cargo = bool(profile.get("cargo_type"))
        has_route = bool(profile.get("route_from") or profile.get("route_to"))
        has_depth = bool(profile.get("fleet_size") or profile.get("budget"))

        if has_cargo and has_route and has_depth:
            return STAGE_CLOSING
        if has_cargo or has_route:
            return STAGE_QUALIFY if msg_count >= 4 else STAGE_DISCOVERY
        if msg_count == 0:
            return STAGE_NEW
        return stage if stage != STAGE_NEW else STAGE_DISCOVERY

    def _build_stage_prompt(self, stage: str, profile: dict, phone: str) -> str:
        """Build a stage-specific prompt addition."""

        missing = []
        if not profile.get("cargo_type"):
            missing.append("نوع البضاعة")
        if not profile.get("route_from") and not profile.get("route_to"):
            missing.append("المسار (من أين إلى أين)")
        if not profile.get("fleet_size"):
            missing.append("عدد الشاحنات المطلوبة")
        if not profile.get("budget"):
            missing.append("الميزانية التقريبية")

        if stage == STAGE_NEW:
            return (
                f"\n\n## المرحلة: استقبال أول رسالة\n"
                f"رقم العميل: {phone}\n\n"
                "هذه أول رسالة من هذا الشخص.\n"
                "ردّ بجملة ترحيب واحدة قصيرة باسم الشركة، "
                "ثم اسأل سؤالاً واحداً مفتوحاً: كيف تقدر تخدمه.\n"
                "لا تسأل عن اسمه بعد — دعه يتكلم أولاً.\n"
                "مثال على النبرة: 'السلام عليكم، معك محمد من Smart Field للنقل المبرد — كيف نقدر نخدمك؟'"
            )

        if stage == STAGE_DISCOVERY:
            next_q = missing[0] if missing else "متطلباتهم"
            return (
                f"\n\n## المرحلة: استكشاف الاحتياج\n"
                f"رقم العميل: {phone}\n"
                f"ما جُمع حتى الآن: {self._profile_summary(profile)}\n\n"
                f"اسأل سؤالاً واحداً محدداً عن: {next_q}\n"
                "لا تسأل أكثر من سؤال في نفس الرسالة.\n"
                "إذا كان العميل سألك عن سعر أو خدمة، أجب أولاً ثم اسأل."
            )

        if stage == STAGE_QUALIFY:
            next_q = missing[0] if missing else "التوقيت المناسب للبدء"
            return (
                f"\n\n## المرحلة: تعميق الاحتياج\n"
                f"رقم العميل: {phone}\n"
                f"ما جُمع: {self._profile_summary(profile)}\n\n"
                f"لديك المعلومات الأساسية. اسأل الآن عن: {next_q}\n"
                "يمكنك أن تذكر نقطة قيمة متعلقة بما قاله (سعر تقريبي، معلومة تقنية) "
                "لإظهار الخبرة، ثم اسأل."
            )

        if stage == STAGE_CLOSING:
            return (
                f"\n\n## المرحلة: الإغلاق\n"
                f"رقم العميل: {phone}\n"
                f"ما جُمع: {self._profile_summary(profile)}\n\n"
                "لديك معلومات كافية. أجب على أي سؤال عندهم، "
                "ثم اطلب موعد مكالمة قصيرة أو اقترح خطوة تالية واضحة.\n"
                "استدعِ أداة register_lead الآن إذا لم تفعل بعد.\n"
                "لا تطوّل — جملة أو جملتين كحد أقصى."
            )

        if stage == STAGE_ACTIVE:
            return (
                f"\n\n## المرحلة: عميل نشط\n"
                f"رقم العميل: {phone}\n"
                f"هذا العميل مسجّل في النظام. {self._profile_summary(profile)}\n\n"
                "أجب على استفساره مباشرة. إذا سأل عن سعر أو تفاصيل، أعطه معلومة واقعية.\n"
                "إذا أراد المضي قدماً، رتّب الخطوة التالية."
            )

        return f"\n\nرقم العميل: {phone}\nما جُمع: {self._profile_summary(profile)}"

    def _profile_summary(self, profile: dict) -> str:
        parts = []
        if profile.get("name"):
            parts.append(f"الاسم: {profile['name']}")
        if profile.get("cargo_type"):
            parts.append(f"البضاعة: {profile['cargo_type']}")
        if profile.get("route_from") or profile.get("route_to"):
            parts.append(f"المسار: {profile.get('route_from','؟')}←{profile.get('route_to','؟')}")
        if profile.get("fleet_size"):
            parts.append(f"شاحنات: {profile['fleet_size']}")
        if profile.get("budget"):
            parts.append(f"الميزانية: {profile['budget']}")
        return "، ".join(parts) if parts else "لا شيء بعد"

    async def _register_lead_from_chat(
        self, phone: str, inp: dict, profile: dict
    ) -> bool:
        """Register lead in CRM and update profile stage to active."""
        try:
            lead = LeadCreate(
                name=inp.get("name", profile.get("name") or "عميل واتساب"),
                phone=phone,
                company=inp.get("company"),
                cargo_type=inp.get("cargo_type"),
                notes=inp.get("notes"),
                source=LeadSource.WHATSAPP,
            )
            processed = await self.pipeline.process(lead)
            mem.update_lead_profile(
                phone,
                stage=STAGE_ACTIVE,
                name=inp.get("name"),
                company=inp.get("company"),
                cargo_type=inp.get("cargo_type"),
                route_from=inp.get("route_from"),
                route_to=inp.get("route_to"),
                fleet_size=inp.get("fleet_size"),
                budget=inp.get("budget"),
                timeline=inp.get("timeline"),
                crm_registered=1,
            )
            await self.notify_new_lead(
                {
                    **inp, "phone": phone,
                    "id": str(processed.lead.id),
                    "source": "WHATSAPP",
                    "crm_id": processed.lead.crm_id,
                },
                {
                    "priority": processed.lead.priority.value,
                    "score": processed.lead.score,
                    "next_actions": processed.next_actions,
                },
            )
            # Log activity in Supabase
            if hasattr(self.crm, "log_activity"):
                asyncio.create_task(self.crm.log_activity(
                    str(processed.lead.id), "message", "done", "تسجيل عميل من واتساب"
                ))
            logger.info("employee.lead_registered_from_chat", phone=phone)
            return True
        except Exception as exc:
            logger.error("employee.lead_register_failed", error=str(exc))
            return False

    async def _update_profile_from_message(
        self, phone: str, message: str, profile: dict, lead_registered: bool
    ) -> None:
        """Lightly parse message to update profile fields without calling Claude."""
        if lead_registered:
            return
        text = message.lower()
        updates: dict[str, Any] = {}

        # Simple keyword cargo detection
        cargo_map = {
            "دجاج": "دواجن", "لحم": "لحوم", "سمك": "مأكولات بحرية",
            "خضار": "خضروات", "فاكهة": "فواكه", "ألبان": "منتجات ألبان",
            "مجمد": "منتجات مجمدة", "دواء": "أدوية", "صيدل": "صيدلانيات",
            "طبي": "مستلزمات طبية",
        }
        if not profile.get("cargo_type"):
            for kw, ct in cargo_map.items():
                if kw in text:
                    updates["cargo_type"] = ct
                    break

        if updates:
            mem.update_lead_profile(phone, **updates)

    # ─── Helpers ──────────────────────────────────────────────────────────────

    def _is_owner(self, phone: str) -> bool:
        phone_clean = phone.lstrip("+").strip()
        return any(p.lstrip("+").strip() == phone_clean for p in self._owner_phones)

    def _extract_text(self, response: Any) -> str:
        for block in response.content:
            if hasattr(block, "text"):
                return block.text.strip()
        return ""

    async def _send_whatsapp(self, phone: str, message: str):
        """Send a WhatsApp message via the notifier's send_custom_message."""
        await self.notifier.send_custom_message(phone, message)

    async def _send_whatsapp_template(
        self,
        phone: str,
        content_sid: str,
        variables: dict,
    ) -> bool:
        """
        Send a Twilio-approved WhatsApp Template message.
        يرسل رسالة Template واتساب معتمدة عبر Twilio.

        Args:
            phone: recipient number in E.164 format (+966...)
            content_sid: Twilio Content SID (HXxxx...)
            variables: template variable values e.g. {"1": "12/1", "2": "3pm"}
        """
        if not self.config.is_twilio_configured():
            logger.warning("employee.template_skipped", reason="Twilio not configured")
            return False

        import json as _json

        from_number = self.config.TWILIO_WHATSAPP_FROM
        if not from_number.startswith("whatsapp:"):
            from_number = f"whatsapp:{from_number}"

        to_number = phone if phone.startswith("whatsapp:") else f"whatsapp:{phone}"

        def _send():
            from twilio.rest import Client
            client = Client(self.config.TWILIO_ACCOUNT_SID, self.config.TWILIO_AUTH_TOKEN)
            msg = client.messages.create(
                from_=from_number,
                to=to_number,
                content_sid=content_sid,
                content_variables=_json.dumps(variables),
            )
            return msg.sid

        loop = asyncio.get_running_loop()
        try:
            sid = await loop.run_in_executor(None, _send)
            logger.info("employee.template_sent", phone=phone, sid=sid, content_sid=content_sid)
            return True
        except Exception as exc:
            logger.error("employee.template_failed", phone=phone, error=str(exc))
            return False

    def _build_proposal(self, lead_name: str, cargo_type: str | None) -> str:
        cargo_line = f" لنقل {cargo_type}" if cargo_type else ""
        return (
            f"السلام عليكم {lead_name}،\n\n"
            f"Smart Field{cargo_line} — نقل مبرد B2B داخل الرياض يبدأ من 160 ريال للرحلة، مع تقرير حراري موثّق مع كل تسليم.\n\n"
            f"متى يناسبكم مكالمة قصيرة نناقش فيها احتياجاتكم؟"
        )

    async def _get_pipeline_stats(self) -> dict:
        try:
            return await self.crm.get_pipeline_stats()
        except Exception:
            today_actions = mem.get_today_actions()
            return {
                "new_leads_today": sum(1 for a in today_actions if a["action_type"] == "lead_created"),
                "qualified_today": 0,
                "contacted_today": sum(1 for a in today_actions if "متابعة" in a["description"]),
                "pipeline_total": 0,
                "high_priority_open": 0,
            }

    async def _get_recent_leads(self, limit: int = 5) -> list[dict]:
        try:
            stats = await self.crm.get_pipeline_stats()
            return stats.get("recent_leads", [])[:limit]
        except Exception:
            return []

    # ─── Telegram support ────────────────────────────────────────────────────

    async def handle_telegram_message(self, chat_id: str, name: str, text: str) -> str:
        """
        Entry point for all incoming Telegram messages.
        Routes owner commands or lead conversations through the same Claude agent.
        نقطة دخول رسائل تيليغرام - نفس منطق واتساب تماماً.
        """
        is_owner = self._is_telegram_owner(chat_id)
        logger.info("employee.telegram_message", chat_id=chat_id, is_owner=is_owner)

        conv_key = f"tg:{chat_id}"
        mem.save_message(conv_key, "user", text)
        history = mem.get_conversation_history(conv_key)

        if is_owner:
            response = await self._handle_owner_command(chat_id, text, history)
        else:
            response = await self._handle_lead_conversation(chat_id, text, history)

        mem.save_message(conv_key, "assistant", response)
        mem.log_action(
            "telegram_response",
            f"رد على {'المالك' if is_owner else 'عميل'} تيليغرام {chat_id}",
            response[:100],
        )
        return response

    async def notify_telegram_owners(self, message: str):
        """Send a message to all owner Telegram chat IDs."""
        if not self.telegram:
            return
        for chat_id in self._owner_telegram_ids:
            await self.telegram.send_message(chat_id, message)

    async def send_daily_report_telegram(self) -> bool:
        """Send daily report to Telegram owners."""
        if not self.telegram or not self._owner_telegram_ids:
            return False
        try:
            stats = await self._get_pipeline_stats()
            from employee.report_generator import build_daily_report
            report = build_daily_report(stats)
            await self.notify_telegram_owners(report)
            mem.log_action("daily_report_telegram", "تقرير يومي عبر تيليغرام", "نجح")
            return True
        except Exception as exc:
            logger.error("employee.telegram_report_failed", error=str(exc))
            return False

    def _is_telegram_owner(self, chat_id: str) -> bool:
        return str(chat_id).strip() in self._owner_telegram_ids
