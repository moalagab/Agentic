"""
Revenue Dashboard v2 — لوحة إيرادات Smart Field المتكاملة

Metrics shown:
- KPIs: Leads today, Qualified, Meetings, Proposals, Won, Pipeline Value
- Deal Pipeline funnel (visual stages)
- Conversion Rate, Average Deal Size, Revenue Forecast
- SLA Compliance
- Top leads table
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

DEAL_STAGES = [
    ("lead",               "Lead",               "#64748b"),
    ("qualified",          "Qualified",          "#3b82f6"),
    ("contacted",          "Contacted",          "#8b5cf6"),
    ("meeting_scheduled",  "Meeting",            "#f59e0b"),
    ("proposal_sent",      "Proposal",           "#f97316"),
    ("negotiation",        "Negotiation",        "#ec4899"),
    ("won",                "Won ✓",              "#22c55e"),
    ("lost",               "Lost ✗",             "#ef4444"),
]

STAGE_AR = {
    "lead":              "Lead جديد",
    "qualified":         "مؤهَّل",
    "contacted":         "تم التواصل",
    "meeting_scheduled": "اجتماع محدد",
    "proposal_sent":     "عرض مرسل",
    "negotiation":       "تفاوض",
    "won":               "فاز ✓",
    "lost":              "خسارة ✗",
}


async def get_dashboard_data(supabase_client: Any) -> dict:
    """Fetch all dashboard metrics from Supabase."""
    loop = asyncio.get_running_loop()
    now = datetime.utcnow()
    today   = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    week    = (now - timedelta(days=7)).isoformat()
    month   = (now - timedelta(days=30)).isoformat()

    try:
        all_r = await loop.run_in_executor(None, lambda: supabase_client.table("leads").select("*").execute())
        leads = all_r.data or []
    except Exception as exc:
        logger.error("Dashboard fetch failed", error=str(exc))
        leads = []

    total = len(leads)

    # Counts by period
    today_leads  = sum(1 for l in leads if l.get("created_at", "") >= today)
    week_leads   = sum(1 for l in leads if l.get("created_at", "") >= week)
    month_leads  = sum(1 for l in leads if l.get("created_at", "") >= month)

    # Pipeline stages
    by_stage: dict[str, int] = {s[0]: 0 for s in DEAL_STAGES}
    by_priority: dict[str, int] = {}
    by_source:   dict[str, int] = {}

    scores, budgets, revenues, deal_values = [], [], [], []
    won_count = lost_count = meeting_count = proposal_count = qualified_count = 0
    response_times = []

    for l in leads:
        stage    = str(l.get("deal_stage") or "lead").replace("LeadStatus.", "")
        priority = str(l.get("priority") or "low").replace("LeadPriority.", "")
        source   = str(l.get("source")   or "manual").replace("LeadSource.", "")

        # Normalise old status → stage
        status = str(l.get("status") or "new").replace("LeadStatus.", "")
        if stage == "lead" and status in ("contacted", "qualified", "converted"):
            stage = {"contacted": "contacted", "qualified": "qualified", "converted": "won"}.get(status, stage)

        by_stage[stage] = by_stage.get(stage, 0) + 1
        by_priority[priority] = by_priority.get(priority, 0) + 1
        by_source[source]     = by_source.get(source, 0) + 1

        if l.get("score"):         scores.append(l["score"])
        if l.get("budget_monthly"): budgets.append(float(l["budget_monthly"]))
        if l.get("estimated_monthly_revenue"): revenues.append(float(l["estimated_monthly_revenue"]))
        if l.get("expected_deal_value"):       deal_values.append(float(l["expected_deal_value"]))
        if l.get("response_time_minutes"):     response_times.append(int(l["response_time_minutes"]))

        if stage == "won":               won_count += 1
        if stage == "lost":              lost_count += 1
        if stage == "meeting_scheduled": meeting_count += 1
        if stage == "proposal_sent":     proposal_count += 1
        if stage == "qualified":         qualified_count += 1

    avg_score   = round(sum(scores) / len(scores), 1) if scores else 0
    avg_budget  = round(sum(budgets) / len(budgets))  if budgets else 0
    avg_deal    = round(sum(deal_values) / len(deal_values)) if deal_values else avg_budget * 12
    pipeline_v  = round(sum(deal_values)) if deal_values else 0
    rev_forecast = round(pipeline_v * 0.25)  # 25% expected to close

    qualified_total = sum(1 for l in leads if (l.get("score") or 0) >= 40)
    conversion_rate = round(won_count / qualified_total * 100, 1) if qualified_total else 0

    avg_response = round(sum(response_times) / len(response_times), 0) if response_times else None
    sla_ok = sum(1 for t in response_times if t <= 15)
    sla_rate = round(sla_ok / len(response_times) * 100, 0) if response_times else None

    top_leads = sorted(
        [l for l in leads if (l.get("score") or 0) >= 40],
        key=lambda x: x.get("score", 0), reverse=True
    )[:8]

    return {
        "total": total, "today": today_leads, "week": week_leads, "month": month_leads,
        "qualified": qualified_count, "meetings": meeting_count,
        "proposals": proposal_count, "won": won_count, "lost": lost_count,
        "avg_score": avg_score, "avg_budget": avg_budget, "avg_deal": avg_deal,
        "pipeline_value": pipeline_v, "revenue_forecast": rev_forecast,
        "conversion_rate": conversion_rate,
        "by_stage": by_stage, "by_priority": by_priority, "by_source": by_source,
        "avg_response_min": avg_response, "sla_rate": sla_rate,
        "top_leads": top_leads,
        "generated_at": now.isoformat(),
    }


def render_dashboard_html(data: dict) -> str:
    total      = data["total"]
    today      = data["today"]
    month      = data["month"]
    qualified  = data["qualified"]
    meetings   = data["meetings"]
    proposals  = data["proposals"]
    won        = data["won"]
    avg_score  = data["avg_score"]
    avg_deal   = data["avg_deal"]
    pipeline_v = data["pipeline_value"]
    rev_fore   = data["revenue_forecast"]
    conv_rate  = data["conversion_rate"]
    by_stage   = data["by_stage"]
    by_source  = data["by_source"]
    top_leads  = data["top_leads"]
    avg_resp   = data["avg_response_min"]
    sla_rate   = data["sla_rate"]
    gen_at     = data["generated_at"][:19].replace("T", " ")

    PRIORITY_COLOR = {"high": "#ef4444", "medium": "#f59e0b", "low": "#22c55e"}
    SOURCE_AR = {"website": "الموقع", "whatsapp": "واتساب", "ads": "إعلانات",
                 "linkedin": "LinkedIn", "manual": "يدوي", "google_forms": "نموذج",
                 "telegram": "Telegram"}

    # ── Pipeline Funnel ────────────────────────────────────────────────────────
    max_stage = max(by_stage.values(), default=1) or 1
    funnel_bars = ""
    for stage_id, stage_en, color in DEAL_STAGES:
        count = by_stage.get(stage_id, 0)
        pct   = max(6, round(count / max_stage * 100))
        label = STAGE_AR.get(stage_id, stage_en)
        funnel_bars += f"""
        <div style="display:flex;align-items:center;margin-bottom:8px;gap:12px">
          <div style="width:130px;text-align:right;font-size:13px;color:#555">{label}</div>
          <div style="flex:1;background:#f1f5f9;border-radius:6px;height:28px;position:relative">
            <div style="width:{pct}%;background:{color};height:28px;border-radius:6px;min-width:28px;display:flex;align-items:center;padding:0 10px">
              <span style="color:white;font-size:12px;font-weight:600">{count}</span>
            </div>
          </div>
        </div>"""

    # ── Source Distribution ────────────────────────────────────────────────────
    max_src = max(by_source.values(), default=1) or 1
    src_bars = ""
    for k, v in sorted(by_source.items(), key=lambda x: -x[1])[:6]:
        pct = max(4, round(v / max_src * 100))
        src_bars += f"""
        <div style="margin-bottom:10px">
          <div style="display:flex;justify-content:space-between;margin-bottom:3px">
            <span style="font-size:13px">{SOURCE_AR.get(k, k)}</span>
            <strong style="font-size:13px">{v}</strong>
          </div>
          <div style="background:#f1f5f9;border-radius:4px;height:8px">
            <div style="width:{pct}%;background:#3b82f6;height:8px;border-radius:4px"></div>
          </div>
        </div>"""

    # ── Top Leads Table ────────────────────────────────────────────────────────
    rows = ""
    for l in top_leads:
        p = str(l.get("priority", "low")).replace("LeadPriority.", "").lower()
        s = str(l.get("deal_stage") or l.get("status") or "lead")
        stage_label = STAGE_AR.get(s.replace("LeadStatus.", ""), s)
        color = PRIORITY_COLOR.get(p, "#94a3b8")
        est_rev = l.get("estimated_monthly_revenue") or l.get("budget_monthly") or 0
        rows += f"""
        <tr style="border-bottom:1px solid #f1f5f9">
          <td style="padding:10px 12px;font-weight:500">{l.get('name','')}</td>
          <td style="padding:10px 12px;color:#64748b;font-size:13px">{l.get('cargo_type') or '—'}</td>
          <td style="padding:10px 12px;text-align:center">
            <span style="background:{color};color:white;padding:3px 10px;border-radius:12px;font-size:12px;font-weight:600">{l.get('score', 0)}</span>
          </td>
          <td style="padding:10px 12px;font-size:13px">{stage_label}</td>
          <td style="padding:10px 12px;font-size:13px;color:#059669">{int(est_rev):,} ر</td>
          <td style="padding:10px 12px;color:#94a3b8;font-size:12px">{str(l.get('created_at',''))[:10]}</td>
        </tr>"""

    if not rows:
        rows = '<tr><td colspan="6" style="text-align:center;padding:30px;color:#94a3b8">لا توجد leads مؤهلة بعد</td></tr>'

    sla_html = f"""
      <div style="display:flex;gap:12px;margin-top:12px">
        <div style="flex:1;background:#f0fdf4;border-radius:8px;padding:12px;text-align:center">
          <div style="font-size:20px;font-weight:700;color:#16a34a">{avg_resp:.0f} دقيقة</div>
          <div style="font-size:11px;color:#16a34a;margin-top:2px">متوسط وقت الاستجابة</div>
        </div>
        <div style="flex:1;background:#{'f0fdf4' if (sla_rate or 0) >= 80 else 'fef2f2'};border-radius:8px;padding:12px;text-align:center">
          <div style="font-size:20px;font-weight:700;color:#{'16a34a' if (sla_rate or 0) >= 80 else 'dc2626'}">{sla_rate:.0f}%</div>
          <div style="font-size:11px;color:#64748b;margin-top:2px">SLA Compliance</div>
        </div>
      </div>""" if avg_resp is not None else '<p style="color:#94a3b8;font-size:13px;margin-top:8px">لا توجد بيانات استجابة بعد</p>'

    return f"""<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Smart Field — Revenue Dashboard</title>
<meta http-equiv="refresh" content="120">
<style>
  *{{margin:0;padding:0;box-sizing:border-box}}
  body{{font-family:'Segoe UI',Arial,sans-serif;background:#f8fafc;color:#1e293b;direction:rtl}}
  .nav{{background:linear-gradient(135deg,#0f172a,#1e40af);color:white;padding:16px 28px;display:flex;justify-content:space-between;align-items:center}}
  .nav h1{{font-size:18px;font-weight:700}}
  .nav small{{opacity:.7;font-size:11px}}
  .container{{max-width:1280px;margin:0 auto;padding:20px}}
  .kpi-row{{display:grid;grid-template-columns:repeat(7,1fr);gap:12px;margin-bottom:20px}}
  .kpi{{background:white;border-radius:10px;padding:16px;box-shadow:0 1px 4px rgba(0,0,0,.06);text-align:center}}
  .kpi .v{{font-size:26px;font-weight:700;line-height:1}}
  .kpi .l{{font-size:11px;color:#64748b;margin-top:6px;text-transform:uppercase;letter-spacing:.5px}}
  .kpi.highlight{{background:linear-gradient(135deg,#1e40af,#3b82f6);color:white}}
  .kpi.highlight .l{{color:rgba(255,255,255,.8)}}
  .kpi.green{{background:linear-gradient(135deg,#059669,#34d399);color:white}}
  .kpi.green .l{{color:rgba(255,255,255,.85)}}
  .grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}}
  .grid3{{display:grid;grid-template-columns:2fr 1fr;gap:16px;margin-bottom:16px}}
  .card{{background:white;border-radius:10px;padding:20px;box-shadow:0 1px 4px rgba(0,0,0,.06)}}
  .card h2{{font-size:14px;font-weight:600;color:#475569;margin-bottom:14px;border-bottom:1px solid #f1f5f9;padding-bottom:10px;display:flex;align-items:center;gap:8px}}
  .forecast-bar{{background:linear-gradient(135deg,#dc2626,#ef4444);color:white;border-radius:10px;padding:20px;text-align:center;margin-bottom:16px}}
  .forecast-bar .big{{font-size:34px;font-weight:700;direction:ltr}}
  .forecast-bar p{{opacity:.9;margin-top:6px;font-size:13px}}
  table{{width:100%;border-collapse:collapse}}
  th{{background:#f8fafc;color:#64748b;padding:10px 12px;font-size:12px;font-weight:600;text-align:right;border-bottom:2px solid #e2e8f0}}
  @media(max-width:900px){{.kpi-row{{grid-template-columns:repeat(4,1fr)}}.grid,.grid3{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<div class="nav">
  <div><h1>❄️ Smart Field — Revenue Dashboard</h1><small>آخر تحديث: {gen_at} UTC | يتجدد كل دقيقتين</small></div>
  <div style="display:flex;gap:16px;font-size:13px">
    <span>اليوم: <strong>{today}</strong> leads</span>
    <span>هذا الشهر: <strong>{month}</strong> leads</span>
  </div>
</div>

<div class="container">

  <!-- KPI Row -->
  <div class="kpi-row">
    <div class="kpi"><div class="v">{total}</div><div class="l">إجمالي Leads</div></div>
    <div class="kpi highlight"><div class="v">{qualified}</div><div class="l">Qualified</div></div>
    <div class="kpi"><div class="v">{meetings}</div><div class="l">Meetings</div></div>
    <div class="kpi"><div class="v">{proposals}</div><div class="l">Proposals</div></div>
    <div class="kpi green"><div class="v">{won}</div><div class="l">Won Deals</div></div>
    <div class="kpi"><div class="v">{conv_rate}%</div><div class="l">Conversion</div></div>
    <div class="kpi"><div class="v">{avg_score}</div><div class="l">Avg Score</div></div>
  </div>

  <!-- Revenue Forecast -->
  <div class="forecast-bar">
    <div class="big">{pipeline_v:,} ريال</div>
    <p>Pipeline Value (قيمة الصفقات المتوقعة سنوياً) &nbsp;|&nbsp; Revenue Forecast: <strong>{rev_fore:,} ريال</strong></p>
  </div>

  <!-- Pipeline Funnel + Source -->
  <div class="grid">
    <div class="card">
      <h2>📊 Deal Pipeline — مراحل الصفقات</h2>
      {funnel_bars}
    </div>
    <div class="card">
      <h2>📡 مصادر العملاء</h2>
      {src_bars or '<p style="color:#94a3b8">لا توجد بيانات</p>'}
      <div style="margin-top:20px">
        <h2 style="border:none;padding-bottom:0;margin-bottom:12px">⏱️ SLA — وقت الاستجابة</h2>
        <div style="font-size:12px;color:#64748b;margin-bottom:6px">الهدف: &lt; 5 دقائق للأولوية العالية</div>
        {sla_html}
      </div>
    </div>
  </div>

  <!-- Stats row -->
  <div class="grid">
    <div class="card">
      <h2>💰 مؤشرات المال</h2>
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px">
        <div style="background:#f0fdf4;border-radius:8px;padding:14px;text-align:center">
          <div style="font-size:18px;font-weight:700;color:#16a34a">{avg_deal:,}</div>
          <div style="font-size:11px;color:#16a34a;margin-top:4px">Avg Deal Size (ر)</div>
        </div>
        <div style="background:#eff6ff;border-radius:8px;padding:14px;text-align:center">
          <div style="font-size:18px;font-weight:700;color:#2563eb">{conv_rate}%</div>
          <div style="font-size:11px;color:#2563eb;margin-top:4px">Conversion Rate</div>
        </div>
        <div style="background:#fef9c3;border-radius:8px;padding:14px;text-align:center">
          <div style="font-size:18px;font-weight:700;color:#ca8a04">{rev_fore:,}</div>
          <div style="font-size:11px;color:#ca8a04;margin-top:4px">Revenue Forecast (ر)</div>
        </div>
      </div>
    </div>
    <div class="card">
      <h2>🎯 أسئلة المدير</h2>
      <table style="font-size:13px">
        <tr><td style="padding:6px 0;color:#64748b">Leads هذا الشهر</td><td style="padding:6px 0;font-weight:600;text-align:left">{month}</td></tr>
        <tr><td style="padding:6px 0;color:#64748b">Qualified Leads</td><td style="padding:6px 0;font-weight:600;text-align:left">{qualified}</td></tr>
        <tr><td style="padding:6px 0;color:#64748b">Conversion Rate</td><td style="padding:6px 0;font-weight:600;text-align:left">{conv_rate}%</td></tr>
        <tr><td style="padding:6px 0;color:#64748b">Average Deal Size</td><td style="padding:6px 0;font-weight:600;text-align:left">{avg_deal:,} ر</td></tr>
        <tr><td style="padding:6px 0;color:#64748b">Revenue Forecast</td><td style="padding:6px 0;font-weight:600;color:#dc2626;text-align:left">{rev_fore:,} ر</td></tr>
        <tr><td style="padding:6px 0;color:#64748b">Won Deals</td><td style="padding:6px 0;font-weight:600;color:#16a34a;text-align:left">{won}</td></tr>
      </table>
    </div>
  </div>

  <!-- Top Leads -->
  <div class="card">
    <h2>🏆 أفضل العملاء المؤهلين</h2>
    <table>
      <thead><tr>
        <th>الاسم</th><th>نوع البضاعة</th><th>Score</th><th>المرحلة</th><th>الإيراد المتوقع</th><th>التاريخ</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>

</div>
</body>
</html>"""
