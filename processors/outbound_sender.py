"""
Outbound Sender — محرك إرسال الـ Outreach بنظام الموافقة
Layer 2 of RevOS v6

التدفق:
1. 9:30 ص — يجلب العملاء المعلّقين ويرسل كارت موافقة لكل عميل عبر Telegram
2. المالك يضغط ✅ موافق / ❌ رفض على كل كارت
3. عند الموافقة → يُرسل WhatsApp فوراً (حتى الحد اليومي 10 رسائل)
4. approval_status: PENDING → BATCH_SENT → APPROVED/REJECTED → SENT
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, date
from typing import TYPE_CHECKING, Optional

import structlog

from agent.cold_outreach import build_outreach_for_prospect
from processors.ab_test_engine import ABTestEngine, pick_variant, render_variant, VARIANTS

if TYPE_CHECKING:
    from crm.supabase_crm import SupabaseCRM
    from notifications.whatsapp import WhatsAppNotifier
    from channels.telegram import TelegramHandler

logger = structlog.get_logger(__name__)

_APPROVAL_STATUSES = ("PENDING", "BATCH_SENT", "APPROVED", "REJECTED", "SENT")


class OutboundSender:
    """
    Sends personalized WhatsApp outreach only after owner approval via Telegram.
    Daily cap: 10 messages. Approval gate prevents unsupervised outreach.
    """

    def __init__(
        self,
        crm: "SupabaseCRM",
        notifier: "WhatsAppNotifier",
        anthropic_api_key: str,
        telegram: Optional["TelegramHandler"] = None,
        owner_chat_ids: Optional[list[str]] = None,
        daily_cap: int = 10,
    ) -> None:
        self.crm = crm
        self.notifier = notifier
        self.api_key = anthropic_api_key
        self.telegram = telegram
        self.owner_chat_ids = owner_chat_ids or []
        self.daily_cap = daily_cap
        self.ab_engine = ABTestEngine(crm.client)
        self._log = logger.bind(component="OutboundSender")

    # ── Main daily job (called at 9:30 AM) ────────────────────────────────────

    async def send_daily_approval_batch(self) -> dict:
        """
        Fetch pending leads and send approval cards to Telegram.
        Called by scheduler at 9:30 AM.
        """
        if not self.telegram or not self.owner_chat_ids:
            self._log.warning("outbound.no_telegram_configured")
            return {"status": "no_telegram", "sent_to_telegram": 0}

        # لا ترسل دفعة جديدة إذا لا تزال هناك بطاقات بانتظار ردك
        awaiting = await self._count_awaiting_approval()
        if awaiting > 0:
            self._log.info("outbound.blocked_pending_approval", awaiting=awaiting)
            return {"status": "awaiting_approval", "awaiting": awaiting}

        today_sent = await self._count_today_sent()
        remaining = max(0, self.daily_cap - today_sent)

        if remaining == 0:
            self._log.info("outbound.daily_cap_reached", cap=self.daily_cap)
            return {"status": "cap_reached", "sent_today": today_sent, "cap": self.daily_cap}

        leads = await self._fetch_pending(limit=remaining * 3)  # fetch extra to account for filtered-out dupes
        if not leads:
            self._log.info("outbound.no_pending_leads")
            return {"status": "no_pending", "sent_to_telegram": 0}

        # Filter out leads whose phone was already contacted (duplicate records)
        leads = await self._filter_already_contacted(leads, limit=remaining)
        if not leads:
            self._log.info("outbound.all_pending_already_contacted")
            return {"status": "no_pending", "sent_to_telegram": 0, "note": "all leads already contacted"}

        self._log.info("outbound.batch_start", total=len(leads))

        # Header message
        header = (
            f"📋 *قائمة الإرسال اليومية — Smart Field*\n"
            f"{len(leads)} عميل في انتظار موافقتك\n"
            f"الحد اليومي: {today_sent}/{self.daily_cap} مُرسَل\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        await self._notify_owner(header)

        sent_count = 0
        for idx, lead in enumerate(leads):
            ok = await self._send_approval_card(lead, idx)
            if ok:
                await self._update_approval_status(lead["id"], "BATCH_SENT")
                sent_count += 1
            await asyncio.sleep(0.5)  # avoid Telegram rate limiting

        footer = "_الإرسال يبدأ بعد موافقتك على كل عميل_"
        await self._notify_owner(footer)

        self._log.info("outbound.batch_complete", sent_to_telegram=sent_count)
        return {"status": "batch_sent", "sent_to_telegram": sent_count}

    # ── Telegram approval card ─────────────────────────────────────────────────

    async def _send_approval_card(self, lead: dict, idx: int = 0) -> bool:
        """Send one lead as a Telegram card with ✅/❌ inline buttons."""
        raw = lead.get("raw_data") or {}
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                raw = {}

        name     = lead.get("name", "غير معروف")
        phone    = lead.get("phone", "—")
        score    = lead.get("score", 0)
        icp      = lead.get("icp_segment") or "—"
        category = raw.get("gemini_category") or lead.get("category", "—")
        # Assign A/B variant round-robin
        variant_key = pick_variant(idx)
        company = lead.get("name", "")
        ab_msg = render_variant(variant_key, company)
        if not raw.get("draft_message"):
            raw["draft_message"] = ab_msg
        raw["ab_variant"] = variant_key
        draft    = (raw.get("draft_message") or "").strip()[:280]
        variant_label = VARIANTS.get(variant_key, {}).get("name", variant_key)

        text = (
            f"🏢 *{name}*\n"
            f"التصنيف: {category}  |  ICP: {icp}  |  Score: {score}/100\n"
            f"الهاتف: `{phone}`\n\n"
            f"_الرسالة المقترحة:_\n{draft}"
        )

        keyboard = {
            "inline_keyboard": [[
                {"text": "✅ موافق — أرسل", "callback_data": f"outbound_approve:{lead['id']}"},
                {"text": "❌ رفض",           "callback_data": f"outbound_reject:{lead['id']}"},
            ]]
        }

        ok = False
        for chat_id in self.owner_chat_ids:
            ok = await self.telegram.send_message_with_keyboard(chat_id, text, keyboard) or ok
        return ok

    # ── Callback handler (called from webhook) ─────────────────────────────────

    async def handle_approval(self, lead_id: str, approved: bool) -> str:
        """
        Called from /webhook/telegram when owner taps ✅ or ❌.
        Returns a short Arabic status string shown in Telegram toast.
        """
        if not approved:
            await self._update_approval_status(lead_id, "REJECTED")
            return "❌ تم الرفض"

        # Check daily cap before sending
        today_sent = await self._count_today_sent()
        if today_sent >= self.daily_cap:
            return f"⚠️ وصلنا الحد اليومي ({self.daily_cap}). سيُرسل غداً."

        lead = await self._get_lead(lead_id)
        if not lead:
            return "⚠️ العميل غير موجود في CRM"

        phone = (lead.get("phone") or "").strip()
        if not phone or phone == "+966500000000":
            return "⚠️ لا يوجد رقم هاتف"

        await self._update_approval_status(lead_id, "APPROVED")

        # Get draft message
        raw = lead.get("raw_data") or {}
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                raw = {}
        message = (raw.get("draft_message") or "").strip()
        if not message:
            message = build_outreach_for_prospect(
                {"company": lead.get("name", ""), "city": "الرياض", "activity": ""},
                "generic",
            )

        sent = await self.notifier.send_custom_message(phone, message)
        if sent:
            await self._mark_sent(lead_id, message)
            # Record A/B variant
            variant = raw.get("ab_variant", "A")
            await self.ab_engine.record_variant(lead_id, variant, raw)
            self._log.info("outbound.sent", name=lead.get("name"), phone=phone)
            return f"✅ أُرسلت لـ *{lead.get('name')}*"
        else:
            self._log.warning("outbound.send_failed", lead_id=lead_id, phone=phone)
            return f"❌ فشل الإرسال — {phone}"

    # ── Legacy entry point (kept for backwards compat) ─────────────────────────

    async def send_pending_outreach(self) -> dict:
        """Redirects to the approval batch flow."""
        return await self.send_daily_approval_batch()

    # ── Supabase helpers ───────────────────────────────────────────────────────

    async def _fetch_pending(self, limit: int) -> list[dict]:
        """Fetch leads with approval_status=PENDING, phone set, status=new."""
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: self.crm.client.table("leads")
                    .select("*")
                    .eq("approval_status", "PENDING")
                    .not_.is_("phone", "null")
                    .neq("phone", "")
                    .eq("status", "new")
                    .order("score", desc=True)
                    .limit(limit)
                    .execute()
            )
            return result.data or []
        except Exception:
            # Fallback: approval_status column may not exist yet
            try:
                result = await loop.run_in_executor(
                    None,
                    lambda: self.crm.client.table("leads")
                        .select("*")
                        .not_.is_("phone", "null")
                        .neq("phone", "")
                        .eq("status", "new")
                        .order("score", desc=True)
                        .limit(limit)
                        .execute()
                )
                return result.data or []
            except Exception as exc:
                self._log.error("outbound.fetch_failed", error=str(exc))
                return []

    async def _get_contacted_phones(self) -> set:
        """Return set of phone numbers that have any non-new record in CRM."""
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: self.crm.client.table("leads")
                    .select("phone")
                    .neq("status", "new")
                    .not_.is_("phone", "null")
                    .neq("phone", "")
                    .neq("phone", "+966500000000")
                    .execute()
            )
            return {row["phone"] for row in (result.data or [])}
        except Exception as exc:
            self._log.warning("outbound.get_contacted_phones_failed", error=str(exc))
            return set()

    async def _filter_already_contacted(self, leads: list[dict], limit: int) -> list[dict]:
        """
        Remove leads whose phone number already exists in CRM with status != new.
        Also deduplicates within the batch (same phone appearing twice).
        """
        contacted_phones = await self._get_contacted_phones()
        seen_phones: set = set()
        filtered: list[dict] = []

        skipped = 0
        for lead in leads:
            phone = (lead.get("phone") or "").strip()
            if not phone:
                continue
            if phone in contacted_phones:
                self._log.info(
                    "outbound.skip_already_contacted",
                    name=lead.get("name"), phone=phone,
                )
                # Mark duplicate as contacted so it doesn't clog the pipeline
                await self._update_approval_status(lead["id"], "SENT")
                skipped += 1
                continue
            if phone in seen_phones:
                skipped += 1
                continue
            seen_phones.add(phone)
            filtered.append(lead)
            if len(filtered) >= limit:
                break

        if skipped:
            self._log.info("outbound.filtered_contacted", skipped=skipped, kept=len(filtered))

        return filtered

    async def _count_awaiting_approval(self) -> int:
        """عدد البطاقات التي أُرسلت للتيليغرام ولم يُبَتّ فيها بعد (BATCH_SENT)."""
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: self.crm.client.table("leads")
                    .select("id", count="exact")
                    .eq("approval_status", "BATCH_SENT")
                    .execute()
            )
            return result.count or 0
        except Exception:
            return 0

    async def _count_today_sent(self) -> int:
        """Count leads actually sent via WhatsApp today.
        Uses status=contacted to distinguish real sends from duplicate-cleanup records."""
        loop = asyncio.get_running_loop()
        today = date.today().isoformat()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: self.crm.client.table("leads")
                    .select("id", count="exact")
                    .eq("approval_status", "SENT")
                    .eq("status", "contacted")
                    .gte("updated_at", today)
                    .execute()
            )
            return result.count or 0
        except Exception:
            return 0

    async def _get_lead(self, lead_id: str) -> Optional[dict]:
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: self.crm.client.table("leads")
                    .select("*")
                    .eq("id", lead_id)
                    .limit(1)
                    .execute()
            )
            rows = result.data or []
            return rows[0] if rows else None
        except Exception as exc:
            self._log.error("outbound.get_lead_failed", lead_id=lead_id, error=str(exc))
            return None

    async def _update_approval_status(self, lead_id: str, status: str) -> None:
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None,
                lambda: self.crm.client.table("leads")
                    .update({"approval_status": status, "updated_at": datetime.utcnow().isoformat()})
                    .eq("id", lead_id)
                    .execute()
            )
        except Exception as exc:
            self._log.warning("outbound.update_status_failed", lead_id=lead_id, error=str(exc))

    async def _mark_sent(self, lead_id: str, message: str) -> None:
        loop = asyncio.get_running_loop()
        now = datetime.utcnow().isoformat()
        try:
            await loop.run_in_executor(
                None,
                lambda: self.crm.client.table("leads")
                    .update({
                        "approval_status": "SENT",
                        "status": "contacted",
                        "notes": f"[outbound] {message[:200]}",
                        "updated_at": now,
                    })
                    .eq("id", lead_id)
                    .execute()
            )
        except Exception as exc:
            self._log.warning("outbound.mark_sent_failed", lead_id=lead_id, error=str(exc))

    async def _notify_owner(self, message: str) -> None:
        if not self.telegram or not self.owner_chat_ids:
            return
        for chat_id in self.owner_chat_ids:
            try:
                await self.telegram.send_message(chat_id, message)
            except Exception:
                pass
