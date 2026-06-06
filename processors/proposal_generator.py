"""
AI Proposal Generator — مولّد العروض بالذكاء الاصطناعي

Pipeline:
  Lead → Qualified → AI gathers requirements → Proposal Draft
  → Owner Approval (Telegram) → Send to Client

Flow:
  1. generate_proposal_text() — Claude writes the proposal
  2. ProposalApprovalManager.request_approval() — sends draft to owner via Telegram
  3. Owner replies "موافق" → auto-sent to client via WhatsApp
  4. Owner replies "تعديل: ..." → regenerated with notes, re-sent for approval
  5. Owner replies "رفض" → proposal archived, lead flagged for manual review
"""

from __future__ import annotations

import io
import os
import asyncio
import json
from datetime import datetime
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)

# ─── Pending Approvals Store (in-memory, keyed by proposal_id) ───────────────
# Structure: { proposal_id: { "lead": {...}, "text": "...", "telegram_msg_id": int, ... } }
_pending_approvals: dict[str, dict] = {}


def _build_proposal_prompt(lead: dict, revision_notes: str = "") -> str:
    """Build the Arabic proposal generation prompt."""
    from agent.knowledge_base import get_kb_text

    name        = lead.get("name", "")
    company     = lead.get("company", "")
    cargo_type  = lead.get("cargo_type", "")
    fleet_size  = lead.get("fleet_size_needed", "")
    budget      = lead.get("budget_monthly", "")
    route_from  = lead.get("route_from", "")
    route_to    = lead.get("route_to", "")
    score       = lead.get("score", 0)
    prob        = lead.get("probability_to_close", 0)
    ltv         = lead.get("estimated_ltv", 0)

    revision_section = f"\n\n## ملاحظات التعديل من المالك\n{revision_notes}" if revision_notes else ""

    return f"""
أنت مسؤول مبيعات متخصص في شركة سمارت فيلد للنقل المبرد — المملكة العربية السعودية.

## معلومات الشركة للرجوع إليها
{get_kb_text()}

## معلومات العميل
الاسم: {name}
الشركة: {company or 'غير محدد'}
البضاعة: {cargo_type or 'غير محدد'}
الشاحنات: {fleet_size or 'غير محدد'}
الميزانية: {budget or 'غير محدد'} ريال/شهر
المسار: {route_from or '—'} → {route_to or '—'}
درجة التأهيل: {score}/100
احتمال الإغلاق: {prob*100:.0f}%
{revision_section}

## المطلوب
اكتب عرض سعر احترافي ومقنع باللغة العربية يتضمن:
1. تحية شخصية مختصرة (سطر واحد)
2. فهم الاحتياج (2-3 جمل تُظهر أنك فهمت وضعهم تحديداً)
3. الحل المقترح:
   - نوع الشاحنات والمواصفات (درجة الحرارة المطلوبة)
   - تفاصيل تغطية المسار
   - التتبع والتوثيق (GPS، logger درجة الحرارة)
4. الأسعار التقديرية بأرقام واضحة (استخدم جدول الأسعار أعلاه)
5. 3 مزايا تنافسية محددة لسمارت فيلد
6. دعوة للخطوة التالية (مكالمة 15 دقيقة)

النبرة: احترافي، مباشر، ثقيل بالخبرة. لا حشو.
الطول: 350-500 كلمة.
""".strip()


async def generate_proposal_text(lead: dict, anthropic_key: str, revision_notes: str = "") -> str:
    """
    Use Claude to generate a personalized proposal in Arabic.
    يستخدم Claude لإنشاء عرض مخصص باللغة العربية.
    """
    try:
        from agent.ai_client import get_text
        return await get_text(
            anthropic_key,
            "أنت كاتب عروض احترافي لشركة سمارت فيلد للنقل المبرد في السعودية. اكتب بالعربية فقط.",
            _build_proposal_prompt(lead, revision_notes),
            max_tokens=2000,
        )
    except Exception as exc:
        logger.error("Proposal generation failed", error=str(exc))
        return _fallback_proposal(lead)


def _fallback_proposal(lead: dict) -> str:
    """Basic fallback proposal if Claude fails."""
    name = lead.get("name", "العميل الكريم")
    return f"""
عزيزي/عزيزتي {name}،

شكراً لتواصلكم مع Smart Field للنقل المبرد.

يسعدنا تقديم خدماتنا المتميزة في نقل البضائع المبردة بكفاءة عالية وأسعار تنافسية.

للمزيد من التفاصيل، يرجى التواصل مع فريق المبيعات.

مع تحيات،
فريق Smart Field
📞 +966 5X XXX XXXX
""".strip()


def generate_proposal_pdf(proposal_text: str, lead: dict) -> bytes:
    """
    Convert proposal text to a PDF file using WeasyPrint.
    يحوّل نص العرض إلى ملف PDF.
    """
    name    = lead.get("name", "")
    company = lead.get("company", "")
    date    = datetime.now().strftime("%Y/%m/%d")

    html_content = f"""
<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<style>
  @import url('https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700&display=swap');
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: 'Cairo', Arial, sans-serif;
    direction: rtl;
    text-align: right;
    color: #1a1a2e;
    background: #fff;
    padding: 40px;
    line-height: 1.8;
  }}
  .header {{
    background: linear-gradient(135deg, #0f3460, #16213e);
    color: white;
    padding: 30px 40px;
    border-radius: 12px;
    margin-bottom: 30px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }}
  .header h1 {{ font-size: 28px; font-weight: 700; }}
  .header .logo {{ font-size: 22px; font-weight: 600; color: #e94560; }}
  .meta {{
    background: #f8f9fc;
    border-right: 4px solid #e94560;
    padding: 15px 20px;
    margin-bottom: 25px;
    border-radius: 4px;
  }}
  .meta p {{ margin: 4px 0; font-size: 14px; color: #555; }}
  .meta strong {{ color: #1a1a2e; }}
  .body {{
    font-size: 15px;
    line-height: 2;
    white-space: pre-wrap;
    color: #333;
  }}
  .footer {{
    margin-top: 40px;
    padding-top: 20px;
    border-top: 2px solid #eee;
    text-align: center;
    color: #888;
    font-size: 13px;
  }}
  .badge {{
    display: inline-block;
    background: #e94560;
    color: white;
    padding: 4px 12px;
    border-radius: 20px;
    font-size: 12px;
    margin-top: 10px;
  }}
</style>
</head>
<body>
  <div class="header">
    <div>
      <h1>عرض سعر</h1>
      <p style="margin-top:6px; font-size:14px; opacity:0.85;">النقل المبرد المتكامل</p>
    </div>
    <div class="logo">Smart Field ❄️</div>
  </div>

  <div class="meta">
    <p><strong>إلى:</strong> {name} {('— ' + company) if company else ''}</p>
    <p><strong>التاريخ:</strong> {date}</p>
    <p><strong>مُعدّ بواسطة:</strong> فريق مبيعات Smart Field</p>
  </div>

  <div class="body">{proposal_text}</div>

  <div class="footer">
    <p>Smart Field للنقل المبرد — المملكة العربية السعودية</p>
    <span class="badge">سري وخاص بالعميل</span>
  </div>
</body>
</html>
"""

    try:
        from weasyprint import HTML
        pdf_bytes = HTML(string=html_content).write_pdf()
        return pdf_bytes
    except Exception as exc:
        logger.warning("WeasyPrint failed, using fallback PDF", error=str(exc))
        return _generate_pdf_reportlab(proposal_text, lead)


def _generate_pdf_reportlab(proposal_text: str, lead: dict) -> bytes:
    """Fallback PDF using ReportLab (no CSS, basic)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
    from reportlab.lib.enums import TA_RIGHT, TA_CENTER

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            rightMargin=2*cm, leftMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("Title", parent=styles["Title"],
                                  fontSize=20, textColor=colors.HexColor("#0f3460"),
                                  alignment=TA_CENTER, spaceAfter=10)
    body_style = ParagraphStyle("Body", parent=styles["Normal"],
                                 fontSize=11, leading=18, spaceAfter=8)
    meta_style = ParagraphStyle("Meta", parent=styles["Normal"],
                                 fontSize=10, textColor=colors.grey, spaceAfter=4)

    name = lead.get("name", "")
    date = datetime.now().strftime("%Y/%m/%d")

    story = [
        Paragraph("Smart Field — عرض سعر", title_style),
        HRFlowable(width="100%", thickness=2, color=colors.HexColor("#e94560")),
        Spacer(1, 12),
        Paragraph(f"إلى: {name}", meta_style),
        Paragraph(f"التاريخ: {date}", meta_style),
        Spacer(1, 16),
    ]

    for line in proposal_text.split("\n"):
        if line.strip():
            story.append(Paragraph(line.strip(), body_style))
        else:
            story.append(Spacer(1, 6))

    story.append(Spacer(1, 20))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.lightgrey))
    story.append(Paragraph("Smart Field للنقل المبرد — المملكة العربية السعودية", meta_style))

    doc.build(story)
    return buffer.getvalue()


async def create_and_save_proposal(lead: dict, anthropic_key: str, output_dir: str = "data/proposals") -> str:
    """
    Full pipeline: generate text → create PDF → save to disk.
    Returns the file path.
    """
    os.makedirs(output_dir, exist_ok=True)

    lead_id = lead.get("id", "unknown")
    name_slug = lead.get("name", "lead").replace(" ", "_").lower()
    date_slug = datetime.now().strftime("%Y%m%d")
    filename = f"{output_dir}/proposal_{name_slug}_{date_slug}.pdf"

    proposal_text = await generate_proposal_text(lead, anthropic_key)
    pdf_bytes = generate_proposal_pdf(proposal_text, lead)

    with open(filename, "wb") as f:
        f.write(pdf_bytes)

    logger.info("Proposal saved", path=filename, lead_id=lead_id, size_kb=len(pdf_bytes)//1024)
    return filename


# ─── Proposal Approval Manager ────────────────────────────────────────────────

class ProposalApprovalManager:
    """
    Manages the proposal approval flow via Telegram.

    Flow:
      1. request_approval(lead) → generate draft → send to owner on Telegram
      2. Owner replies:
         - "موافق"       → send proposal to client via WhatsApp
         - "تعديل: ..."  → revise with notes, re-send for approval
         - "رفض"         → archive proposal, flag lead
      3. handle_owner_reply(proposal_id, reply_text) → acts accordingly
    """

    def __init__(
        self,
        anthropic_key: str,
        telegram_handler: Any,
        wa_notifier: Any,
        owner_chat_ids: list[str],
    ) -> None:
        self.anthropic_key = anthropic_key
        self.telegram = telegram_handler
        self.wa_notifier = wa_notifier
        self.owner_chat_ids = owner_chat_ids
        self._log = logger.bind(component="ProposalApproval")

    async def request_approval(self, lead: dict) -> str:
        """
        Generate a proposal draft and send it to the owner for approval.
        Returns the proposal_id.
        """
        import uuid
        proposal_id = f"prop_{lead.get('id', uuid.uuid4().hex[:8])}"

        self._log.info("Generating proposal draft", lead=lead.get("name"), proposal_id=proposal_id)
        proposal_text = await generate_proposal_text(lead, self.anthropic_key)

        # Store pending approval
        _pending_approvals[proposal_id] = {
            "lead": lead,
            "text": proposal_text,
            "status": "pending",
            "created_at": datetime.utcnow().isoformat(),
            "revisions": 0,
        }

        # Format message for owner
        preview = proposal_text[:300] + "..." if len(proposal_text) > 300 else proposal_text
        approval_msg = (
            f"*عرض جديد — موافقة مطلوبة*\n"
            f"━━━━━━━━━━━━━━\n"
            f"العميل: {lead.get('name', '—')}\n"
            f"الشركة: {lead.get('company', '—')}\n"
            f"التقييم: {lead.get('score', 0)}/100\n"
            f"━━━━━━━━━━━━━━\n"
            f"*معاينة العرض:*\n{preview}\n"
            f"━━━━━━━━━━━━━━\n"
            f"رد بـ:\n"
            f"  موافق {proposal_id}\n"
            f"  تعديل {proposal_id}: [ملاحظاتك]\n"
            f"  رفض {proposal_id}"
        )

        for chat_id in self.owner_chat_ids:
            try:
                await self.telegram.send_message(chat_id, approval_msg)
            except Exception as exc:
                self._log.error("Failed to send approval request", chat_id=chat_id, error=str(exc))

        self._log.info("Approval request sent", proposal_id=proposal_id)
        return proposal_id

    async def handle_owner_reply(self, reply_text: str) -> Optional[str]:
        """
        Process owner's reply to a proposal.
        Returns feedback message for the owner.
        """
        text = reply_text.strip()

        # ── موافق <proposal_id> ───────────────────────────────────────────────
        if text.startswith("موافق"):
            parts = text.split()
            proposal_id = parts[1] if len(parts) > 1 else None
            if not proposal_id or proposal_id not in _pending_approvals:
                return "لم أجد هذا العرض. تأكد من رقم المعرّف."

            approval = _pending_approvals[proposal_id]
            approval["status"] = "approved"
            lead = approval["lead"]
            proposal_text = approval["text"]

            # Send to client via WhatsApp
            client_phone = lead.get("phone")
            if client_phone and self.wa_notifier:
                await self.wa_notifier.send_custom_message(client_phone, proposal_text)
                self._log.info("Proposal sent to client", phone=client_phone, proposal_id=proposal_id)
                del _pending_approvals[proposal_id]
                return f"تم إرسال العرض لـ {lead.get('name')} على واتساب."
            else:
                return f"لا يوجد رقم واتساب للعميل {lead.get('name')}. العرض موافق عليه لكن لم يُرسل."

        # ── تعديل <proposal_id>: <notes> ─────────────────────────────────────
        if text.startswith("تعديل"):
            colon_idx = text.find(":")
            if colon_idx == -1:
                return "الصيغة: تعديل <رقم العرض>: [ملاحظاتك]"

            header = text[:colon_idx]
            parts = header.split()
            proposal_id = parts[1] if len(parts) > 1 else None
            notes = text[colon_idx+1:].strip()

            if not proposal_id or proposal_id not in _pending_approvals:
                return "لم أجد هذا العرض. تأكد من رقم المعرّف."

            approval = _pending_approvals[proposal_id]
            approval["revisions"] += 1

            if approval["revisions"] > 3:
                return "تم تجاوز الحد الأقصى للتعديلات (3). راجع العرض يدوياً."

            self._log.info("Revising proposal", proposal_id=proposal_id, notes=notes)
            new_text = await generate_proposal_text(approval["lead"], self.anthropic_key, notes)
            approval["text"] = new_text
            approval["status"] = "pending"

            # Re-send revised draft
            preview = new_text[:300] + "..." if len(new_text) > 300 else new_text
            revised_msg = (
                f"*عرض معدّل (مراجعة {approval['revisions']})*\n"
                f"━━━━━━━━━━━━━━\n"
                f"ملاحظاتك: {notes}\n"
                f"━━━━━━━━━━━━━━\n"
                f"{preview}\n"
                f"━━━━━━━━━━━━━━\n"
                f"موافق {proposal_id} | تعديل {proposal_id}: ... | رفض {proposal_id}"
            )
            for chat_id in self.owner_chat_ids:
                try:
                    await self.telegram.send_message(chat_id, revised_msg)
                except Exception:
                    pass

            return f"تم تعديل العرض وإعادة إرساله (مراجعة {approval['revisions']})."

        # ── رفض <proposal_id> ────────────────────────────────────────────────
        if text.startswith("رفض"):
            parts = text.split()
            proposal_id = parts[1] if len(parts) > 1 else None
            if not proposal_id or proposal_id not in _pending_approvals:
                return "لم أجد هذا العرض."

            approval = _pending_approvals[proposal_id]
            approval["status"] = "rejected"
            lead_name = approval["lead"].get("name", "—")
            del _pending_approvals[proposal_id]
            self._log.info("Proposal rejected", proposal_id=proposal_id, lead=lead_name)
            return f"تم رفض العرض وأرشفته. العميل {lead_name} يحتاج مراجعة يدوية."

        return None  # Not a proposal command

    def get_pending_count(self) -> int:
        return sum(1 for a in _pending_approvals.values() if a["status"] == "pending")
