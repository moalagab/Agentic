"""
Contract Converter — محوّل العملاء للعقد الشهري (NEW-002)
يتحقق يومياً من العملاء الذين نفّذوا 3+ رحلات ويعرض عليهم عقداً شهرياً
بسعر مخفض (160 → 130 SAR/رحلة).
يتطلب موافقة المالك عبر تيليغرام قبل إرسال العرض لواتساب العميل.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)

_OFFER_TEMPLATE = (
    "مرحباً {name} 👋\n\n"
    "لاحظنا إنكم أكملتم {trips} رحلات معنا بنجاح 🎯\n\n"
    "عندنا لكم عرض خاص:\n"
    "بدل 160 SAR/رحلة → *130 SAR/رحلة* عند التعاقد على 20 رحلة شهرياً\n\n"
    "مزايا العقد الشهري:\n"
    "✅ سعة ثابتة محجوزة لكم يومياً\n"
    "✅ أولوية في حالات الطوارئ\n"
    "✅ تقرير حراري شهري موثق\n\n"
    "هل نتفق؟ 📋"
)

_FOLLOWUP_TEMPLATE = (
    "مرحباً {name} — أردنا التأكد إنكم استلمتم عرضنا للعقد الشهري بـ 130 SAR/رحلة 🌡️\n"
    "هل تودّون أن نضبط التفاصيل معكم هذا الأسبوع؟"
)


class ContractConverter:
    """
    يومياً الساعة 10:15 ص:
    - يبحث عن عملاء completed_trips >= 3 + contract_offered = FALSE + deal_stage = WON
    - يرسل بطاقة موافقة للمالك عبر تيليغرام
    - بعد موافقة المالك: يرسل عرض واتساب ويضبط contract_offered = TRUE
    - متابعة بعد 3 أيام إن لم يرد العميل
    """

    def __init__(self, crm: Any, notifier: Any, telegram: Any = None, owner_chat_ids: list[str] | None = None):
        self.crm = crm
        self.notifier = notifier
        self.telegram = telegram
        self.owner_chat_ids: list[str] = owner_chat_ids or []
        self._log = logger.bind(component="ContractConverter")

    # ── Main daily check ────────────────────────────────────────────────────────

    async def check_and_offer_contracts(self) -> dict:
        """يُشغَّل يومياً — يرسل بطاقات موافقة للمالك لكل عميل مؤهل."""
        results = {"approval_cards_sent": 0, "followup_sent": 0}
        leads = await self._fetch_eligible_leads()
        followup_leads = await self._fetch_followup_due()

        for lead in leads:
            ok = await self._send_approval_card(lead)
            if ok:
                results["approval_cards_sent"] += 1

        for lead in followup_leads:
            sent = await self._send_followup(lead)
            if sent:
                results["followup_sent"] += 1

        self._log.info("contract_converter.daily_check", **results)
        return results

    # ── Telegram approval card ──────────────────────────────────────────────────

    async def _send_approval_card(self, lead: dict) -> bool:
        if not self.telegram or not self.owner_chat_ids:
            return False
        trips = int(lead.get("completed_trips") or 0)
        name = lead.get("name", "عميل")
        phone = lead.get("phone", "—")
        text = (
            f"🤝 *عرض عقد شهري*\n"
            f"الاسم: {name}\n"
            f"الهاتف: `{phone}`\n"
            f"الرحلات المكتملة: {trips}\n\n"
            f"العرض: 130 SAR/رحلة (بدل 160) — 20 رحلة/شهر\n\n"
            f"هل ترسل العرض للعميل؟"
        )
        keyboard = {"inline_keyboard": [[
            {"text": "✅ أرسل العرض", "callback_data": f"outbound_contract_approve:{lead['id']}"},
            {"text": "❌ تجاهل",       "callback_data": f"outbound_contract_reject:{lead['id']}"},
        ]]}
        ok = False
        for cid in self.owner_chat_ids:
            ok = await self.telegram.send_message_with_keyboard(cid, text, keyboard) or ok
        return ok

    # ── Approval callback (called from webhook) ─────────────────────────────────

    async def handle_approval(self, lead_id: str, approved: bool) -> str:
        if not approved:
            await self._mark_contract_rejected(lead_id)
            return "⏭ تم التجاهل"
        lead = await self._get_lead(lead_id)
        if not lead:
            return "⚠️ العميل غير موجود"
        phone = (lead.get("phone") or "").strip()
        if not phone:
            return "⚠️ لا يوجد رقم هاتف"
        name = lead.get("name", "عميل")
        trips = int(lead.get("completed_trips") or 0)
        msg = _OFFER_TEMPLATE.format(name=name, trips=trips)
        try:
            sent = await self.notifier.send_custom_message(phone, msg)
        except Exception:
            sent = False
        if sent:
            await self._mark_contract_offered(lead_id)
            return f"✅ عرض العقد أُرسل — {name}"
        return f"❌ فشل الإرسال — {phone}"

    # ── 3-day follow-up ─────────────────────────────────────────────────────────

    async def _fetch_followup_due(self) -> list[dict]:
        loop = asyncio.get_running_loop()
        cutoff = (datetime.utcnow() - timedelta(days=3)).isoformat()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: self.crm.client.table("leads")
                    .select("*")
                    .eq("contract_offered", True)
                    .neq("contract_status", "accepted")
                    .neq("contract_status", "rejected")
                    .lt("updated_at", cutoff)
                    .execute()
            )
            return result.data or []
        except Exception as exc:
            self._log.error("contract.followup_fetch_failed", error=str(exc))
            return []

    async def _send_followup(self, lead: dict) -> bool:
        phone = (lead.get("phone") or "").strip()
        if not phone:
            return False
        name = lead.get("name", "عميل")
        msg = _FOLLOWUP_TEMPLATE.format(name=name)
        try:
            sent = await self.notifier.send_custom_message(phone, msg)
            if sent:
                await self._bump_updated_at(lead["id"])
            return sent
        except Exception:
            return False

    # ── Supabase helpers ────────────────────────────────────────────────────────

    async def _fetch_eligible_leads(self) -> list[dict]:
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: self.crm.client.table("leads")
                    .select("*")
                    .gte("completed_trips", 3)
                    .eq("contract_offered", False)
                    .eq("deal_stage", "WON")
                    .execute()
            )
            return result.data or []
        except Exception as exc:
            self._log.error("contract.fetch_eligible_failed", error=str(exc))
            return []

    async def _get_lead(self, lead_id: str) -> Optional[dict]:
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: self.crm.client.table("leads").select("*").eq("id", lead_id).limit(1).execute()
            )
            rows = result.data or []
            return rows[0] if rows else None
        except Exception:
            return None

    async def _mark_contract_offered(self, lead_id: str) -> None:
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None,
                lambda: self.crm.client.table("leads")
                    .update({
                        "contract_offered": True,
                        "contract_status": "offered",
                        "updated_at": datetime.utcnow().isoformat(),
                    })
                    .eq("id", lead_id)
                    .execute()
            )
        except Exception as exc:
            self._log.warning("contract.mark_offered_failed", error=str(exc))

    async def _mark_contract_rejected(self, lead_id: str) -> None:
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None,
                lambda: self.crm.client.table("leads")
                    .update({
                        "contract_status": "rejected",
                        "updated_at": datetime.utcnow().isoformat(),
                    })
                    .eq("id", lead_id)
                    .execute()
            )
        except Exception as exc:
            self._log.warning("contract.mark_rejected_failed", error=str(exc))

    async def _bump_updated_at(self, lead_id: str) -> None:
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None,
                lambda: self.crm.client.table("leads")
                    .update({"updated_at": datetime.utcnow().isoformat()})
                    .eq("id", lead_id)
                    .execute()
            )
        except Exception:
            pass
