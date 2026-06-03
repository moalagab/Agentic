"""
Abstract base class for CRM integrations.
الفئة الأساسية المجردة لتكاملات نظام إدارة علاقات العملاء

All CRM adapters must implement these async methods.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from models.lead import Lead


class BaseCRM(ABC):
    """
    Abstract interface for CRM systems.
    All methods are async to support non-blocking I/O operations.
    """

    @abstractmethod
    async def create_lead(self, lead: Lead) -> str:
        """
        Create a new lead/contact record in the CRM.

        Args:
            lead: Fully populated Lead model instance.

        Returns:
            str: The CRM-assigned record ID for this lead.

        Raises:
            Exception: If creation fails for any reason (network, validation, etc.)
        """
        ...

    @abstractmethod
    async def update_lead(self, crm_id: str, data: dict) -> bool:
        """
        Update an existing lead record in the CRM.

        Args:
            crm_id: The CRM record ID returned by create_lead.
            data: Dictionary of fields to update (partial update / PATCH semantics).

        Returns:
            bool: True if the update succeeded, False otherwise.
        """
        ...

    @abstractmethod
    async def search_lead(
        self,
        email: Optional[str] = None,
        phone: Optional[str] = None,
    ) -> Optional[Lead]:
        """
        Search for an existing lead by email or phone number.
        Used for deduplication before creating a new record.

        Args:
            email: Email address to search for.
            phone: Phone number to search for.

        Returns:
            Lead | None: The existing lead if found, None otherwise.
        """
        ...

    @abstractmethod
    async def create_task(self, crm_id: str, task: dict) -> str:
        """
        Create a follow-up task associated with a lead in the CRM.

        Args:
            crm_id: The CRM record ID of the lead.
            task: Task data dictionary with keys:
                  - title (str): Task title
                  - type (str): 'call', 'email', 'meeting', etc.
                  - notes (str): Additional context for the sales rep
                  - due_hours (int): Hours from now when task is due
                  - priority (str): 'high', 'medium', or 'low'
                  - assigned_to (str | None): Assignee email

        Returns:
            str: The CRM-assigned task ID.
        """
        ...

    @abstractmethod
    async def get_pipeline_stats(self) -> dict:
        """
        Retrieve aggregated pipeline statistics from the CRM.

        Returns:
            dict: Statistics including counts by status, category, source,
                  average scores, and conversion rates.
        """
        ...
