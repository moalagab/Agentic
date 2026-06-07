"""
Automated report generation in Arabic.
توليد التقارير التلقائية باللغة العربية
"""

from __future__ import annotations

from datetime import datetime

import structlog

from employee.memory import get_today_actions, get_actions_since

logger = structlog.get_logger(__name__)

PRIORITY_LABEL = {"HIGH": "عالية", "MEDIUM": "متوسطة", "LOW": "منخفضة"}
PRIORITY_MARKER = {"HIGH": "●", "MEDIUM": "◑", "LOW": "○"}


def build_daily_report(stats: dict) -> str:
    """Build a formatted Arabic daily report string."""
    now = datetime.utcnow()
    date_str = now.strftime("%Y/%m/%d")
    actions = get_today_actions()

    new_leads = stats.get("new_leads_today", 0)
    qualified = stats.get("qualified_today", 0)
    contacted = stats.get("contacted_today", 0)
    pipeline_total = stats.get("pipeline_total", 0)
    high_priority = stats.get("high_priority_open", 0)

    action_lines = ""
    for a in actions[:8]:
        action_lines += f"  · {a['description']}\n"
    if not action_lines:
        action_lines = "  · لا توجد أنشطة مسجلة اليوم\n"

    report = (
        f"*تقرير يومي — سمارت فيلد*\n"
        f"{date_str}\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"*العملاء اليوم*\n"
        f"  · جديد: {new_leads}  |  تواصل: {contacted}  |  مؤهَّل: {qualified}\n\n"
        f"*خط المبيعات*\n"
        f"  · الإجمالي: {pipeline_total}  |  عالي الأولوية: {high_priority}\n\n"
        f"*أنشطة الوكيل*\n"
        f"{action_lines}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"_سمارت فيلد_"
    )
    return report


def build_weekly_report(stats: dict) -> str:
    """Build a rich Arabic weekly report with real pipeline metrics."""
    actions = get_actions_since(hours=168)
    now = datetime.utcnow()

    total_leads    = stats.get("total_leads", stats.get("total_leads_week", 0))
    today_leads    = stats.get("today_leads", 0)
    won_count      = stats.get("won_count", 0)
    lost_count     = stats.get("lost_count", 0)
    qualified      = stats.get("qualified_count", 0)
    pipeline_sar   = stats.get("pipeline_value", 0)
    forecast_sar   = stats.get("revenue_forecast", pipeline_sar)
    win_rate       = stats.get("win_rate_pct", stats.get("conversion_rate", 0))
    avg_deal       = stats.get("avg_deal", 0)
    top_source     = stats.get("top_source", "غير محدد")
    by_stage       = stats.get("by_stage", {})
    churn_risks    = stats.get("churn_risks", 0)

    # Pipeline stage summary
    stage_map = {
        "lead": "جديد", "NEW_LEAD": "جديد",
        "qualified": "مؤهَّل", "QUALIFIED": "مؤهَّل",
        "contacted": "تواصل", "CONTACTED": "تواصل",
        "meeting_scheduled": "اجتماع", "MEETING_BOOKED": "اجتماع",
        "proposal_sent": "عرض", "PROPOSAL_SENT": "عرض",
        "negotiation": "تفاوض", "NEGOTIATION": "تفاوض",
        "won": "فاز ✅", "WON": "فاز ✅",
        "lost": "خسارة ❌", "LOST": "خسارة ❌",
    }
    stage_lines = ""
    for k, v in by_stage.items():
        if v > 0:
            label = stage_map.get(k, k)
            stage_lines += f"  · {label}: {v}\n"

    action_lines = ""
    for a in actions[:5]:
        action_lines += f"  · {a['description']}\n"
    if not action_lines:
        action_lines = "  · لا توجد أنشطة مسجلة\n"

    report = (
        f"📊 *التقرير الأسبوعي — Smart Field*\n"
        f"الأسبوع المنتهي: {now.strftime('%Y/%m/%d')}\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"*💰 الإيرادات*\n"
        f"  · Pipeline: *{int(pipeline_sar):,} ريال*\n"
        f"  · Forecast: *{int(forecast_sar):,} ريال*\n"
        f"  · متوسط الصفقة: {int(avg_deal):,} ريال\n\n"
        f"*📈 خط المبيعات*\n"
        f"  · إجمالي العملاء: {total_leads}\n"
        f"  · مؤهَّل: {qualified}  |  فاز: {won_count}  |  خسارة: {lost_count}\n"
        f"  · Win Rate: *{win_rate:.1f}%*\n"
        f"  · أفضل قناة: {top_source}\n\n"
        f"*🎯 توزيع Pipeline*\n"
        f"{stage_lines if stage_lines else '  · لا بيانات\n'}\n"
        f"*⚠️ تنبيهات*\n"
        f"  · عملاء بخطر المغادرة: {churn_risks}\n\n"
        f"*🤖 أنشطة الوكيل*\n"
        f"{action_lines}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"_Smart Field — النقل المبرد_"
    )
    return report


async def build_weekly_report_from_supabase(supabase_url: str, supabase_key: str) -> str:
    """
    Fetch real metrics from Supabase and build a rich weekly report.
    يجلب البيانات الحقيقية من Supabase ويولّد تقريراً أسبوعياً.
    """
    try:
        from supabase import create_client
        from datetime import timedelta

        sb = create_client(supabase_url, supabase_key)
        now = datetime.utcnow()
        week_ago = (now - timedelta(days=7)).isoformat()
        month_ago = (now - timedelta(days=30)).isoformat()

        rows = sb.table("leads").select("*").execute().data or []

        total = len(rows)
        week_leads  = sum(1 for r in rows if r.get("created_at", "") >= week_ago)
        won   = [r for r in rows if str(r.get("deal_stage") or r.get("status") or "").upper() in ("WON", "won")]
        lost  = [r for r in rows if str(r.get("deal_stage") or r.get("status") or "").upper() in ("LOST", "lost")]
        qualified = sum(1 for r in rows if (r.get("score") or 0) >= 40)

        pipeline = sum(float(r.get("expected_monthly_revenue") or 0) * 12 * float(r.get("expected_close_probability") or 0.10) for r in rows)
        avg_deal = (pipeline / max(len(won), 1)) if won else 0

        by_source: dict[str, int] = {}
        for r in rows:
            src = str(r.get("source") or "manual")
            by_source[src] = by_source.get(src, 0) + 1
        top_source_key = max(by_source, key=lambda k: by_source[k]) if by_source else "—"
        SOURCE_LABELS_AR = {
            "serpapi_prospecting": "خرائط جوجل", "website": "الموقع",
            "whatsapp": "واتساب", "linkedin": "LinkedIn",
            "manual": "يدوي", "ads": "إعلانات", "referral": "إحالة",
        }
        top_source = SOURCE_LABELS_AR.get(top_source_key, top_source_key)

        by_stage: dict[str, int] = {}
        for r in rows:
            s = str(r.get("deal_stage") or r.get("status") or "NEW_LEAD").upper()
            by_stage[s] = by_stage.get(s, 0) + 1

        closed = len(won) + len(lost)
        win_rate = (len(won) / closed * 100) if closed > 0 else 0

        # Churn risk: WON customers silent 21+ days
        from datetime import timedelta as td
        churn = 0
        for r in won:
            last = r.get("last_shipment_date")
            if last:
                try:
                    last_dt = datetime.fromisoformat(str(last).replace("Z", ""))
                    if (now - last_dt).days >= 21:
                        churn += 1
                except Exception:
                    pass

        stats = {
            "total_leads": total, "today_leads": week_leads,
            "won_count": len(won), "lost_count": len(lost),
            "qualified_count": qualified,
            "pipeline_value": round(pipeline),
            "revenue_forecast": round(pipeline),
            "win_rate_pct": round(win_rate, 1),
            "avg_deal": round(avg_deal),
            "top_source": top_source,
            "by_stage": by_stage,
            "churn_risks": churn,
        }
        return build_weekly_report(stats)

    except Exception as exc:
        return (
            f"*التقرير الأسبوعي — Smart Field*\n"
            f"{datetime.utcnow().strftime('%Y/%m/%d')}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ تعذّر جلب البيانات: {exc}\n"
            f"_Smart Field_"
        )


def build_new_lead_alert(lead_data: dict, processed_data: dict) -> str:
    """Build a WhatsApp alert message for a new lead."""
    priority = processed_data.get("priority", "MEDIUM")
    score = processed_data.get("score", 0)
    next_actions = processed_data.get("next_actions", [])
    first_action = next_actions[0] if next_actions else "المتابعة اليدوية"
    source = lead_data.get("source", "MANUAL")
    marker = PRIORITY_MARKER.get(priority, "◑")
    priority_ar = PRIORITY_LABEL.get(priority, priority)

    lines = [
        f"*عميل جديد — سمارت فيلد*",
        f"━━━━━━━━━━━━━━",
        f"الاسم: {lead_data.get('name', 'غير محدد')}",
        f"الشركة: {lead_data.get('company', 'غير محدد')}",
        f"الهاتف: {lead_data.get('phone', 'غير محدد')}",
        f"البضاعة: {lead_data.get('cargo_type', 'غير محدد')}",
        f"المسار: {lead_data.get('route', 'غير محدد')}",
        f"",
        f"{marker} الأولوية: {priority_ar}  |  التقييم: {score}/100",
        f"المصدر: {source}",
        f"",
        f"الإجراء التالي: _{first_action}_",
    ]
    return "\n".join(lines)


def build_greeting_followup_message(attempt: int) -> str:
    """
    Follow-up for contacts who sent a greeting but never shared their need.
    No name collected — keep it warm and open, never pushy.
    """
    messages = [
        # 6 hours after greeting — soft nudge
        (
            "هلا، تواصلت معنا اليوم — إذا عندك أي احتياج للنقل المبرد نقدر نساعدك."
        ),
        # 48 hours — final attempt, door open
        (
            "السلام عليكم، آخر رسالة منا — إذا احتجت شاحنات مبردة في أي وقت، سمارت فيلد هنا."
        ),
    ]
    idx = min(attempt, len(messages) - 1)
    return messages[idx]


def build_follow_up_message(lead_name: str, attempt: int) -> str:
    """Build a follow-up WhatsApp message to a lead."""
    messages = [
        (
            f"السلام عليكم {lead_name}،\n\n"
            f"تواصلت معكم سابقاً من سمارت فيلد للنقل المبرد.\n"
            f"هل لديكم وقت مناسب هذا الأسبوع للحديث؟"
        ),
        (
            f"السلام عليكم {lead_name}،\n\n"
            f"أحاول التواصل بخصوص احتياجاتكم في النقل المبرد.\n"
            f"يسعدنا تقديم عرض مخصص — هل يناسبكم التواصل الآن؟"
        ),
        (
            f"السلام عليكم {lead_name}،\n\n"
            f"آخر رسالة منا — متى يناسبكم التواصل مستقبلاً؟\n"
            f"سمارت فيلد"
        ),
    ]
    idx = min(attempt, len(messages) - 1)
    return messages[idx]
