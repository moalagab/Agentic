"""
CRM integration package for Smartfield Lead Generation System.
حزمة تكامل نظام إدارة علاقات العملاء
"""

from crm.base import BaseCRM
from crm.hubspot import HubSpotCRM
from crm.airtable import AirtableCRM

__all__ = ["BaseCRM", "HubSpotCRM", "AirtableCRM"]
