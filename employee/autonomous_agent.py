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

import anthropic
import structlog

from agent.prompts import SYSTEM_PROMPT_AR
from employee import memory as mem
from employee.report_generator import (
    build_daily_report,
    build_follow_up_message,
    build_new_lead_alert,
)
from models.lead import LeadCreate, LeadSource

if TYPE_CHECKING:
    from channels.telegram import TelegramHandler
    from config import Settings
    from crm.base import BaseCRM
    from notifications.whatsapp import WhatsAppNotifier
    from processors.pipeline import LeadPipeline

logger = structlog.get_logger(__name__)

CLAUDE_MODEL = "claude-sonnet-4-6"

# ─── Employee system prompt ───────────────────────────────────────────────────

EMPLOYEE_SYSTEM_PROMPT = """\
أنت موظف ذكاء اصطناعي محترف في شركة سمارت فيلد للنقل المبرد في المملكة العربية السعودية.
اسمك "سمارت" وأنت تعمل كمسؤول مبيعات وخدمة عملاء.

## شخصيتك ومبادئك
- محترف، ودّي، وواضح في التواصل
- تتحدث العربية الخليجية بشكل طبيعي
- تُعبّر عن نفسك بثقة دون تكبّر
- تعرف منتجات وخدمات الشركة جيداً
- تتصرف بشكل مستقل وتُبادر دون انتظار الأوامر

## معلومات الشركة
- **سمارت فيلد** - النقل المبرد في المملكة العربية السعودية
- خدمات: نقل غذاء، أدوية، صناعات حرارة حساسة
- تغطية: الرياض، جدة، الدمام، وجميع المناطق
- معايير: ISO 22000، تتبع GPS مباشر، سائقون مدرّبون

## كيفية التعامل مع المحادثات

### إذا كان الرسالة من **صاحب العمل** (مالك الشركة):
- نفّذ الأوامر مباشرة
- أبلغ بالنتائج بوضوح
- اسأل إذا كانت التعليمات غير واضحة

### إذا كان الرسالة من **عميل محتمل**:
1. رحّب بحرارة
2. اجمع المعلومات تدريجياً (الاسم، الشركة، نوع البضاعة، المسار، الكمية)
3. قدّم الخدمات المناسبة لاحتياجاته
4. أضفه كـ Lead في النظام
5. حدّد موعد متابعة

## ردودك يجب أن تكون:
- قصيرة ومباشرة (3-5 جمل كحد أقصى)
- بالعربية دائماً ما لم يتحدث العميل بلغة أخرى
- تنتهي بسؤال يدفع المحادثة للأمام

## أوامر صاحب العمل التي تفهمها:
- "أرسل عرض لـ [اسم]" → أرسل رسالة ترحيبية للعميل
- "تقرير اليوم" → أرسل ملخص أنشطة اليوم
- "متابعة [اسم]" → راجع حالة العميل وتابعه
- "إحصائيات" → أرسل إحصائيات خط المبيعات
- "قائمة العملاء" → أرسل أحدث العملاء
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
        self.client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
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
                lead_name = fu["lead_name"] or "عزيزي العميل"
                # Attempt 0: send approved Template (appointment reminder style)
                if fu["attempts"] == 0 and self.config.TWILIO_FOLLOWUP_TEMPLATE_SID:
                    sent_ok = await self._send_whatsapp_template(
                        phone=fu["lead_phone"],
                        content_sid=self.config.TWILIO_FOLLOWUP_TEMPLATE_SID,
                        variables={"1": lead_name, "2": "سمارت فيلد"},
                    )
                    if not sent_ok:
                        # Fallback to plain text if template fails
                        msg = build_follow_up_message(lead_name, fu["attempts"])
                        await self._send_whatsapp(fu["lead_phone"], msg)
                else:
                    msg = build_follow_up_message(lead_name, fu["attempts"])
                    await self._send_whatsapp(fu["lead_phone"], msg)
                next_days = None if fu["attempts"] >= 2 else (3 if fu["attempts"] == 0 else 7)
                mem.mark_follow_up_done(fu["id"], next_days=next_days, notes="تم الإرسال تلقائياً")
                mem.log_action(
                    "proactive_follow_up",
                    f"متابعة تلقائية مع {fu['lead_name']} (محاولة {fu['attempts']+1})",
                    "تم الإرسال",
                    fu["lead_id"],
                )
                sent += 1
                await asyncio.sleep(1)  # avoid rate limiting
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
        """Process a command from the business owner using Claude with tools."""
        messages = [
            *history[:-1],  # previous turns (history already includes current)
            {"role": "user", "content": message},
        ]

        response = await self.client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1024,
            system=[
                {
                    "type": "text",
                    "text": EMPLOYEE_SYSTEM_PROMPT + "\n\nأنت تتحدث الآن مع **صاحب العمل**.",
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=OWNER_COMMAND_TOOLS,
            messages=messages,
        )

        # Agentic loop for tool calls
        while response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = await self._execute_owner_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    })

            messages = [
                *messages,
                {"role": "assistant", "content": response.content},
                {"role": "user", "content": tool_results},
            ]
            response = await self.client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=1024,
                system=[
                    {
                        "type": "text",
                        "text": EMPLOYEE_SYSTEM_PROMPT + "\n\nأنت تتحدث مع صاحب العمل.",
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                tools=OWNER_COMMAND_TOOLS,
                messages=messages,
            )

        return self._extract_text(response)

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
        """Handle a conversation with a potential lead, extracting info progressively."""
        messages = [*history[:-1], {"role": "user", "content": message}]

        extract_tool = {
            "name": "add_lead_from_conversation",
            "description": "أضف العميل كـ Lead عندما تجمع اسمه ورقم هاتفه على الأقل",
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "company": {"type": "string"},
                    "cargo_type": {"type": "string"},
                    "route": {"type": "string"},
                    "budget_monthly": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": ["name"],
            },
        }

        response = await self.client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=512,
            system=[
                {
                    "type": "text",
                    "text": (
                        EMPLOYEE_SYSTEM_PROMPT
                        + f"\n\nالعميل يتحدث من الرقم: {phone}\n"
                        "اجمع المعلومات تدريجياً وأضفه كـ Lead عند توفر الاسم."
                    ),
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=[extract_tool],
            messages=messages,
        )

        if response.stop_reason == "tool_use":
            for block in response.content:
                if block.type == "tool_use" and block.name == "add_lead_from_conversation":
                    inp = block.input
                    inp["phone"] = phone
                    try:
                        lead = LeadCreate(
                            name=inp.get("name", ""),
                            phone=phone,
                            company=inp.get("company"),
                            cargo_type=inp.get("cargo_type"),
                            route=inp.get("route"),
                            notes=inp.get("notes"),
                            source=LeadSource.WHATSAPP,
                        )
                        processed = await self.pipeline.process(lead)
                        await self.notify_new_lead(
                            {**inp, "id": str(processed.lead.id), "source": "WHATSAPP",
                             "crm_id": processed.lead.crm_id},
                            {"priority": processed.lead.priority.value,
                             "score": processed.lead.score,
                             "next_actions": processed.next_actions},
                        )
                        logger.info("employee.lead_captured_from_chat", phone=phone)
                    except Exception as exc:
                        logger.error("employee.lead_capture_failed", error=str(exc))

        # Get the text response from Claude
        text_response = self._extract_text(response)
        if not text_response:
            text_response = "شكراً لتواصلكم! سنعود إليكم قريباً. 🚛"
        return text_response

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
        cargo_line = f"خاصة لـ **{cargo_type}**" if cargo_type else ""
        return (
            f"السلام عليكم {lead_name}، 👋\n\n"
            f"يسعدنا تقديم عرضنا المتميز {cargo_line} من *سمارت فيلد* للنقل المبرد:\n\n"
            f"✅ أسطول حديث من شاحنات التبريد\n"
            f"✅ تغطية شاملة في المملكة العربية السعودية\n"
            f"✅ تتبع GPS لحظة بلحظة\n"
            f"✅ معايير سلامة غذائية ISO 22000\n"
            f"✅ أسعار تنافسية مع ضمان الجودة\n\n"
            f"📞 نودّ ترتيب مكالمة قصيرة لمناقشة احتياجاتكم.\n\n"
            f"_فريق سمارت فيلد_"
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
