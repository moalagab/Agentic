"""
AI Proposal Generator — مولّد العروض بالذكاء الاصطناعي

Generates personalized Arabic proposals for Smart Field leads using Claude,
then produces a PDF and can send it via WhatsApp or as a download link.
"""

from __future__ import annotations

import io
import os
import asyncio
from datetime import datetime
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)


def _build_proposal_prompt(lead: dict) -> str:
    """Build the Arabic proposal generation prompt."""
    name        = lead.get("name", "")
    company     = lead.get("company", "")
    cargo_type  = lead.get("cargo_type", "")
    fleet_size  = lead.get("fleet_size_needed", "")
    budget      = lead.get("budget_monthly", "")
    route_from  = lead.get("route_from", "")
    route_to    = lead.get("route_to", "")
    score       = lead.get("score", 0)

    return f"""
أنت مسؤول مبيعات في شركة Smart Field للنقل المبرد.
اكتب عرض سعر احترافي باللغة العربية لهذا العميل:

معلومات العميل:
- الاسم: {name}
- الشركة: {company or 'غير محدد'}
- نوع البضاعة: {cargo_type or 'غير محدد'}
- عدد الشاحنات المطلوبة: {fleet_size or 'غير محدد'}
- الميزانية الشهرية: {budget or 'غير محدد'} ريال
- المسار: {route_from or '—'} → {route_to or '—'}
- درجة التأهيل: {score}/100

متطلبات العرض:
1. **تحية شخصية** باسم العميل
2. **ملخص فهم الاحتياج** (2-3 جمل)
3. **الحل المقترح** مع تفاصيل:
   - عدد الشاحنات ومواصفاتها (مبردة، درجة حرارة -18 إلى +8)
   - تغطية المسار
   - جودة الخدمة (GPS تتبع، تقارير دورية، سائقين معتمدين)
4. **الأسعار التقديرية** (بناءً على الميزانية المذكورة ±10%)
5. **مميزات Smart Field** (3-5 نقاط)
6. **الخطوة التالية** (دعوة لاجتماع أو مكالمة)
7. **توقيع** فريق المبيعات

اجعل العرض احترافياً، ودياً، ومقنعاً. اذكر أرقاماً واضحة.
الطول: 400-600 كلمة.
""".strip()


async def generate_proposal_text(lead: dict, anthropic_key: str) -> str:
    """
    Use Claude to generate a personalized proposal in Arabic.
    يستخدم Claude لإنشاء عرض مخصص باللغة العربية.
    """
    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=anthropic_key)

        message = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            system="أنت كاتب عروض محترف لشركة Smart Field للنقل المبرد في السعودية.",
            messages=[{"role": "user", "content": _build_proposal_prompt(lead)}],
        )
        return message.content[0].text

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
