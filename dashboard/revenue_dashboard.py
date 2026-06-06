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

    by_stage: dict[str, int] = {s[0]: 0 for s in DEAL_STAGES}
    by_priority: dict[str, int] = {}
    by_source:   dict[str, int] = {}
    by_icp:      dict[str, int] = {}

    scores, monthly_revenues = [], []
    won_count = lost_count = meeting_count = proposal_count = qualified_count = 0
    response_times = []
    total_pipeline = 0.0   # annual pipeline
    total_actual   = 0.0

    for l in leads:
        raw_stage = str(l.get("deal_stage") or l.get("status") or "NEW_LEAD")
        # Normalise to lowercase stage key used in DEAL_STAGES list
        stage = raw_stage.lower().replace("leadstatus.", "")
        # Map new DealStage enum values to display keys
        stage_map = {
            "new_lead": "lead", "qualified": "qualified", "contacted": "contacted",
            "meeting_booked": "meeting_scheduled", "proposal_sent": "proposal_sent",
            "negotiation": "negotiation", "won": "won", "lost": "lost",
            # legacy statuses
            "new": "lead", "quotation_requested": "proposal_sent",
            "quotation_sent": "proposal_sent", "unqualified": "lost",
        }
        stage = stage_map.get(stage, stage)

        priority = str(l.get("priority") or "low").replace("LeadPriority.", "").lower()
        source   = str(l.get("source")   or "manual").replace("LeadSource.", "").lower()
        icp_seg  = str(l.get("icp_segment") or "—").lower()

        by_stage[stage]    = by_stage.get(stage, 0) + 1
        by_priority[priority] = by_priority.get(priority, 0) + 1
        by_source[source]  = by_source.get(source, 0) + 1
        by_icp[icp_seg]    = by_icp.get(icp_seg, 0) + 1

        if l.get("score"):
            scores.append(int(l["score"]))

        monthly = float(l.get("expected_monthly_revenue") or l.get("budget_monthly") or 0)
        if monthly > 0:
            monthly_revenues.append(monthly)

        close_prob = float(l.get("expected_close_probability") or 0) or 0.10
        total_pipeline += monthly * 12 * close_prob
        total_actual   += float(l.get("actual_revenue") or 0)

        if l.get("response_time_minutes"):
            response_times.append(int(l["response_time_minutes"]))

        if stage == "won":               won_count += 1
        if stage == "lost":              lost_count += 1
        if stage == "meeting_scheduled": meeting_count += 1
        if stage == "proposal_sent":     proposal_count += 1
        if stage == "qualified":         qualified_count += 1

    avg_score    = round(sum(scores) / len(scores), 1) if scores else 0
    avg_monthly  = round(sum(monthly_revenues) / len(monthly_revenues)) if monthly_revenues else 0
    avg_deal     = avg_monthly * 12
    pipeline_v   = round(total_pipeline)
    rev_forecast = pipeline_v  # already weighted by close_probability

    qualified_total = sum(1 for l in leads if (l.get("score") or 0) >= 40)
    conversion_rate = round(won_count / qualified_total * 100, 1) if qualified_total else 0

    avg_response = round(sum(response_times) / len(response_times), 0) if response_times else None
    sla_ok   = sum(1 for t in response_times if t <= 15)
    sla_rate = round(sla_ok / len(response_times) * 100, 0) if response_times else None

    top_leads = sorted(
        [l for l in leads if (l.get("score") or 0) >= 40],
        key=lambda x: x.get("score", 0), reverse=True
    )[:8]

    today_messages = sorted(
        [l for l in leads
         if l.get("status") == "contacted"
         and l.get("updated_at", l.get("created_at", "")) >= today],
        key=lambda x: x.get("updated_at", x.get("created_at", "")), reverse=True
    )[:20]

    # Attribution (top sources by lead count)
    try:
        from processors.attribution import compute_attribution, attribution_to_dict
        attribution = attribution_to_dict(compute_attribution(leads))
    except Exception:
        attribution = {}

    return {
        "total": total, "today": today_leads, "week": week_leads, "month": month_leads,
        "qualified": qualified_count, "meetings": meeting_count,
        "proposals": proposal_count, "won": won_count, "lost": lost_count,
        "avg_score": avg_score, "avg_budget": avg_monthly, "avg_deal": avg_deal,
        "pipeline_value": pipeline_v, "revenue_forecast": rev_forecast,
        "actual_revenue": round(total_actual),
        "conversion_rate": conversion_rate,
        "by_stage": by_stage, "by_priority": by_priority, "by_source": by_source,
        "by_icp": by_icp,
        "avg_response_min": avg_response, "sla_rate": sla_rate,
        "top_leads": top_leads,
        "today_messages": today_messages,
        "attribution": attribution,
        "generated_at": now.isoformat(),
    }


def _render_today_messages(messages: list) -> str:
    if not messages:
        return ""
    rows = ""
    for i, l in enumerate(messages, 1):
        name = l.get("name") or "—"
        phone = l.get("phone") or "—"
        score = l.get("score") or 0
        msg = ""
        raw = l.get("raw_data") or {}
        if isinstance(raw, dict):
            msg = raw.get("draft_message", "")[:80] or raw.get("message", "")[:80]
        time_str = str(l.get("updated_at") or l.get("created_at") or "")
        # Convert UTC to Arabia time (+3)
        try:
            from datetime import timezone, timedelta as td
            dt = datetime.fromisoformat(time_str[:19])
            dt_ar = dt + td(hours=3)
            time_str = dt_ar.strftime("%H:%M")
        except Exception:
            time_str = time_str[11:16]
        rows += f"""
        <tr style="border-bottom:1px solid #f1f5f9">
          <td style="padding:8px 12px;font-size:13px;color:#64748b;width:40px">{i}</td>
          <td style="padding:8px 12px;font-weight:500">{name}</td>
          <td style="padding:8px 12px;color:#64748b;font-size:13px;direction:ltr">{phone}</td>
          <td style="padding:8px 12px;text-align:center">
            <span style="background:#3b82f6;color:white;padding:2px 8px;border-radius:10px;font-size:11px">{score}</span>
          </td>
          <td style="padding:8px 12px;font-size:12px;color:#475569;max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{msg}</td>
          <td style="padding:8px 12px;font-size:12px;color:#94a3b8;white-space:nowrap">{time_str}</td>
        </tr>"""

    return f"""
  <div class="card" style="margin-top:0">
    <h2>📨 رسائل واتساب المُرسلة اليوم ({len(messages)})</h2>
    <table>
      <thead><tr>
        <th style="width:40px">#</th>
        <th>اسم العميل</th>
        <th>رقم الهاتف</th>
        <th>Score</th>
        <th>مقتطف الرسالة</th>
        <th>الوقت</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>"""


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
    actual_rev = data.get("actual_revenue", 0)
    conv_rate  = data["conversion_rate"]
    by_stage   = data["by_stage"]
    by_source  = data["by_source"]
    by_icp     = data.get("by_icp", {})
    attribution = data.get("attribution", {})
    top_leads       = data["top_leads"]
    today_messages  = data.get("today_messages", [])
    avg_resp        = data["avg_response_min"]
    sla_rate        = data["sla_rate"]
    gen_at          = data["generated_at"][:19].replace("T", " ")

    PRIORITY_COLOR = {"high": "#ef4444", "medium": "#f59e0b", "low": "#22c55e"}
    SOURCE_AR = {"website": "الموقع", "whatsapp": "واتساب", "ads": "إعلانات",
                 "linkedin": "LinkedIn", "manual": "يدوي", "google_forms": "نموذج",
                 "telegram": "Telegram", "serpapi_prospecting": "خرائط جوجل"}
    ICP_AR = {
        "premium_fb":    "Premium F&B 🍫",
        "pharma_beauty": "Pharma & Beauty 💊",
        "fresh_food":    "Fresh Food 🥩",
        "horeca":        "HoReCa 🏨",
        "not_icp":       "خارج ICP",
        "—":             "غير محدد",
    }

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

    # ── ICP Distribution ──────────────────────────────────────────────────────
    max_icp = max(by_icp.values(), default=1) or 1
    icp_bars = ""
    ICP_COLORS = {
        "premium_fb": "#8b5cf6", "pharma_beauty": "#3b82f6",
        "fresh_food": "#22c55e", "horeca": "#f59e0b",
        "not_icp": "#94a3b8", "—": "#e2e8f0",
    }
    for seg, cnt in sorted(by_icp.items(), key=lambda x: -x[1]):
        pct = max(4, round(cnt / max_icp * 100))
        color = ICP_COLORS.get(seg, "#64748b")
        label = ICP_AR.get(seg, seg)
        icp_bars += f"""
        <div style="margin-bottom:10px">
          <div style="display:flex;justify-content:space-between;margin-bottom:3px">
            <span style="font-size:13px">{label}</span>
            <strong style="font-size:13px">{cnt}</strong>
          </div>
          <div style="background:#f1f5f9;border-radius:4px;height:8px">
            <div style="width:{pct}%;background:{color};height:8px;border-radius:4px"></div>
          </div>
        </div>"""

    # ── Attribution Table ──────────────────────────────────────────────────────
    attr_rows = ""
    for src in (attribution.get("by_source") or [])[:6]:
        attr_rows += f"""
        <tr style="border-bottom:1px solid #f1f5f9">
          <td style="padding:8px 12px;font-size:13px">{src.get('source_ar', src.get('source',''))}</td>
          <td style="padding:8px 12px;text-align:center;font-weight:600">{src.get('total_leads',0)}</td>
          <td style="padding:8px 12px;text-align:center;color:#22c55e;font-weight:600">{src.get('won_leads',0)}</td>
          <td style="padding:8px 12px;text-align:center">{src.get('win_rate_pct',0)}%</td>
          <td style="padding:8px 12px;text-align:center;color:#059669">{int(src.get('revenue_sar',0)):,}</td>
        </tr>"""
    if not attr_rows:
        attr_rows = '<tr><td colspan="5" style="text-align:center;padding:20px;color:#94a3b8">لا بيانات attribution بعد</td></tr>'

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

  <!-- ICP + Attribution -->
  <div class="grid">
    <div class="card">
      <h2>🎯 توزيع ICP — شرائح العميل المثالي</h2>
      {icp_bars or '<p style="color:#94a3b8">لا بيانات ICP بعد</p>'}
    </div>
    <div class="card">
      <h2>📊 Attribution — أداء المصادر</h2>
      <table>
        <thead><tr>
          <th>المصدر</th><th>Leads</th><th>Won</th><th>Win Rate</th><th>إيراد (ر)</th>
        </tr></thead>
        <tbody>{attr_rows}</tbody>
      </table>
    </div>
  </div>

  <!-- Revenue numbers -->
  <div class="grid">
    <div class="card" style="background:linear-gradient(135deg,#0f172a,#1e3a5f);color:white">
      <h2 style="color:rgba(255,255,255,.7);border-color:rgba(255,255,255,.1)">💵 ملخص الإيرادات</h2>
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px">
        <div style="text-align:center;padding:14px;background:rgba(255,255,255,.08);border-radius:8px">
          <div style="font-size:20px;font-weight:700">{pipeline_v:,}</div>
          <div style="font-size:11px;opacity:.7;margin-top:4px">Pipeline Value (ر)</div>
        </div>
        <div style="text-align:center;padding:14px;background:rgba(255,255,255,.08);border-radius:8px">
          <div style="font-size:20px;font-weight:700;color:#34d399">{rev_fore:,}</div>
          <div style="font-size:11px;opacity:.7;margin-top:4px">Forecast Revenue (ر)</div>
        </div>
        <div style="text-align:center;padding:14px;background:rgba(255,255,255,.08);border-radius:8px">
          <div style="font-size:20px;font-weight:700;color:#fbbf24">{actual_rev:,}</div>
          <div style="font-size:11px;opacity:.7;margin-top:4px">Actual Revenue (ر)</div>
        </div>
      </div>
    </div>
    <div class="card">
      <h2>📈 Win Rate & KPIs</h2>
      <table style="font-size:13px">
        <tr><td style="padding:6px 0;color:#64748b">Win Rate</td><td style="padding:6px 0;font-weight:600;text-align:left;color:#22c55e">{conv_rate}%</td></tr>
        <tr><td style="padding:6px 0;color:#64748b">Avg Deal Size (سنوي)</td><td style="padding:6px 0;font-weight:600;text-align:left">{avg_deal:,} ر</td></tr>
        <tr><td style="padding:6px 0;color:#64748b">Qualified Leads</td><td style="padding:6px 0;font-weight:600;text-align:left">{qualified}</td></tr>
        <tr><td style="padding:6px 0;color:#64748b">Won Deals</td><td style="padding:6px 0;font-weight:600;color:#16a34a;text-align:left">{won}</td></tr>
        <tr><td style="padding:6px 0;color:#64748b">Avg Score</td><td style="padding:6px 0;font-weight:600;text-align:left">{avg_score}/100</td></tr>
      </table>
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

  <!-- Today's WhatsApp Messages -->
  {_render_today_messages(today_messages)}

</div>
</body>
</html>"""
