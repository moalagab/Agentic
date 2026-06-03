"""
Automated report generation in Arabic.
توليد التقارير التلقائية باللغة العربية
"""

from __future__ import annotations

from datetime import datetime

import structlog

from employee.memory import get_today_actions, get_actions_since

logger = structlog.get_logger(__name__)

PRIORITY_EMOJI = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}
SOURCE_EMOJI = {
    "WHATSAPP": "💬",
    "LINKEDIN": "💼",
    "WEBSITE": "🌐",
    "GOOGLE_FORMS": "📋",
    "ADS": "📢",
    "MANUAL": "✍️",
}


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
        action_lines += f"  • {a['description']}\n"
    if not action_lines:
        action_lines = "  • لا توجد أنشطة مسجلة اليوم\n"

    report = (
        f"📊 *تقرير يومي - سمارت فيلد*\n"
        f"📅 {date_str}\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"📥 *العملاء المحتملون اليوم*\n"
        f"  • جديد: {new_leads}\n"
        f"  • تم التواصل: {contacted}\n"
        f"  • مؤهَّل: {qualified}\n\n"
        f"📈 *حالة خط المبيعات*\n"
        f"  • إجمالي Pipeline: {pipeline_total}\n"
        f"  • عالي الأولوية 🔴: {high_priority}\n\n"
        f"⚡ *أنشطة الوكيل اليوم*\n"
        f"{action_lines}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🤖 _وكيل سمارت فيلد الذكي_"
    )
    return report


def build_weekly_report(stats: dict) -> str:
    """Build a formatted Arabic weekly report."""
    actions = get_actions_since(hours=168)
    now = datetime.utcnow()

    total_leads = stats.get("total_leads_week", 0)
    converted = stats.get("converted_week", 0)
    conversion_rate = round((converted / total_leads * 100) if total_leads else 0, 1)
    top_source = stats.get("top_source", "غير محدد")

    report = (
        f"📊 *التقرير الأسبوعي - سمارت فيلد*\n"
        f"🗓 الأسبوع المنتهي: {now.strftime('%Y/%m/%d')}\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"📥 *إجمالي العملاء المحتملين*: {total_leads}\n"
        f"✅ *تم التحويل*: {converted}\n"
        f"📊 *معدل التحويل*: {conversion_rate}%\n"
        f"🏆 *أفضل قناة*: {SOURCE_EMOJI.get(top_source, '📌')} {top_source}\n\n"
        f"🤖 تمت {len(actions)} عملية تلقائية هذا الأسبوع.\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"_وكيل سمارت فيلد الذكي_"
    )
    return report


def build_new_lead_alert(lead_data: dict, processed_data: dict) -> str:
    """Build a WhatsApp alert message for a new lead."""
    priority = processed_data.get("priority", "MEDIUM")
    score = processed_data.get("score", 0)
    next_actions = processed_data.get("next_actions", [])
    first_action = next_actions[0] if next_actions else "المتابعة اليدوية"
    source = lead_data.get("source", "MANUAL")

    lines = [
        f"🚛 *عميل جديد - سمارت فيلد*",
        f"━━━━━━━━━━━━━━",
        f"👤 الاسم: {lead_data.get('name', 'غير محدد')}",
        f"🏢 الشركة: {lead_data.get('company', 'غير محدد')}",
        f"📞 الهاتف: {lead_data.get('phone', 'غير محدد')}",
        f"📦 نوع البضاعة: {lead_data.get('cargo_type', 'غير محدد')}",
        f"🛣 المسار: {lead_data.get('route', 'غير محدد')}",
        f"",
        f"{PRIORITY_EMOJI.get(priority, '🟡')} الأولوية: {priority}",
        f"⭐ التقييم: {score}/100",
        f"📡 المصدر: {SOURCE_EMOJI.get(source, '📌')} {source}",
        f"",
        f"⚡ الإجراء التالي:",
        f"  _{first_action}_",
    ]
    return "\n".join(lines)


def build_follow_up_message(lead_name: str, attempt: int) -> str:
    """Build a follow-up WhatsApp message to a lead."""
    messages = [
        (
            f"السلام عليكم {lead_name}،\n\n"
            f"شكراً لاهتمامكم بخدمات *سمارت فيلد* للنقل المبرد. 🚛\n\n"
            f"يسعدنا تقديم أفضل حلول النقل المبرد لأعمالكم بأعلى معايير الجودة والأمان.\n\n"
            f"هل يمكنني تحديد موعد مناسب للحديث معكم؟\n\n"
            f"_فريق المبيعات - سمارت فيلد_"
        ),
        (
            f"السلام عليكم {lead_name}، 👋\n\n"
            f"أودّ التأكد من وصول رسالتنا السابقة. نحن في *سمارت فيلد* نوفر:\n\n"
            f"✅ شاحنات تبريد حديثة\n"
            f"✅ تتبع مباشر للشحنات\n"
            f"✅ أسعار تنافسية\n"
            f"✅ تغطية شاملة في المملكة\n\n"
            f"هل أنتم مهتمون بمعرفة المزيد؟\n\n"
            f"_فريق سمارت فيلد_"
        ),
        (
            f"السلام عليكم {lead_name}،\n\n"
            f"هذه آخر مراسلة منا. نتمنى أن تتاح لنا الفرصة لخدمتكم مستقبلاً. 🙏\n\n"
            f"يمكنكم التواصل معنا في أي وقت عبر هذا الرقم.\n\n"
            f"_سمارت فيلد للنقل المبرد_"
        ),
    ]
    idx = min(attempt, len(messages) - 1)
    return messages[idx]
