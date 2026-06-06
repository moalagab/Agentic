"""
Revenue Forecast Engine
Pipeline Value (SAR) × Expected Close (%) = Forecast (SAR)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from models.lead import DealStage, Lead


# Stage-based close probability multipliers (default if not set on lead)
STAGE_PROBABILITY: dict[str, float] = {
    DealStage.NEW_LEAD.value:       0.05,
    DealStage.QUALIFIED.value:      0.15,
    DealStage.CONTACTED.value:      0.25,
    DealStage.MEETING_BOOKED.value: 0.40,
    DealStage.PROPOSAL_SENT.value:  0.60,
    DealStage.NEGOTIATION.value:    0.80,
    DealStage.WON.value:            1.00,
    DealStage.LOST.value:           0.00,
}

# ICP boost — higher ICP score → higher win probability
def _icp_boost(icp_score: int) -> float:
    if icp_score >= 80:
        return 0.10
    elif icp_score >= 60:
        return 0.05
    return 0.0


@dataclass
class LeadForecast:
    lead_id:            str
    lead_name:          str
    deal_stage:         str
    pipeline_value:     float   # expected_monthly_revenue × 12 (annual)
    close_probability:  float   # 0-1
    forecast_value:     float   # pipeline_value × close_probability
    icp_score:          int
    icp_segment:        str


@dataclass
class PipelineSummary:
    total_pipeline:        float = 0.0
    weighted_forecast:     float = 0.0
    by_stage:              dict[str, float] = field(default_factory=dict)
    leads_by_stage:        dict[str, int]   = field(default_factory=dict)
    win_rate:              float = 0.0
    avg_deal_value:        float = 0.0
    total_actual_revenue:  float = 0.0
    lead_count:            int   = 0
    won_count:             int   = 0
    lost_count:            int   = 0
    forecasts:             list[LeadForecast] = field(default_factory=list)


def compute_lead_forecast(lead: Lead) -> LeadForecast:
    """Compute forecast for a single lead."""
    stage = lead.deal_stage or DealStage.NEW_LEAD.value
    stage_prob = STAGE_PROBABILITY.get(stage, 0.05)

    # Use lead's explicit probability if set, else fallback to stage default
    prob = lead.expected_close_probability if lead.expected_close_probability > 0 else stage_prob

    # Add ICP boost
    prob = min(1.0, prob + _icp_boost(lead.icp_score))

    # Pipeline value = monthly × 12 months
    monthly = lead.expected_monthly_revenue or 0.0
    pipeline = monthly * 12

    forecast = pipeline * prob

    return LeadForecast(
        lead_id=lead.id,
        lead_name=lead.name,
        deal_stage=stage,
        pipeline_value=pipeline,
        close_probability=prob,
        forecast_value=forecast,
        icp_score=lead.icp_score,
        icp_segment=lead.icp_segment or "—",
    )


def compute_pipeline_summary(leads: list[Lead]) -> PipelineSummary:
    """Aggregate pipeline metrics across all leads."""
    summary = PipelineSummary()
    summary.lead_count = len(leads)

    won_revenues: list[float] = []

    for lead in leads:
        fc = compute_lead_forecast(lead)
        summary.forecasts.append(fc)
        summary.total_pipeline += fc.pipeline_value
        summary.weighted_forecast += fc.forecast_value
        summary.total_actual_revenue += lead.actual_revenue or 0.0

        stage = fc.deal_stage
        summary.by_stage[stage] = summary.by_stage.get(stage, 0.0) + fc.pipeline_value
        summary.leads_by_stage[stage] = summary.leads_by_stage.get(stage, 0) + 1

        if stage == DealStage.WON.value:
            summary.won_count += 1
            won_revenues.append(lead.expected_monthly_revenue * 12)
        elif stage == DealStage.LOST.value:
            summary.lost_count += 1

    closed = summary.won_count + summary.lost_count
    summary.win_rate = (summary.won_count / closed) if closed > 0 else 0.0
    summary.avg_deal_value = (sum(won_revenues) / len(won_revenues)) if won_revenues else 0.0

    return summary


def forecast_to_dict(summary: PipelineSummary) -> dict[str, Any]:
    """Serialize PipelineSummary to JSON-friendly dict for dashboard."""
    return {
        "total_pipeline_sar":   round(summary.total_pipeline, 2),
        "weighted_forecast_sar": round(summary.weighted_forecast, 2),
        "actual_revenue_sar":   round(summary.total_actual_revenue, 2),
        "win_rate_pct":         round(summary.win_rate * 100, 1),
        "avg_deal_value_sar":   round(summary.avg_deal_value, 2),
        "lead_count":           summary.lead_count,
        "won_count":            summary.won_count,
        "lost_count":           summary.lost_count,
        "by_stage":             {k: round(v, 2) for k, v in summary.by_stage.items()},
        "leads_by_stage":       summary.leads_by_stage,
        "top_leads": [
            {
                "name":          fc.lead_name,
                "stage":         fc.deal_stage,
                "pipeline_sar":  round(fc.pipeline_value, 2),
                "probability":   round(fc.close_probability * 100, 1),
                "forecast_sar":  round(fc.forecast_value, 2),
                "icp_score":     fc.icp_score,
                "icp_segment":   fc.icp_segment,
            }
            for fc in sorted(summary.forecasts, key=lambda x: x.forecast_value, reverse=True)[:10]
        ],
    }
