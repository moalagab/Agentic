"""
Background task scheduler for Smartfield autonomous agent.
المجدول التلقائي للمهام الدورية - كالموظف الذي يصحى كل صباح ويبدأ العمل

Uses APScheduler for recurring tasks:
- Daily report at 9 AM (Riyadh time)
- Follow-up check every 2 hours
- Weekly report every Sunday at 10 AM
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

if TYPE_CHECKING:
    from employee.autonomous_agent import AutonomousEmployee

logger = structlog.get_logger(__name__)

# Riyadh timezone (UTC+3)
RIYADH_TZ = "Asia/Riyadh"


class SmartfieldScheduler:
    """
    Autonomous scheduler that drives the agent's proactive behaviors.
    Once started, the agent works 24/7 without human intervention.
    """

    def __init__(self, employee: "AutonomousEmployee"):
        self.employee = employee
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

        # End-of-day summary - every day at 5:30 PM
        self.scheduler.add_job(
            self._run_eod_summary,
            CronTrigger(hour=17, minute=30, timezone=RIYADH_TZ),
            id="eod_summary",
            name="ملخص نهاية اليوم",
            replace_existing=True,
            misfire_grace_time=300,
        )

        logger.info("scheduler.jobs_registered", count=4)

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
                f"🌙 *ملخص آخر اليوم - سمارت فيلد*\n"
                f"━━━━━━━━━━━━━\n"
                f"إجمالي الأنشطة اليوم: {len(actions)}\n"
                + "\n".join(f"  • {a['description']}" for a in actions[:5])
                + "\n\n_وكيل سمارت فيلد_"
            )
            for phone in self.employee._owner_phones:
                await self.employee._send_whatsapp(phone, summary)
        except Exception as exc:
            logger.error("scheduler.eod_summary_error", error=str(exc))
