"""
Channel handlers package for Smartfield Lead Generation System.
حزمة معالجات قنوات الاستقبال

Each module handles webhook payloads from a specific source
and normalizes them into LeadCreate objects.
"""

from channels.whatsapp import WhatsAppChannelHandler
from channels.linkedin import LinkedInChannelHandler
from channels.google_forms import GoogleFormsHandler
from channels.website import WebsiteChannelHandler

__all__ = [
    "WhatsAppChannelHandler",
    "LinkedInChannelHandler",
    "GoogleFormsHandler",
    "WebsiteChannelHandler",
]
