"""
Configuration management for the Smartfield Lead Generation System.
إعدادات نظام توليد العملاء المحتملين - سمارت فيلد
"""

from functools import lru_cache
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.
    إعدادات التطبيق المحملة من متغيرات البيئة.
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ─── Anthropic / Claude ───────────────────────────────────────────────────
    ANTHROPIC_API_KEY: str = Field(..., description="Anthropic API key for Claude")

    # ─── HubSpot CRM ─────────────────────────────────────────────────────────
    HUBSPOT_API_KEY: str = Field(default="", description="HubSpot private app API key")
    HUBSPOT_PORTAL_ID: str = Field(default="", description="HubSpot portal/account ID")

    # ─── Airtable ─────────────────────────────────────────────────────────────
    AIRTABLE_API_KEY: str = Field(default="", description="Airtable personal access token")
    AIRTABLE_BASE_ID: str = Field(default="", description="Airtable base ID (starts with app)")
    AIRTABLE_TABLE_NAME: str = Field(default="Leads", description="Airtable table name for leads")

    # ─── Twilio / WhatsApp Notifications ─────────────────────────────────────
    TWILIO_ACCOUNT_SID: str = Field(default="", description="Twilio account SID")
    TWILIO_AUTH_TOKEN: str = Field(default="", description="Twilio auth token")
    TWILIO_WHATSAPP_FROM: str = Field(
        default="whatsapp:+14155238886",
        description="Twilio WhatsApp sender number (whatsapp:+1...)"
    )
    TWILIO_FOLLOWUP_TEMPLATE_SID: str = Field(
        default="",
        description="Twilio Content SID for follow-up template (HXxxx...)"
    )

    # ─── WhatsApp Business Cloud API ─────────────────────────────────────────
    WHATSAPP_BUSINESS_TOKEN: str = Field(
        default="", description="WhatsApp Business Cloud API permanent token"
    )
    WHATSAPP_PHONE_ID: str = Field(
        default="", description="WhatsApp Business phone number ID"
    )
    WHATSAPP_VERIFY_TOKEN: str = Field(
        default="smartfield_verify_2024",
        description="Verification token for WhatsApp webhook setup"
    )

    # ─── LinkedIn ─────────────────────────────────────────────────────────────
    LINKEDIN_CLIENT_ID: str = Field(default="", description="LinkedIn OAuth app client ID")
    LINKEDIN_CLIENT_SECRET: str = Field(
        default="", description="LinkedIn OAuth app client secret"
    )

    # ─── CRM Selection ────────────────────────────────────────────────────────
    PRIMARY_CRM: str = Field(
        default="hubspot",
        description="Primary CRM to use: 'hubspot' or 'airtable'"
    )

    # ─── Telegram Notifications ───────────────────────────────────────────────
    TELEGRAM_BOT_TOKEN: str = Field(
        default="", description="Telegram Bot token from @BotFather"
    )
    TELEGRAM_OWNER_CHAT_IDS: list[str] = Field(
        default_factory=list,
        description="Telegram chat IDs for owners/sales team (get via @userinfobot)"
    )

    # ─── Sales Team Notifications ─────────────────────────────────────────────
    SALES_TEAM_WHATSAPP: list[str] = Field(
        default_factory=list,
        description="List of sales team WhatsApp numbers (e.g. +966501234567)"
    )

    # ─── Server ──────────────────────────────────────────────────────────────
    APP_HOST: str = Field(default="0.0.0.0", description="Server bind host")
    APP_PORT: int = Field(default=8000, description="Server bind port")
    LOG_LEVEL: str = Field(default="INFO", description="Logging level: DEBUG, INFO, WARNING, ERROR")

    # ─── Optional: Meta Ads webhook secret ───────────────────────────────────
    META_ADS_VERIFY_TOKEN: str = Field(
        default="smartfield_meta_2024",
        description="Verify token for Meta Ads webhook"
    )

    @field_validator("PRIMARY_CRM")
    @classmethod
    def validate_crm(cls, v: str) -> str:
        allowed = {"hubspot", "airtable"}
        if v.lower() not in allowed:
            raise ValueError(f"PRIMARY_CRM must be one of {allowed}, got '{v}'")
        return v.lower()

    @field_validator("LOG_LEVEL")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {allowed}")
        return upper

    @property
    def hubspot_base_url(self) -> str:
        return "https://api.hubapi.com"

    @property
    def airtable_base_url(self) -> str:
        return f"https://api.airtable.com/v0/{self.AIRTABLE_BASE_ID}"

    @property
    def whatsapp_api_url(self) -> str:
        return f"https://graph.facebook.com/v18.0/{self.WHATSAPP_PHONE_ID}/messages"

    def is_hubspot_configured(self) -> bool:
        return bool(self.HUBSPOT_API_KEY and self.HUBSPOT_PORTAL_ID)

    def is_airtable_configured(self) -> bool:
        return bool(self.AIRTABLE_API_KEY and self.AIRTABLE_BASE_ID)

    def is_twilio_configured(self) -> bool:
        return bool(self.TWILIO_ACCOUNT_SID and self.TWILIO_AUTH_TOKEN)

    def is_whatsapp_configured(self) -> bool:
        return bool(self.WHATSAPP_BUSINESS_TOKEN and self.WHATSAPP_PHONE_ID)

    def is_telegram_configured(self) -> bool:
        return bool(self.TELEGRAM_BOT_TOKEN and self.TELEGRAM_OWNER_CHAT_IDS)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Returns a cached singleton Settings instance."""
    return Settings()
