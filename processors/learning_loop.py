"""
Learning Loop — حلقة التعلم والتحسين (Layer 10 - RevOS v6)

يحلّل الصفقات المُغلقة (مكسوبة ومخسورة) ويولّد:
- تقرير Win/Loss مفصّل
- تحديثات ICP (Ideal Customer Profile)
- توصيات تحسين الرسائل
- اقتراحات تعديل التسعير
- تحليل القنوات الأعلى تحويلاً

يعمل أسبوعياً ويُرسل التقرير لصاحب العمل.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)


class LearningLoop:
    """
    Closed-loop learning system that improves the revenue engine over time.
    حلقة تعلم مغلقة تُحسّن منظومة الإيرادات باستمرار.
    """

    def __init__(
        self,
        supabase_client: Any = None,
        anthropic_api_key: Optional[str] = None,
        notifier: Any = None,
    ) -> None:
        self.supabase = supabase_client
        self.api_key = anthropic_api_key
        self.notifier = notifier
        self._log = logger.bind(component="LearningLoop")

    async def run_weekly_analysis(self) -> dict:
        """
        Main weekly analysis job.
        يُشغَّل أسبوعياً لتحليل أداء المبيعات وتوليد توصيات.
        """
        self._log.info("learning_loop.weekly_analysis_started")

        # Fetch data
        won_deals = await self._fetch_leads_by_status("won")
        lost_deals = await self._fetch_leads_by_status("lost")
        all_recent = await self._fetch_recent_leads(days=30)

        # Run analyses
        win_loss = self._analyze_win_loss(won_deals, lost_deals)
        icp_insights = self._analyze_icp(won_deals)
        channel_performance = self._analyze_channels(all_recent, won_deals)
        scoring_insights = self._analyze_scoring_accuracy(all_recent, won_deals)
        pipeline_velocity = self._analyze_pipeline_velocity(won_deals)

        report = {
            "period": {
                "from": (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d"),
                "to": datetime.utcnow().strftime("%Y-%m-%d"),
            },
            "win_loss": win_loss,
            "icp_insights": icp_insights,
            "channel_performance": channel_performance,
            "scoring_insights": scoring_insights,
            "pipeline_velocity": pipeline_velocity,
            "generated_at": datetime.utcnow().isoformat(),
        }

        # Generate AI narrative
        if self.api_key:
            report["narrative_ar"] = await self._generate_narrative(report)

        # Send report
        formatted = self._format_report_ar(report)
        await self._send_report(formatted)

        self._log.info(
            "learning_loop.weekly_analysis_complete",
            won=win_loss.get("won_count", 0),
            lost=win_loss.get("lost_count", 0),
            win_rate=win_loss.get("win_rate_pct", 0),
        )

        return report

    def _analyze_win_loss(self, won: list[dict], lost: list[dict]) -> dict:
        """Analyze win/loss patterns."""
        total = len(won) + len(lost)
        win_rate = round(len(won) / total * 100, 1) if total > 0 else 0

        # Category breakdown
        won_categories = Counter(d.get("category", "other") for d in won)
        lost_categories = Counter(d.get("category", "other") for d in lost)

        # Score distribution
        won_scores = [int(d.get("score") or 0) for d in won]
        lost_scores = [int(d.get("score") or 0) for d in lost]
        avg_won_score = round(sum(won_scores) / len(won_scores), 1) if won_scores else 0
        avg_lost_score = round(sum(lost_scores) / len(lost_scores), 1) if lost_scores else 0

        # Revenue impact
        won_revenue = sum(float(d.get("expected_monthly_revenue") or 0) for d in won)
        lost_revenue = sum(float(d.get("expected_monthly_revenue") or 0) for d in lost)

        return {
            "won_count": len(won),
            "lost_count": len(lost),
            "total": total,
            "win_rate_pct": win_rate,
            "won_categories": dict(won_categories.most_common(5)),
            "lost_categories": dict(lost_categories.most_common(5)),
            "avg_won_score": avg_won_score,
            "avg_lost_score": avg_lost_score,
            "won_monthly_revenue_sar": round(won_revenue, 0),
            "lost_opportunity_sar": round(lost_revenue, 0),
        }

    def _analyze_icp(self, won_deals: list[dict]) -> dict:
        """Derive ICP (Ideal Customer Profile) from won deals."""
        if not won_deals:
            return {"message": "لا توجد صفقات مُغلقة كافية للتحليل"}

        categories = Counter(d.get("category", "other") for d in won_deals)
        sources = Counter(d.get("source", "manual") for d in won_deals)
        priorities = Counter(d.get("priority", "medium") for d in won_deals)

        # Fleet size distribution
        fleet_sizes = [int(d.get("fleet_size_needed") or 0) for d in won_deals if d.get("fleet_size_needed")]
        avg_fleet = round(sum(fleet_sizes) / len(fleet_sizes), 1) if fleet_sizes else 0

        # Budget distribution
        budgets = [float(d.get("budget_monthly") or 0) for d in won_deals if d.get("budget_monthly")]
        avg_budget = round(sum(budgets) / len(budgets), 0) if budgets else 0

        # Revenue scores
        revenues = [float(d.get("expected_monthly_revenue") or 0) for d in won_deals]
        avg_revenue = round(sum(revenues) / len(revenues), 0) if revenues else 0

        top_category = categories.most_common(1)[0][0] if categories else "other"
        top_source = sources.most_common(1)[0][0] if sources else "manual"

        return {
            "top_winning_category": top_category,
            "top_winning_source": top_source,
            "avg_fleet_size": avg_fleet,
            "avg_budget_sar": avg_budget,
            "avg_revenue_sar": avg_revenue,
            "category_breakdown": dict(categories.most_common()),
            "source_breakdown": dict(sources.most_common()),
            "icp_description": (
                f"العميل المثالي: {top_category} | "
                f"مصدره الأفضل: {top_source} | "
                f"أسطول متوسط: {avg_fleet} شاحنة | "
                f"ميزانية: {avg_budget:,.0f} ريال"
            ),
        }

    def _analyze_channels(self, all_leads: list[dict], won_deals: list[dict]) -> dict:
        """Analyze which channels produce the highest quality leads."""
        won_ids = {d.get("id") for d in won_deals}

        channel_total: Counter = Counter()
        channel_won: Counter = Counter()

        for lead in all_leads:
            source = lead.get("source", "manual")
            channel_total[source] += 1
            if lead.get("id") in won_ids:
                channel_won[source] += 1

        channel_rates = {}
        for source, total in channel_total.items():
            won_count = channel_won.get(source, 0)
            rate = round(won_count / total * 100, 1) if total > 0 else 0
            channel_rates[source] = {
                "total_leads": total,
                "won": won_count,
                "conversion_rate_pct": rate,
            }

        # Sort by conversion rate
        best_channel = max(channel_rates.items(), key=lambda x: x[1]["conversion_rate_pct"], default=("—", {}))

        return {
            "by_channel": channel_rates,
            "best_channel": best_channel[0],
            "best_channel_rate": best_channel[1].get("conversion_rate_pct", 0),
        }

    def _analyze_scoring_accuracy(self, all_leads: list[dict], won_deals: list[dict]) -> dict:
        """Check if our scoring model is accurate."""
        won_ids = {d.get("id") for d in won_deals}

        high_priority_won = 0
        high_priority_lost = 0
        low_priority_won = 0

        for lead in all_leads:
            priority = lead.get("priority", "medium")
            is_won = lead.get("id") in won_ids

            if priority == "high":
                if is_won:
                    high_priority_won += 1
                else:
                    high_priority_lost += 1
            elif priority == "low" and is_won:
                low_priority_won += 1

        total_high = high_priority_won + high_priority_lost
        accuracy = round(high_priority_won / total_high * 100, 1) if total_high > 0 else 0

        recommendation = "النموذج دقيق" if accuracy >= 60 else "يُنصح بمراجعة معايير التقييم — كثير من العالي الأولوية لم يُغلق"

        return {
            "high_priority_won": high_priority_won,
            "high_priority_lost": high_priority_lost,
            "low_priority_won": low_priority_won,
            "scoring_accuracy_pct": accuracy,
            "recommendation": recommendation,
        }

    def _analyze_pipeline_velocity(self, won_deals: list[dict]) -> dict:
        """Calculate average time from lead creation to close."""
        durations = []
        for deal in won_deals:
            created = self._parse_date(deal.get("created_at"))
            won_date = self._parse_date(deal.get("won_date"))
            if created and won_date:
                days = (won_date - created).days
                if 0 < days < 365:
                    durations.append(days)

        if not durations:
            return {"avg_days_to_close": None, "message": "بيانات غير كافية"}

        avg_days = round(sum(durations) / len(durations), 1)
        min_days = min(durations)
        max_days = max(durations)

        return {
            "avg_days_to_close": avg_days,
            "min_days": min_days,
            "max_days": max_days,
            "sample_size": len(durations),
            "velocity_grade": "ممتاز" if avg_days <= 7 else "جيد" if avg_days <= 14 else "بطيء — يحتاج تحسين",
        }

    async def _generate_narrative(self, report: dict) -> str:
        """Use Claude to write an Arabic narrative summary of the analysis."""
        import anthropic

        wl = report.get("win_loss", {})
        icp = report.get("icp_insights", {})
        velocity = report.get("pipeline_velocity", {})

        prompt = f"""بناءً على تحليل أداء المبيعات التالي، اكتب تقرير تنفيذي موجز (150-200 كلمة) بالعربية:

معدل الإغلاق: {wl.get('win_rate_pct', 0)}%
صفقات مكتسبة: {wl.get('won_count', 0)} | مفقودة: {wl.get('lost_count', 0)}
إيراد محقق: {wl.get('won_monthly_revenue_sar', 0):,.0f} ريال/شهر
فرص ضائعة: {wl.get('lost_opportunity_sar', 0):,.0f} ريال/شهر
القطاع الأفضل: {icp.get('top_winning_category', '—')}
متوسط أيام الإغلاق: {velocity.get('avg_days_to_close', '—')} يوم

اذكر:
1. أبرز نقطتين إيجابيتين
2. أكبر فرصة للتحسين
3. توصية واحدة قابلة للتنفيذ الأسبوع القادم"""

        client = anthropic.AsyncAnthropic(api_key=self.api_key)
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()

    def _format_report_ar(self, report: dict) -> str:
        """Format the full report as a readable Arabic message."""
        wl = report.get("win_loss", {})
        icp = report.get("icp_insights", {})
        ch = report.get("channel_performance", {})
        sc = report.get("scoring_insights", {})
        vel = report.get("pipeline_velocity", {})

        lines = [
            f"*تقرير التحليل الأسبوعي — سمارت فيلد*",
            f"الفترة: {report['period']['from']} → {report['period']['to']}",
            f"━━━━━━━━━━━━━━",
            f"",
            f"*Win/Loss*",
            f"  • معدل الإغلاق: {wl.get('win_rate_pct', 0)}%",
            f"  • مكتسبة: {wl.get('won_count', 0)} | مفقودة: {wl.get('lost_count', 0)}",
            f"  • إيراد محقق: {wl.get('won_monthly_revenue_sar', 0):,.0f} ريال/شهر",
            f"  • فرص ضائعة: {wl.get('lost_opportunity_sar', 0):,.0f} ريال/شهر",
            f"",
            f"*ملف العميل المثالي (ICP)*",
            f"  {icp.get('icp_description', '—')}",
            f"",
            f"*أفضل قناة تحويل*",
            f"  {ch.get('best_channel', '—')} — {ch.get('best_channel_rate', 0)}% تحويل",
            f"",
            f"*دقة التقييم*",
            f"  {sc.get('scoring_accuracy_pct', 0)}% — {sc.get('recommendation', '—')}",
            f"",
            f"*سرعة خط المبيعات*",
            f"  متوسط الإغلاق: {vel.get('avg_days_to_close', '—')} يوم — {vel.get('velocity_grade', '—')}",
        ]

        if report.get("narrative_ar"):
            lines += ["", "*التحليل التنفيذي*", report["narrative_ar"]]

        return "\n".join(lines)

    async def _send_report(self, report_text: str) -> None:
        if not self.notifier:
            return
        try:
            if hasattr(self.notifier, "send_message"):
                for chat_id in getattr(self.notifier, "_owner_chat_ids", []):
                    await self.notifier.send_message(chat_id, report_text)
            elif hasattr(self.notifier, "send_custom_message"):
                for phone in getattr(self.notifier, "_sales_phones", []):
                    await self.notifier.send_custom_message(phone, report_text)
        except Exception as exc:
            self._log.warning("learning_loop.send_failed", error=str(exc))

    async def _fetch_leads_by_status(self, status: str) -> list[dict]:
        if not self.supabase:
            return []
        try:
            cutoff = (datetime.utcnow() - timedelta(days=30)).isoformat()
            result = self.supabase.table("leads").select("*").eq(
                "status", status
            ).gte("created_at", cutoff).execute()
            return result.data or []
        except Exception as exc:
            self._log.error("learning_loop.fetch_failed", status=status, error=str(exc))
            return []

    async def _fetch_recent_leads(self, days: int = 30) -> list[dict]:
        if not self.supabase:
            return []
        try:
            cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
            result = self.supabase.table("leads").select("*").gte(
                "created_at", cutoff
            ).execute()
            return result.data or []
        except Exception as exc:
            self._log.error("learning_loop.fetch_recent_failed", error=str(exc))
            return []

    def _parse_date(self, value: Any) -> Optional[datetime]:
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value).replace("Z", "").replace("+00:00", ""))
        except Exception:
            return None
