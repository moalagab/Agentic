"""
WhatsApp Business Cloud API channel handler for Smartfield.
معالج قناة واتساب بيزنس للعملاء المحتملين

Parses incoming WhatsApp webhook payloads and extracts lead
information from conversational messages using Claude NLP.
"""

from __future__ import annotations

import re
from typing import Any, Optional

import structlog

from models.lead import LeadCreate, LeadSource

logger = structlog.get_logger(__name__)


# ── Escalation Detection ───────────────────────────────────────────────────────

# سؤال عن السعر — يرد تلقائياً ويُشعر المالك (بدون قفل الـ thread)
_PRICE_KEYWORDS = [
    # عربي
    "كم السعر", "كم التكلفة", "وش الأسعار", "بكم",
    "كم الاشتراك", "كم الرحلة", "كم تكلفة", "اسعار", "سعر",
    # إنجليزي
    "price", "pricing", "how much", "cost", "rate", "quote",
    "what's the price", "what is the price", "tariff",
]

# جاهز للإغلاق — ينتظر تدخّل المالك ويقفل الـ thread
_BUYING_SIGNAL_KEYWORDS = [
    # عربي
    "نبي نجرب", "نبغى نبدأ", "نحتاج توصيل",
    "ابي اتواصل", "نقدر نتفق", "متى تقدر", "وقت مناسب",
    "نبغى نتعاقد", "وش الخطوة",
    # إنجليزي
    "ready to start", "let's begin", "want to sign", "send contract",
    "when can you start", "i'm interested", "let's go",
]

_COMPLAINT_KEYWORDS = [
    # عربي
    "مشكلة", "تأخر", "تلف", "خسارة", "غلط",
    "مو زين", "للأسف", "مستاء", "مو راضي", "رفع شكوى",
    # إنجليزي
    "problem", "issue", "late", "damaged", "complaint",
    "not good", "unhappy", "disappointed",
]

# الرد التلقائي على سؤال السعر — عدّل الرقم في .env أو هنا
PRICE_AUTO_REPLY = (
    "أسعارنا تنافسية وتعتمد على نوع البضاعة والمسار والكميات. "
    "فريقنا سيتواصل معك خلال دقائق لتقديم عرض مخصص لاحتياجاتك. 🌡️"
)


def detect_escalation(text: str) -> Optional[str]:
    """
    Returns 'price_inquiry', 'buying_signal', 'complaint', or None.

    price_inquiry  → auto-reply with price + notify owner (no thread lock)
    buying_signal  → ack customer + notify owner + lock thread
    complaint      → ack customer + notify owner + lock thread
    """
    t = text.lower()
    for kw in _PRICE_KEYWORDS:
        if kw in t:
            return "price_inquiry"
    for kw in _BUYING_SIGNAL_KEYWORDS:
        if kw in t:
            return "buying_signal"
    for kw in _COMPLAINT_KEYWORDS:
        if kw in t:
            return "complaint"
    return None


def build_escalation_telegram_message(
    escalation_type: str,
    name: str,
    phone: str,
    last_message: str,
) -> str:
    """Format the Telegram alert sent to the owner on escalation."""
    preview = last_message[:200].replace("_", " ").replace("*", "")
    if escalation_type == "price_inquiry":
        return (
            f"💰 *سؤال عن السعر*\n"
            f"الاسم: {name}\n"
            f"الرسالة: _{preview}_\n"
            f"الهاتف: `{phone}`\n\n"
            f"تم الرد التلقائي — تابع الآن ✅"
        )
    if escalation_type == "buying_signal":
        return (
            f"🟢 *عميل جاهز للإغلاق*\n"
            f"الاسم: {name}\n"
            f"الرسالة: _{preview}_\n"
            f"الهاتف: `{phone}`\n\n"
            f"تدخّل الآن ✅"
        )
    return (
        f"🔴 *شكوى واردة*\n"
        f"الاسم: {name}\n"
        f"الرسالة: _{preview}_\n"
        f"الهاتف: `{phone}`\n\n"
        f"تدخّل فوراً ⚠️"
    )


def _extract_phone_from_message(wa_id: str) -> str:
    """Normalize WhatsApp phone number to E.164 format."""
    cleaned = re.sub(r"[^\d+]", "", wa_id)
    if not cleaned.startswith("+"):
        cleaned = f"+{cleaned}"
    return cleaned


def _extract_name_from_profile(profile: dict[str, Any]) -> str:
    """Extract sender name from WhatsApp profile data."""
    return profile.get("name", "WhatsApp Contact")


def _parse_lead_info_from_text(message_text: str, phone: str) -> dict[str, Any]:
    """
    Extract lead information from natural conversational WhatsApp text
    using regex patterns and keyword matching.

    In production, this can be augmented by calling Claude for NLP parsing.
    """
    text = message_text.lower()
    info: dict[str, Any] = {}

    # Budget extraction (SAR amounts)
    budget_patterns = [
        r"(\d[\d,\.]+)\s*(?:ريال|sar|sr|riyal)",
        r"ميزانية[^\d]*(\d[\d,\.]+)",
        r"budget[^\d]*(\d[\d,\.]+)",
    ]
    for pattern in budget_patterns:
        match = re.search(pattern, text)
        if match:
            try:
                budget_str = match.group(1).replace(",", "")
                info["budget_monthly"] = float(budget_str)
                break
            except ValueError:
                pass

    # Fleet size extraction
    fleet_patterns = [
        r"(\d+)\s*(?:شاحنة|شاحنات|truck|trucks)",
        r"(?:اريد|احتاج|need|want)\s*(\d+)",
        r"(\d+)\s*(?:عربية|سيارة)",
    ]
    for pattern in fleet_patterns:
        match = re.search(pattern, text)
        if match:
            try:
                info["fleet_size_needed"] = int(match.group(1))
                break
            except ValueError:
                pass

    # Cargo type detection from keywords
    cargo_keywords = {
        "دجاج": "دواجن - دجاج",
        "لحم": "لحوم حمراء",
        "سمك": "مأكولات بحرية",
        "خضار": "خضروات",
        "فاكهة": "فواكه",
        "ألبان": "منتجات الألبان",
        "مجمد": "منتجات مجمدة",
        "دواء": "أدوية",
        "صيدل": "منتجات صيدلانية",
        "طبي": "مستلزمات طبية",
        "chicken": "poultry",
        "meat": "red meat",
        "fish": "seafood",
        "pharma": "pharmaceuticals",
        "medicine": "medicines",
    }
    for keyword, cargo_type in cargo_keywords.items():
        if keyword in text:
            info["cargo_type"] = cargo_type
            break

    # Route extraction
    route_patterns = [
        r"من\s+([؀-ۿ\w]+)\s+(?:الى|إلى|ل|لـ)\s+([؀-ۿ\w]+)",
        r"from\s+(\w+)\s+to\s+(\w+)",
    ]
    for pattern in route_patterns:
        match = re.search(pattern, text)
        if match:
            info["route_from"] = match.group(1)
            info["route_to"] = match.group(2)
            break

    return info


class WhatsAppChannelHandler:
    """
    Parses WhatsApp Business Cloud API webhook payloads and converts
    conversational messages into structured LeadCreate objects.
    """

    def __init__(self) -> None:
        self._log = logger.bind(channel="WhatsApp")

    def parse_webhook(self, payload: dict[str, Any]) -> Optional[LeadCreate]:
        """
        Parse a WhatsApp Business webhook payload into a LeadCreate.
        يحلل حمولة webhook واتساب بيزنس ويحولها إلى LeadCreate.

        Handles the nested structure of WhatsApp Cloud API webhooks:
        payload → entry[] → changes[] → value → messages[]

        Returns None if no processable message is found.
        """
        try:
            entries = payload.get("entry", [])
            for entry in entries:
                for change in entry.get("changes", []):
                    value = change.get("value", {})
                    messages = value.get("messages", [])
                    contacts = value.get("contacts", [])

                    if not messages:
                        continue

                    for message in messages:
                        if message.get("type") != "text":
                            # Skip non-text messages (images, audio, etc.)
                            continue

                        text_body = message.get("text", {}).get("body", "")
                        if not text_body.strip():
                            continue

                        wa_id = message.get("from", "")
                        phone = _extract_phone_from_message(wa_id)

                        # Get name from contacts if available
                        name = "WhatsApp Contact"
                        if contacts:
                            profile = contacts[0].get("profile", {})
                            name = _extract_name_from_profile(profile)

                        # Extract structured info from message text
                        extracted = _parse_lead_info_from_text(text_body, phone)

                        self._log.info(
                            "WhatsApp message parsed",
                            phone=phone,
                            name=name,
                            text_length=len(text_body),
                            extracted_fields=list(extracted.keys()),
                        )

                        return LeadCreate(
                            name=name,
                            phone=phone,
                            email=extracted.get("email"),
                            source=LeadSource.WHATSAPP,
                            cargo_type=extracted.get("cargo_type"),
                            route_from=extracted.get("route_from"),
                            route_to=extracted.get("route_to"),
                            fleet_size_needed=extracted.get("fleet_size_needed"),
                            budget_monthly=extracted.get("budget_monthly"),
                            notes=text_body[:500],
                            raw_data={
                                "whatsapp_message_id": message.get("id"),
                                "whatsapp_timestamp": message.get("timestamp"),
                                "full_message_text": text_body,
                                "wa_id": wa_id,
                            },
                        )

        except Exception as exc:
            self._log.error("Failed to parse WhatsApp webhook", error=str(exc))

        return None

    def extract_raw_message(self, payload: dict[str, Any]) -> Optional[dict[str, Any]]:
        """
        Extract phone + text from a WhatsApp webhook without full lead parsing.
        Supports both Meta Cloud API format and WAHA (self-hosted) format.
        """
        try:
            # ── WAHA format ───────────────────────────────────────────────────
            # {"event": "message", "payload": {"from": "966...@c.us", "body": "..."}}
            if "payload" in payload and "event" in payload:
                msg = payload.get("payload", {})
                if msg.get("fromMe"):
                    return None  # ignore outgoing messages
                body = msg.get("body", "").strip()
                if not body or msg.get("type") not in ("chat", "text", None):
                    return None
                raw_from = msg.get("from", "")  # e.g. "966501234567@c.us" or "193406689145071@lid"
                # Ignore group chats — @g.us suffix = WhatsApp group
                if "@g.us" in raw_from:
                    self._log.debug("wa.group_message_ignored", chat=raw_from[:30])
                    return None
                # Keep raw_from as chatId for reply — strip @suffix for display phone
                chat_id = raw_from
                if raw_from.endswith("@lid"):
                    # @lid is WhatsApp's privacy-preserving linked ID, not a real
                    # phone number — the digits before "@lid" are an opaque internal
                    # ID (often 15+ digits), not E.164. Stripping the suffix and
                    # prepending "+" (old behavior) fabricated a fake phone number
                    # that looked valid but silently broke every follow-up send.
                    # Keep the full chatId instead — _normalize_chat_id() already
                    # passes any "@"-suffixed value straight through to WAHA.
                    phone = raw_from
                    self._log.warning("wa.lid_contact_no_real_phone", chat_id=raw_from[:30])
                else:
                    phone = raw_from.split("@")[0]
                    if not phone.startswith("+"):
                        phone = f"+{phone}"
                name = msg.get("notifyName") or msg.get("pushName") or "WhatsApp Contact"
                return {"phone": phone, "text": body, "name": name, "waha_chat_id": chat_id}

            # ── Meta Cloud API format ─────────────────────────────────────────
            for entry in payload.get("entry", []):
                for change in entry.get("changes", []):
                    value = change.get("value", {})
                    messages = value.get("messages", [])
                    contacts = value.get("contacts", [])
                    for message in messages:
                        if message.get("type") != "text":
                            continue
                        text = message.get("text", {}).get("body", "").strip()
                        if not text:
                            continue
                        wa_id = message.get("from", "")
                        phone = _extract_phone_from_message(wa_id)
                        name = "WhatsApp Contact"
                        if contacts:
                            name = _extract_name_from_profile(contacts[0].get("profile", {}))
                        return {"phone": phone, "text": text, "name": name}
        except Exception as exc:
            self._log.error("extract_raw_message failed", error=str(exc))
        return None

    def verify_webhook(self, mode: str, token: str, challenge: str, verify_token: str) -> Optional[str]:
        """
        Verify WhatsApp webhook subscription challenge.
        يتحقق من تحدي اشتراك webhook واتساب.

        Returns the challenge string if verification succeeds, None otherwise.
        """
        if mode == "subscribe" and token == verify_token:
            self._log.info("WhatsApp webhook verified")
            return challenge
        self._log.warning("WhatsApp webhook verification failed", mode=mode)
        return None

    def parse_status_update(self, payload: dict[str, Any]) -> Optional[dict[str, Any]]:
        """
        Parse message delivery/read status updates from WhatsApp webhook.
        يحلل تحديثات حالة تسليم الرسائل.
        """
        try:
            entries = payload.get("entry", [])
            for entry in entries:
                for change in entry.get("changes", []):
                    value = change.get("value", {})
                    statuses = value.get("statuses", [])
                    if statuses:
                        return statuses[0]  # Return first status update
        except Exception as exc:
            self._log.error("Failed to parse status update", error=str(exc))
        return None
