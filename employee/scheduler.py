"""
Background task scheduler for Smartfield autonomous agent.
المجدول التلقائي للمهام الدورية - كالموظف الذي يصحى كل صباح ويبدأ العمل

Uses APScheduler for recurring tasks:
- Daily report at 9 AM (Riyadh time)
- Morning prospecting at 8 AM (Claude searches for new leads)
- SLA breach check every 2 minutes
- Follow-up check every 2 hours
- Weekly report every Sunday at 10 AM
- End-of-day summary at 5:30 PM
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

if TYPE_CHECKING:
    from employee.autonomous_agent import AutonomousEmployee
    from processors.pipeline import LeadPipeline
    from processors.sla_monitor import SLAMonitor
    from processors.customer_success import CustomerSuccessEngine
    from processors.learning_loop import LearningLoop
    from processors.content_engine import ContentEngine

logger = structlog.get_logger(__name__)

# Riyadh timezone (UTC+3)
RIYADH_TZ = "Asia/Riyadh"


class SmartfieldScheduler:
    """
    Autonomous scheduler that drives the agent's proactive behaviors.
    Once started, the agent works 24/7 without human intervention.
    """

    def __init__(
        self,
        employee: "AutonomousEmployee",
        pipeline: "Optional[LeadPipeline]" = None,
        sla_monitor: "Optional[SLAMonitor]" = None,
        cs_engine: "Optional[CustomerSuccessEngine]" = None,
        learning_loop: "Optional[LearningLoop]" = None,
        content_engine: "Optional[ContentEngine]" = None,
    ):
        self.employee = employee
        self.pipeline = pipeline
        self.sla_monitor = sla_monitor
        self.cs_engine = cs_engine
        self.learning_loop = learning_loop
        self.content_engine = content_engine
        self.scheduler = AsyncIOScheduler(timezone=RIYADH_TZ)
        self._running = False

    def start(self):
        """Register all jobs and start the scheduler."""
        self._register_jobs()
        self.scheduler.start()
        self._running = True
        logger.info("scheduler.started", jobs=len(self.scheduler.get_jobs()))

    def stop(self):
        if self._running:
            self.scheduler.shutdown(wait=False)
            self._running = False
            logger.info("scheduler.stopped")

    def _register_jobs(self):
        # Morning prospecting — every day at 8:00 AM (before daily report)
        self.scheduler.add_job(
            self._run_morning_prospecting,
            CronTrigger(hour=8, minute=0, timezone=RIYADH_TZ),
            id="morning_prospecting",
            name="البحث الصباحي عن عملاء جدد",
            replace_existing=True,
            misfire_grace_time=600,
        )

        # Daily report - every day at 9:00 AM Riyadh time
        self.scheduler.add_job(
            self._run_daily_report,
            CronTrigger(hour=9, minute=0, timezone=RIYADH_TZ),
            id="daily_report",
            name="التقرير اليومي الصباحي",
            replace_existing=True,
            misfire_grace_time=300,
        )

        # Weekly report - every Sunday at 10:00 AM
        self.scheduler.add_job(
            self._run_weekly_report,
            CronTrigger(day_of_week="sun", hour=10, minute=0, timezone=RIYADH_TZ),
            id="weekly_report",
            name="التقرير الأسبوعي",
            replace_existing=True,
            misfire_grace_time=600,
        )

        # Follow-up check - every 2 hours
        self.scheduler.add_job(
            self._run_follow_ups,
            CronTrigger(minute=0, hour="*/2", timezone=RIYADH_TZ),
            id="follow_up_check",
            name="متابعة العملاء",
            replace_existing=True,
            misfire_grace_time=120,
        )

        # SLA breach scan — every 2 minutes
        self.scheduler.add_job(
            self._run_sla_check,
            CronTrigger(minute="*/2", timezone=RIYADH_TZ),
            id="sla_check",
            name="مراقبة SLA",
            replace_existing=True,
            misfire_grace_time=30,
        )

        # End-of-day summary - every day at 5:30 PM
        self.scheduler.add_job(
            self._run_eod_summary,
            CronTrigger(hour=17, minute=30, timezone=RIYADH_TZ),
            id="eod_summary",
            name="ملخص نهاية اليوم",
            replace_existing=True,
            misfire_grace_time=300,
        )

        # Customer Success daily check - every day at 10:00 AM
        self.scheduler.add_job(
            self._run_cs_check,
            CronTrigger(hour=10, minute=0, timezone=RIYADH_TZ),
            id="cs_daily_check",
            name="فحص نجاح العملاء اليومي",
            replace_existing=True,
            misfire_grace_time=300,
        )

        # Learning Loop weekly analysis - every Sunday at 8:00 AM
        self.scheduler.add_job(
            self._run_learning_loop,
            CronTrigger(day_of_week="sun", hour=8, minute=0, timezone=RIYADH_TZ),
            id="learning_loop",
            name="تحليل Win/Loss الأسبوعي",
            replace_existing=True,
            misfire_grace_time=600,
        )

        # Weekly content plan - every Saturday at 7:00 PM
        self.scheduler.add_job(
            self._run_content_plan,
            CronTrigger(day_of_week="sat", hour=19, minute=0, timezone=RIYADH_TZ),
            id="weekly_content",
            name="خطة المحتوى الأسبوعية",
            replace_existing=True,
            misfire_grace_time=600,
        )

        logger.info("scheduler.jobs_registered", count=9)

    async def _run_morning_prospecting(self):
        logger.info("scheduler.running_morning_prospecting")
        if not self.pipeline:
            logger.warning("scheduler.prospecting_skipped", reason="pipeline not set")
            return
        try:
            from processors.prospecting_engine import ProspectingEngine
            engine = ProspectingEngine(config=self.employee.config, pipeline=self.pipeline)
            results = await engine.run_morning_prospecting(target_total=104)
            logger.info(
                "scheduler.prospecting_complete",
                total=results.get("total_prospects", 0),
                added=results.get("added_to_crm", 0),
                segments=results.get("segments_covered", 0),
                errors=len(results.get("errors", [])),
            )
            # 1. Send summary report
            report = await engine.get_prospecting_report(results)
            if self.employee.telegram and self.employee._owner_telegram_ids:
                for chat_id in self.employee._owner_telegram_ids:
                    await self.employee.telegram.send_message(chat_id, report)
            else:
                for phone in self.employee._owner_phones:
                    await self.employee._send_whatsapp(phone, report)

            # 2. Send outreach briefing (top 10 with ready messages)
            top_prospects = results.get("top_prospects", [])
            if top_prospects:
                briefing = engine.build_outreach_briefing(top_prospects, top_n=10)
                if self.employee.telegram and self.employee._owner_telegram_ids:
                    for chat_id in self.employee._owner_telegram_ids:
                        await self.employee.telegram.send_message(chat_id, briefing)
                else:
                    for phone in self.employee._owner_phones:
                        await self.employee._send_whatsapp(phone, briefing)
        except Exception as exc:
            logger.error("scheduler.prospecting_error", error=str(exc))

    async def _run_sla_check(self):
        if not self.sla_monitor:
            return
        try:
            breached = await self.sla_monitor.check_pending_slas()
            if breached > 0:
                logger.warning("scheduler.sla_breaches_found", count=breached)
        except Exception as exc:
            logger.error("scheduler.sla_check_error", error=str(exc))

    async def _run_daily_report(self):
        logger.info("scheduler.running_daily_report")
        try:
            await self.employee.send_daily_report()
        except Exception as exc:
            logger.error("scheduler.daily_report_error", error=str(exc))

    async def _run_weekly_report(self):
        logger.info("scheduler.running_weekly_report")
        try:
            from employee.report_generator import build_weekly_report
            stats = await self.employee._get_pipeline_stats()
            report = build_weekly_report(stats)
            for phone in self.employee._owner_phones:
                await self.employee._send_whatsapp(phone, report)
        except Exception as exc:
            logger.error("scheduler.weekly_report_error", error=str(exc))

    async def _run_follow_ups(self):
        logger.info("scheduler.running_follow_ups")
        try:
            count = await self.employee.run_proactive_follow_ups()
            if count > 0:
                logger.info("scheduler.follow_ups_completed", count=count)
        except Exception as exc:
            logger.error("scheduler.follow_up_error", error=str(exc))

    async def _run_eod_summary(self):
        """Send a brief end-of-day summary to owners."""
        logger.info("scheduler.running_eod_summary")
        try:
            from employee.memory import get_today_actions
            actions = get_today_actions()
            if not actions:
                return

            summary = (
                f"*ملخص آخر اليوم - سمارت فيلد*\n"
                f"━━━━━━━━━━━━━\n"
                f"إجمالي الأنشطة اليوم: {len(actions)}\n"
                + "\n".join(f"  • {a['description']}" for a in actions[:5])
                + "\n\n_وكيل سمارت فيلد_"
            )
            for phone in self.employee._owner_phones:
                await self.employee._send_whatsapp(phone, summary)
        except Exception as exc:
            logger.error("scheduler.eod_summary_error", error=str(exc))

    async def _run_cs_check(self):
        """Run Customer Success daily check (Layer 8)."""
        logger.info("scheduler.running_cs_check")
        if not self.cs_engine:
            logger.debug("scheduler.cs_check_skipped", reason="cs_engine not set")
            return
        try:
            results = await self.cs_engine.run_daily_check()
            logger.info(
                "scheduler.cs_check_complete",
                renewals=results.get("renewals_alerted", 0),
                upsells=results.get("upsells_identified", 0),
                churn_risks=results.get("churn_risks", 0),
                referrals=results.get("referrals_requested", 0),
            )
        except Exception as exc:
            logger.error("scheduler.cs_check_error", error=str(exc))

    async def _run_learning_loop(self):
        """Run weekly Win/Loss learning analysis (Layer 10)."""
        logger.info("scheduler.running_learning_loop")
        if not self.learning_loop:
            logger.debug("scheduler.learning_loop_skipped", reason="learning_loop not set")
            return
        try:
            report = await self.learning_loop.run_weekly_analysis()
            logger.info(
                "scheduler.learning_loop_complete",
                win_rate=report.get("win_loss", {}).get("win_rate_pct", 0),
                won=report.get("win_loss", {}).get("won_count", 0),
                lost=report.get("win_loss", {}).get("lost_count", 0),
            )
        except Exception as exc:
            logger.error("scheduler.learning_loop_error", error=str(exc))

    async def _run_content_plan(self):
        """Generate weekly content plan and send to owner (Layer 7)."""
        logger.info("scheduler.running_content_plan")
        if not self.content_engine:
            logger.debug("scheduler.content_plan_skipped", reason="content_engine not set")
            return
        try:
            # Get pipeline stats for context
            pipeline_data = {}
            if self.pipeline:
                try:
                    pipeline_data = await self.pipeline.primary_crm.get_pipeline_stats()
                except Exception:
                    pass

            plan = await self.content_engine.generate_weekly_content_plan(pipeline_data=pipeline_data)

            # Format and send
            lines = ["*خطة المحتوى الأسبوعية — LinkedIn*", "━━━━━━━━━━━━━━"]
            for item in plan:
                lines.append(f"\n*{item['day']}* — {item['content_type']}")
                lines.append(f"الهدف: {item['revenue_goal']}")
                preview = item["post"][:120].replace("\n", " ")
                lines.append(f"_{preview}..._")

            msg = "\n".join(lines)

            if self.employee.telegram and self.employee._owner_telegram_ids:
                for chat_id in self.employee._owner_telegram_ids:
                    await self.employee.telegram.send_message(chat_id, msg)
            else:
                for phone in self.employee._owner_phones:
                    await self.employee._send_whatsapp(phone, msg)

            logger.info("scheduler.content_plan_sent", posts=len(plan))
        except Exception as exc:
            logger.error("scheduler.content_plan_error", error=str(exc))
