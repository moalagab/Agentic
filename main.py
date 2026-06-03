"""
Smartfield Lead Generation Agent - Entry Point
نقطة الدخول الرئيسية لنظام توليد العملاء المحتملين - سمارت فيلد

Starts the FastAPI webhook server using uvicorn.
"""

from __future__ import annotations

import logging
import sys

import structlog
import uvicorn

from channels.webhook_server import app
from config import get_settings


def configure_logging(log_level: str) -> None:
    """
    Configure structlog for structured JSON logging.
    يضبط structlog للتسجيل المنظم بصيغة JSON.
    """
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer() if sys.stdout.isatty()
            else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Also configure stdlib logging to route through structlog
    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, log_level.upper(), logging.INFO),
        stream=sys.stdout,
    )


def main() -> None:
    """Start the Smartfield Lead Agent server."""
    settings = get_settings()

    configure_logging(settings.LOG_LEVEL)

    log = structlog.get_logger(__name__)
    log.info(
        "Starting Smartfield Lead Generation Agent",
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        log_level=settings.LOG_LEVEL,
        primary_crm=settings.PRIMARY_CRM,
    )

    uvicorn.run(
        app,
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        log_level=settings.LOG_LEVEL.lower(),
        access_log=True,
    )


if __name__ == "__main__":
    main()
