"""
Attribution Engine
Tracks lead source → deal outcome to measure which channels generate revenue.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


SOURCE_LABELS_AR = {
    "linkedin":            "لينكدإن",
    "website":             "الموقع",
    "whatsapp":            "واتساب",
    "google_forms":        "استمارة جوجل",
    "ads":                 "إعلانات",
    "manual":              "يدوي",
    "serpapi_prospecting": "خرائط جوجل",
    "referral":            "إحالة",
    "unknown":             "غير معروف",
}


@dataclass
class SourceMetrics:
    source:        str
    source_ar:     str
    total_leads:   int   = 0
    won_leads:     int   = 0
    lost_leads:    int   = 0
    total_revenue: float = 0.0
    pipeline:      float = 0.0
    win_rate:      float = 0.0
    avg_deal:      float = 0.0


@dataclass
class AttributionReport:
    by_source:         dict[str, SourceMetrics] = field(default_factory=dict)
    best_source:       str = ""
    highest_revenue_source: str = ""
    total_leads:       int   = 0
    total_won:         int   = 0
    total_revenue:     float = 0.0


def compute_attribution(leads: list[dict[str, Any]]) -> AttributionReport:
    """
    Compute attribution metrics from a list of lead dicts (Supabase rows).
    Groups by `source` field and calculates win rate + revenue per source.
    """
    by_source: dict[str, SourceMetrics] = {}

    for lead in leads:
        src = (lead.get("source") or lead.get("attributed_source") or "unknown").lower()
        if src not in by_source:
            by_source[src] = SourceMetrics(
                source=src,
                source_ar=SOURCE_LABELS_AR.get(src, src),
            )

        m = by_source[src]
        m.total_leads += 1

        stage = (lead.get("deal_stage") or "").upper()
        status = (lead.get("status") or "").lower()

        is_won  = stage == "WON" or status == "won"
        is_lost = stage == "LOST" or status == "lost"

        if is_won:
            m.won_leads += 1
            rev = float(lead.get("actual_revenue") or lead.get("expected_monthly_revenue") or 0)
            m.total_revenue += rev * 12  # annualized
        elif is_lost:
            m.lost_leads += 1

        pipeline = float(lead.get("expected_monthly_revenue") or 0) * 12
        m.pipeline += pipeline

    # Calculate derived metrics
    for m in by_source.values():
        closed = m.won_leads + m.lost_leads
        m.win_rate = (m.won_leads / closed) if closed > 0 else 0.0
        m.avg_deal = (m.total_revenue / m.won_leads) if m.won_leads > 0 else 0.0

    report = AttributionReport(by_source=by_source)
    report.total_leads = sum(m.total_leads for m in by_source.values())
    report.total_won = sum(m.won_leads for m in by_source.values())
    report.total_revenue = sum(m.total_revenue for m in by_source.values())

    if by_source:
        report.best_source = max(by_source, key=lambda s: by_source[s].win_rate)
        report.highest_revenue_source = max(by_source, key=lambda s: by_source[s].total_revenue)

    return report


def attribution_to_dict(report: AttributionReport) -> dict[str, Any]:
    """Serialize AttributionReport for dashboard JSON."""
    sources = []
    for src, m in sorted(report.by_source.items(), key=lambda x: x[1].total_leads, reverse=True):
        sources.append({
            "source":        m.source,
            "source_ar":     m.source_ar,
            "total_leads":   m.total_leads,
            "won_leads":     m.won_leads,
            "lost_leads":    m.lost_leads,
            "win_rate_pct":  round(m.win_rate * 100, 1),
            "revenue_sar":   round(m.total_revenue, 2),
            "pipeline_sar":  round(m.pipeline, 2),
            "avg_deal_sar":  round(m.avg_deal, 2),
        })

    return {
        "by_source":               sources,
        "best_source":             SOURCE_LABELS_AR.get(report.best_source, report.best_source),
        "highest_revenue_source":  SOURCE_LABELS_AR.get(
            report.highest_revenue_source, report.highest_revenue_source
        ),
        "total_leads":             report.total_leads,
        "total_won":               report.total_won,
        "total_revenue_sar":       round(report.total_revenue, 2),
    }
