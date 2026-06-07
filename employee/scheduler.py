"""
Background task scheduler for Smartfield autonomous agent.
المجدول التلقائي للمهام الدورية - كالموظف الذي يصحى كل صباح ويبدأ العمل

Uses APScheduler for recurring tasks:
- Daily report at 9 AM (Riyadh time)
- Google Maps prospecting at 8:15 AM (real businesses via Outscraper)
- SLA breach check every 2 minutes
- Follow-up check every 2 hours
- Weekly report every Sunday at 10 AM
- End-of-day summary at 5:30 PM
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

if TYPE_CHECKING:
    from employee.autonomous_agent import AutonomousEmployee
    from processors.pipeline import LeadPipeline
    from processors.sla_monitor import SLAMonitor
    from processors.customer_success import CustomerSuccessEngine
    from processors.learning_loop import LearningLoop
    from processors.content_engine import ContentEngine
    from processors.outbound_sender import OutboundSender

logger = structlog.get_logger(__name__)

# Riyadh timezone (UTC+3)
RIYADH_TZ = "Asia/Riyadh"

# Tracks last successful content generation run (persists across service restarts)
_CONTENT_FLAG_FILE = Path(__file__).parent.parent / "data" / "sf_content_last_run"


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
        outbound_sender: "Optional[OutboundSender]" = None,
    ):
        self.employee = employee
        self.pipeline = pipeline
        self.sla_monitor = sla_monitor
        self.cs_engine = cs_engine
        self.learning_loop = learning_loop
        self.content_engine = content_engine
        self.outbound_sender = outbound_sender
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

        # Startup check — runs once 60s after boot; triggers content engine if >7 days since last run
        self.scheduler.add_job(
            self._startup_content_check,
            DateTrigger(run_date=datetime.now() + timedelta(seconds=60)),
            id="startup_content_check",
            name="فحص محتوى عند الإقلاع",
        )

        # Outbound sending — every day at 9:30 AM (after morning prospecting)
        self.scheduler.add_job(
            self._run_outbound_sender,
            CronTrigger(hour=9, minute=30, timezone=RIYADH_TZ),
            id="outbound_sender",
            name="إرسال رسائل الـ Outreach",
            replace_existing=True,
            misfire_grace_time=600,
        )

        # Google Maps prospecting — every day at 8:15 AM (real businesses with phone numbers)
        self.scheduler.add_job(
            self._run_google_maps_prospecting,
            CronTrigger(hour=8, minute=15, timezone=RIYADH_TZ),
            id="google_maps_prospecting",
            name="البحث عن عملاء حقيقيين — Google Maps",
            replace_existing=True,
            misfire_grace_time=600,
        )

        # SerpAPI prospecting — DISABLED (Outscraper أفضل في تغطية أرقام الهواتف)
        # self.scheduler.add_job(
        #     self._run_serpapi_prospecting,
        #     CronTrigger(hour=8, minute=45, timezone=RIYADH_TZ),
        #     id="serpapi_prospecting",
        #     name="البحث عن عملاء — SerpAPI",
        #     replace_existing=True,
        #     misfire_grace_time=600,
        # )

        logger.info("scheduler.jobs_registered", count=10)

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
            from employee.report_generator import build_weekly_report_from_supabase
            config = self.employee.config
            if getattr(config, "SUPABASE_URL", "") and getattr(config, "SUPABASE_KEY", ""):
                report = await build_weekly_report_from_supabase(
                    config.SUPABASE_URL, config.SUPABASE_KEY
                )
            else:
                from employee.report_generator import build_weekly_report
                stats = await self.employee._get_pipeline_stats()
                report = build_weekly_report(stats)

            # Send via Telegram first (preferred), then WhatsApp
            if self.employee.telegram and self.employee._owner_telegram_ids:
                for chat_id in self.employee._owner_telegram_ids:
                    await self.employee.telegram.send_message(chat_id, report)
                logger.info("scheduler.weekly_report_sent_telegram")
            else:
                for phone in self.employee._owner_phones:
                    await self.employee._send_whatsapp(phone, report)
                logger.info("scheduler.weekly_report_sent_whatsapp")
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

    async def _run_outbound_sender(self):
        """
        Send daily approval batch to Telegram owner.
        Owner taps ✅/❌ on each lead — WhatsApp is sent only after approval.
        """
        logger.info("scheduler.running_outbound_sender")
        if not self.outbound_sender:
            logger.debug("scheduler.outbound_sender_skipped", reason="outbound_sender not set")
            return
        try:
            results = await self.outbound_sender.send_daily_approval_batch()
            logger.info("scheduler.outbound_sender_complete", **results)
        except Exception as exc:
            logger.error("scheduler.outbound_sender_error", error=str(exc))

    async def _run_google_maps_prospecting(self):
        """
        يبحث عن أماكن تجارية حقيقية عبر Google Maps Places API،
        يصنّفها بـ Claude، ويرسل قائمة الموافقة للمالك.
        يعمل 8:15 ص يومياً.
        """
        logger.info("scheduler.running_google_maps_prospecting")

        config = self.employee.config
        if not getattr(config, "OUTSCRAPER_API_KEY", ""):
            logger.warning(
                "scheduler.google_maps_skipped",
                reason="OUTSCRAPER_API_KEY غير مضبوط في .env",
            )
            return

        if not self.pipeline:
            logger.warning("scheduler.google_maps_skipped", reason="pipeline not set")
            return

        crm = getattr(self.pipeline, "primary_crm", None)
        if not crm:
            logger.warning("scheduler.google_maps_skipped", reason="primary_crm not set")
            return

        try:
            from processors.google_maps_engine import run_prospecting_engine

            # دالة الإشعار — ترسل رسالة الموافقة اليومية للمالك
            async def notify(message: str):
                if self.employee.telegram and self.employee._owner_telegram_ids:
                    for chat_id in self.employee._owner_telegram_ids:
                        await self.employee.telegram.send_message(chat_id, message)
                else:
                    for phone in self.employee._owner_phones:
                        await self.employee._send_whatsapp(phone, message)

            results = await run_prospecting_engine(
                outscraper_api_key=config.OUTSCRAPER_API_KEY,
                crm=crm,
                notify_callback=notify,
                gemini_api_key=getattr(config, "GEMINI_API_KEY", ""),
            )

            logger.info(
                "scheduler.google_maps_prospecting_complete",
                new_leads=len(results),
            )

        except Exception as exc:
            logger.error("scheduler.google_maps_prospecting_error", error=str(exc))

    async def _run_serpapi_prospecting(self):
        """
        يبحث عبر SerpAPI كمصدر ثانٍ للتنقيب بجانب Outscraper.
        يعمل 8:45 ص يومياً.
        """
        logger.info("scheduler.running_serpapi_prospecting")

        config = self.employee.config
        if not getattr(config, "SERPAPI_KEY", ""):
            logger.debug("scheduler.serpapi_skipped", reason="SERPAPI_KEY not configured")
            return

        if not self.pipeline:
            logger.warning("scheduler.serpapi_skipped", reason="pipeline not set")
            return

        crm = getattr(self.pipeline, "primary_crm", None)
        if not crm:
            return

        try:
            from processors.serpapi_engine import run_serpapi_prospecting

            async def notify(message: str):
                if self.employee.telegram and self.employee._owner_telegram_ids:
                    for chat_id in self.employee._owner_telegram_ids:
                        await self.employee.telegram.send_message(chat_id, message)
                else:
                    for phone in self.employee._owner_phones:
                        await self.employee._send_whatsapp(phone, message)

            results = await run_serpapi_prospecting(
                api_key=config.SERPAPI_KEY,
                gemini_api_key=getattr(config, "GEMINI_API_KEY", ""),
                crm=crm,
                notify_callback=notify,
                max_queries=4,
            )
            logger.info("scheduler.serpapi_prospecting_complete", new_leads=len(results))

        except Exception as exc:
            logger.error("scheduler.serpapi_prospecting_error", error=str(exc))

    async def _run_content_plan(self):
        """
        Generate weekly social content (X + Instagram + LinkedIn) and upload to Buffer.
        Runs every Saturday 7:00 PM — owner gets Telegram notification when drafts are ready.
        """
        logger.info("scheduler.running_content_plan")
        if not self.content_engine:
            logger.debug("scheduler.content_plan_skipped", reason="content_engine not set")
            return
        try:
            config = self.employee.config
            pipeline_data = {}
            if self.pipeline:
                try:
                    pipeline_data = await self.pipeline.primary_crm.get_pipeline_stats()
                except Exception:
                    pass

            # Generate and upload to Buffer
            summary = await self.content_engine.generate_and_upload_weekly_content(
                buffer_token=getattr(config, "BUFFER_ACCESS_TOKEN", ""),
                x_channel_id=getattr(config, "BUFFER_X_CHANNEL_ID", ""),
                instagram_channel_id=getattr(config, "BUFFER_INSTAGRAM_CHANNEL_ID", ""),
                x_bearer_token=getattr(config, "X_BEARER_TOKEN", ""),
                pipeline_data=pipeline_data,
            )

            total = summary.get("x_posts", 0) + summary.get("ig_reels", 0) + summary.get("ig_posts", 0)
            uploaded = summary.get("buffer_uploads", 0)

            if uploaded > 0:
                msg = (
                    f"📅 *محتوى الأسبوع جاهز على Buffer*\n"
                    f"━━━━━━━━━━━━━━\n"
                    f"X: {summary.get('x_posts', 0)} تغريدة\n"
                    f"Instagram Reels: {summary.get('ig_reels', 0)}\n"
                    f"Instagram Posts: {summary.get('ig_posts', 0)}\n"
                    f"المرفوع على Buffer: {uploaded} قطعة\n"
                    f"_راجع Buffer وانشر بعد موافقتك_"
                )
            else:
                msg = (
                    f"📅 *محتوى الأسبوع جاهز (Buffer غير مضبوط)*\n"
                    f"━━━━━━━━━━━━━━\n"
                    f"تم توليد {total} قطعة محتوى\n"
                    f"X: {summary.get('x_posts', 0)} | IG: {summary.get('ig_reels', 0) + summary.get('ig_posts', 0)}\n"
                    f"_محفوظة في generated_content/ على السيرفر_"
                )

            if self.employee.telegram and self.employee._owner_telegram_ids:
                for chat_id in self.employee._owner_telegram_ids:
                    await self.employee.telegram.send_message(chat_id, msg)
            else:
                for phone in self.employee._owner_phones:
                    await self.employee._send_whatsapp(phone, msg)

            logger.info("scheduler.content_plan_complete", total_pieces=total, buffer_uploads=uploaded)
            # Write timestamp so startup check knows when we last ran
            try:
                _CONTENT_FLAG_FILE.parent.mkdir(parents=True, exist_ok=True)
                _CONTENT_FLAG_FILE.write_text(str(datetime.now().timestamp()))
            except Exception:
                pass
        except Exception as exc:
            logger.error("scheduler.content_plan_error", error=str(exc))

    async def _startup_content_check(self):
        """
        Runs once 60 seconds after startup.
        Triggers content plan immediately if last run was >7 days ago or never ran.
        """
        logger.info("scheduler.startup_content_check")
        if not self.content_engine:
            return

        should_run = True
        try:
            if _CONTENT_FLAG_FILE.exists():
                last_ts = float(_CONTENT_FLAG_FILE.read_text().strip())
                days_since = (datetime.now().timestamp() - last_ts) / 86400
                if days_since < 7:
                    should_run = False
                    logger.info(
                        "scheduler.startup_content_skip",
                        days_since_last_run=round(days_since, 1),
                    )
        except Exception:
            pass  # corrupt flag file → run anyway

        if should_run:
            logger.info("scheduler.startup_content_running", reason="no run in last 7 days")
            await self._run_content_plan()
