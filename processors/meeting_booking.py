"""
Meeting Booking Module — نظام حجز الاجتماعات

Generates meeting booking links and manages meeting requests.
Integrates with:
  - Calendly (if configured)
  - Manual booking via WhatsApp confirmation flow
  - Google Calendar link generation (no API needed)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Optional
from urllib.parse import urlencode, quote

import structlog

logger = structlog.get_logger(__name__)

# Available time slots (can be extended via config)
DEFAULT_SLOTS = [
    "الأحد 10:00 صباحاً",
    "الأحد 2:00 مساءً",
    "الاثنين 10:00 صباحاً",
    "الاثنين 2:00 مساءً",
    "الثلاثاء 11:00 صباحاً",
    "الأربعاء 10:00 صباحاً",
    "الخميس 11:00 صباحاً",
]

MEETING_TYPES = {
    "discovery":  "اجتماع تعرّف - 30 دقيقة",
    "demo":       "عرض تجريبي للخدمة - 45 دقيقة",
    "proposal":   "مناقشة العرض التجاري - 60 دقيقة",
    "followup":   "متابعة ومراجعة - 30 دقيقة",
}


def generate_google_calendar_link(
    title: str,
    description: str,
    start_dt: datetime,
    duration_minutes: int = 45,
    location: str = "اجتماع عبر الإنترنت",
) -> str:
    """
    Generate a Google Calendar event creation link.
    ينشئ رابط إضافة حدث في Google Calendar بدون API.
    """
    end_dt = start_dt + timedelta(minutes=duration_minutes)
    fmt = "%Y%m%dT%H%M%S"

    params = {
        "action": "TEMPLATE",
        "text": title,
        "details": description,
        "location": location,
        "dates": f"{start_dt.strftime(fmt)}/{end_dt.strftime(fmt)}",
    }
    base_url = "https://calendar.google.com/calendar/render?"
    return base_url + urlencode(params)


def build_booking_whatsapp_message(lead_name: str, lead_phone: str) -> str:
    """
    Build a WhatsApp message to initiate meeting booking.
    يبني رسالة واتساب لبدء عملية حجز اجتماع.
    """
    slots_text = "\n".join(f"  {i+1}. {slot}" for i, slot in enumerate(DEFAULT_SLOTS[:5]))

    return f"""مرحباً {lead_name} 👋

شكراً لاهتمامك بخدمات Smart Field للنقل المبرد ❄️

نودّ ترتيب اجتماع قصير معك للتعرف على احتياجاتك وتقديم الحل الأمثل.

📅 *المواعيد المتاحة:*
{slots_text}

ردّ برقم الموعد المناسب لك، أو اقترح وقتاً آخر 🗓️

مدة الاجتماع: 30-45 دقيقة عبر Zoom أو مكالمة هاتفية.

فريق Smart Field ❄️
""".strip()


def build_meeting_confirmation_message(
    lead_name: str,
    slot: str,
    meeting_type: str = "discovery",
    zoom_link: Optional[str] = None,
) -> str:
    """
    Build a confirmation message after booking is done.
    يبني رسالة تأكيد بعد تأكيد الاجتماع.
    """
    type_label = MEETING_TYPES.get(meeting_type, "اجتماع")
    zoom_section = f"\n🔗 رابط الاجتماع: {zoom_link}" if zoom_link else "\nسنرسل لك رابط الاجتماع قبل الموعد بساعة."

    return f"""✅ تم تأكيد الاجتماع!

عزيزي/عزيزتي {lead_name}،

تم حجز موعدكم بنجاح:

📅 الموعد: {slot}
📋 النوع: {type_label}
{zoom_section}

سيتواصل معكم أحد مختصي المبيعات قريباً للتأكيد.

💡 نصيحة: احضر بيانات متطلباتك (عدد الشاحنات، المسارات، نوع البضاعة) لاستفادة أكبر.

شكراً لثقتكم ❤️
فريق Smart Field
""".strip()


class MeetingBookingManager:
    """
    Manages the meeting booking flow via WhatsApp.
    يدير عملية حجز الاجتماعات عبر واتساب.
    """

    def __init__(self, config: Any, notifier: Any) -> None:
        self.config = config
        self.notifier = notifier
        self._log = logger.bind(component="MeetingBooking")
        # In-memory pending bookings {phone: {"step": ..., "lead_name": ...}}
        self._pending: dict[str, dict] = {}

    async def initiate_booking(self, lead_phone: str, lead_name: str) -> bool:
        """
        Send the booking invitation to a lead via WhatsApp.
        يرسل دعوة حجز للعميل عبر واتساب.
        """
        message = build_booking_whatsapp_message(lead_name, lead_phone)
        self._pending[lead_phone] = {
            "step": "awaiting_slot",
            "lead_name": lead_name,
            "initiated_at": datetime.utcnow().isoformat(),
        }

        try:
            await self.notifier.send_custom_message(lead_phone, message)
            self._log.info("Booking invitation sent", phone=lead_phone, name=lead_name)
            return True
        except Exception as exc:
            self._log.error("Failed to send booking invitation", error=str(exc))
            return False

    async def handle_reply(self, lead_phone: str, message_text: str) -> Optional[str]:
        """
        Process a lead's reply to a booking invitation.
        يعالج رد العميل على دعوة الاجتماع.

        Returns a confirmation message to send back, or None if not a booking reply.
        """
        pending = self._pending.get(lead_phone)
        if not pending or pending.get("step") != "awaiting_slot":
            return None

        # Try to match slot number
        text = message_text.strip()
        slot_index = None
        for i in range(1, len(DEFAULT_SLOTS) + 1):
            if str(i) in text:
                slot_index = i - 1
                break

        if slot_index is not None and slot_index < len(DEFAULT_SLOTS):
            chosen_slot = DEFAULT_SLOTS[slot_index]
            lead_name = pending["lead_name"]

            # Mark as confirmed
            self._pending.pop(lead_phone, None)

            confirmation = build_meeting_confirmation_message(
                lead_name=lead_name,
                slot=chosen_slot,
            )
            self._log.info("Meeting booked", phone=lead_phone, slot=chosen_slot)
            return confirmation

        # Free-text slot suggestion
        if len(text) > 5:
            lead_name = pending["lead_name"]
            self._pending.pop(lead_phone, None)
            return build_meeting_confirmation_message(
                lead_name=lead_name,
                slot=text,
            )

        return None

    def get_available_slots(self) -> list[str]:
        return DEFAULT_SLOTS.copy()

    def get_pending_bookings(self) -> dict:
        return dict(self._pending)
