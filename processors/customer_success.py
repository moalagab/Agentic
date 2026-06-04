"""
Customer Success Engine — محرك نجاح العميل (Layer 8 - RevOS v6)

يزيد الاحتفاظ والقيمة طويلة المدى عبر:
- رصد أنماط الشحن
- اكتشاف فرص البيع الإضافي (Upsell)
- تذكيرات التجديد
- طلبات الإحالة

يعمل على العملاء المكتسبين (WON) وتحويلهم لعملاء متكررين.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

import structlog

logger = structlog.get_logger(__name__)


# ─── Thresholds ───────────────────────────────────────────────────────────────

RENEWAL_ALERT_DAYS = 30          # Alert 30 days before contract end
UPSELL_TRIGGER_SHIPMENTS = 10    # After 10 shipments, suggest upsell
REFERRAL_TRIGGER_SHIPMENTS = 5   # After 5 shipments, request referral
CHURN_WARNING_DAYS = 21          # No shipment in 21 days = churn risk
UPSELL_REVENUE_THRESHOLD = 15000 # Monthly revenue above this = upsell candidate


class CustomerSuccessEngine:
    """
    Monitors active customers and drives retention, upsell, and referrals.
    يراقب العملاء النشطين ويدفع نحو الاحتفاظ والتوسع والإحالة.
    """

    def __init__(self, supabase_client: Any = None, notifier: Any = None) -> None:
        self.supabase = supabase_client
        self.notifier = notifier
        self._log = logger.bind(component="CustomerSuccessEngine")

    async def run_daily_check(self) -> dict:
        """
        Main daily job — scans all WON customers and triggers actions.
        يمسح جميع العملاء المكتسبين يومياً ويطلق الإجراءات المناسبة.
        """
        results = {
            "renewals_alerted": 0,
            "upsells_identified": 0,
            "referrals_requested": 0,
            "churn_risks": 0,
            "total_won_customers": 0,
            "date": datetime.utcnow().strftime("%Y-%m-%d"),
        }

        won_customers = await self._fetch_won_customers()
        results["total_won_customers"] = len(won_customers)

        for customer in won_customers:
            try:
                actions = await self._analyze_customer(customer)
                for action_type, triggered in actions.items():
                    if triggered:
                        results[action_type] = results.get(action_type, 0) + 1
            except Exception as exc:
                self._log.warning("cs.customer_check_failed",
                                  customer=customer.get("name"), error=str(exc))

        self._log.info("cs.daily_check_complete", **results)
        return results

    async def _analyze_customer(self, customer: dict) -> dict[str, bool]:
        """Analyze a single customer for CS actions."""
        actions = {
            "renewals_alerted": False,
            "upsells_identified": False,
            "referrals_requested": False,
            "churn_risks": False,
        }

        won_date = self._parse_date(customer.get("won_date"))
        last_shipment = self._parse_date(customer.get("last_shipment_date"))
        total_shipments = int(customer.get("total_shipments") or 0)
        monthly_revenue = float(customer.get("expected_monthly_revenue") or 0)
        referral_requested = bool(customer.get("referral_requested"))

        now = datetime.utcnow()

        # ── Churn Risk: no shipment in 21+ days ───────────────────────────────
        if last_shipment and (now - last_shipment).days >= CHURN_WARNING_DAYS:
            actions["churn_risks"] = True
            await self._trigger_churn_alert(customer, last_shipment)

        # ── Renewal Alert: 30 days before 1-year mark ─────────────────────────
        if won_date:
            days_since_won = (now - won_date).days
            if 335 <= days_since_won <= 365:  # 30-day window before 1 year
                actions["renewals_alerted"] = True
                await self._trigger_renewal_alert(customer, won_date)

        # ── Upsell: high shipment count or high revenue ───────────────────────
        upsell_flagged = bool(customer.get("upsell_opportunity"))
        if not upsell_flagged:
            if total_shipments >= UPSELL_TRIGGER_SHIPMENTS or monthly_revenue >= UPSELL_REVENUE_THRESHOLD:
                upsell_opp = self._identify_upsell(customer, total_shipments, monthly_revenue)
                if upsell_opp:
                    actions["upsells_identified"] = True
                    await self._trigger_upsell(customer, upsell_opp)

        # ── Referral Request: loyal customer with 5+ shipments ────────────────
        if not referral_requested and total_shipments >= REFERRAL_TRIGGER_SHIPMENTS:
            actions["referrals_requested"] = True
            await self._trigger_referral_request(customer)

        return actions

    def _identify_upsell(
        self,
        customer: dict,
        total_shipments: int,
        monthly_revenue: float,
    ) -> Optional[str]:
        """Identify the best upsell opportunity for a customer."""
        category = str(customer.get("category", "")).lower()
        vehicle_type = customer.get("vehicle_type", "medium_truck")
        fleet_size = int(customer.get("fleet_size_needed") or 1)

        # Upsell logic based on profile
        if monthly_revenue >= 30000 and vehicle_type != "dedicated_fleet":
            return "عقد أسطول مخصص — توفير 15% على الأسعار الحالية"

        if "pharma" in category and total_shipments >= 15:
            return "خدمة Cold Chain Plus — تقارير يومية وتتبع متقدم للأدوية"

        if fleet_size >= 5 and vehicle_type == "medium_truck":
            return "ترقية لشاحنة كبيرة — تقليل رحلات وتوفير في التكلفة"

        if total_shipments >= 20:
            return "عقد سنوي — توفير 20% على قيمة العقد الحالي"

        return None

    async def _trigger_churn_alert(self, customer: dict, last_shipment: datetime) -> None:
        """Alert sales team about churn risk."""
        days_silent = (datetime.utcnow() - last_shipment).days
        msg = (
            f"⚠️ *تحذير: خطر فقدان عميل*\n"
            f"العميل: {customer.get('name')}\n"
            f"الشركة: {customer.get('company', '—')}\n"
            f"آخر شحنة: منذ {days_silent} يوم\n"
            f"الإيراد الشهري: {customer.get('expected_monthly_revenue', 0):,.0f} ريال\n"
            f"الإجراء: تواصل فوري لمعرفة السبب"
        )
        await self._send_alert(msg)

    async def _trigger_renewal_alert(self, customer: dict, won_date: datetime) -> None:
        """Alert about upcoming contract renewal."""
        days_until_year = 365 - (datetime.utcnow() - won_date).days
        msg = (
            f"🔄 *تجديد عقد قريب*\n"
            f"العميل: {customer.get('name')}\n"
            f"الشركة: {customer.get('company', '—')}\n"
            f"انتهاء العقد خلال: {days_until_year} يوم\n"
            f"الإيراد الشهري: {customer.get('expected_monthly_revenue', 0):,.0f} ريال\n"
            f"الإجراء: ابدأ محادثة التجديد الآن"
        )
        await self._send_alert(msg)

    async def _trigger_upsell(self, customer: dict, opportunity: str) -> None:
        """Alert sales team about upsell opportunity."""
        msg = (
            f"💡 *فرصة بيع إضافي*\n"
            f"العميل: {customer.get('name')}\n"
            f"الشركة: {customer.get('company', '—')}\n"
            f"الفرصة: {opportunity}\n"
            f"الشحنات المكتملة: {customer.get('total_shipments', 0)}\n"
            f"الإيراد الحالي: {customer.get('expected_monthly_revenue', 0):,.0f} ريال/شهر"
        )
        await self._send_alert(msg)

        # Update upsell field in Supabase
        if self.supabase and customer.get("id"):
            try:
                self.supabase.table("leads").update(
                    {"upsell_opportunity": opportunity}
                ).eq("id", customer["id"]).execute()
            except Exception as exc:
                self._log.warning("cs.upsell_update_failed", error=str(exc))

    async def _trigger_referral_request(self, customer: dict) -> None:
        """Trigger a referral request message."""
        msg = (
            f"🤝 *طلب إحالة — عميل وفي*\n"
            f"العميل: {customer.get('name')}\n"
            f"الشركة: {customer.get('company', '—')}\n"
            f"الشحنات المكتملة: {customer.get('total_shipments', 0)}\n"
            f"أرسل له رسالة واتساب تطلب إحالة شريك أو معارف في القطاع"
        )
        await self._send_alert(msg)

        # Mark as requested
        if self.supabase and customer.get("id"):
            try:
                self.supabase.table("leads").update(
                    {"referral_requested": True}
                ).eq("id", customer["id"]).execute()
            except Exception as exc:
                self._log.warning("cs.referral_update_failed", error=str(exc))

    async def _fetch_won_customers(self) -> list[dict]:
        """Fetch all WON customers from Supabase."""
        if not self.supabase:
            return []
        try:
            result = self.supabase.table("leads").select("*").eq(
                "status", "won"
            ).execute()
            return result.data or []
        except Exception as exc:
            self._log.error("cs.fetch_failed", error=str(exc))
            return []

    async def get_cs_opportunities(self) -> dict:
        """Get all current CS opportunities for the dashboard."""
        won_customers = await self._fetch_won_customers()

        opportunities = {
            "churn_risks": [],
            "upsell_candidates": [],
            "renewal_due": [],
            "referral_ready": [],
            "total_arr": 0.0,
        }

        now = datetime.utcnow()
        for c in won_customers:
            monthly_rev = float(c.get("expected_monthly_revenue") or 0)
            opportunities["total_arr"] += monthly_rev * 12

            # Churn risk
            last_ship = self._parse_date(c.get("last_shipment_date"))
            if last_ship and (now - last_ship).days >= CHURN_WARNING_DAYS:
                opportunities["churn_risks"].append({
                    "name": c.get("name"),
                    "company": c.get("company"),
                    "days_silent": (now - last_ship).days,
                    "monthly_revenue": monthly_rev,
                })

            # Upsell
            if c.get("upsell_opportunity"):
                opportunities["upsell_candidates"].append({
                    "name": c.get("name"),
                    "company": c.get("company"),
                    "opportunity": c.get("upsell_opportunity"),
                    "monthly_revenue": monthly_rev,
                })

            # Renewal
            won_date = self._parse_date(c.get("won_date"))
            if won_date:
                days_since_won = (now - won_date).days
                if 335 <= days_since_won <= 365:
                    opportunities["renewal_due"].append({
                        "name": c.get("name"),
                        "company": c.get("company"),
                        "days_remaining": 365 - days_since_won,
                        "monthly_revenue": monthly_rev,
                    })

            # Referral
            if not c.get("referral_requested") and int(c.get("total_shipments") or 0) >= REFERRAL_TRIGGER_SHIPMENTS:
                opportunities["referral_ready"].append({
                    "name": c.get("name"),
                    "company": c.get("company"),
                    "total_shipments": c.get("total_shipments"),
                })

        return opportunities

    async def _send_alert(self, message: str) -> None:
        """Send alert via configured notifier."""
        if not self.notifier:
            self._log.info("cs.alert_no_notifier", preview=message[:80])
            return
        try:
            # Try Telegram first, then WhatsApp
            if hasattr(self.notifier, "send_message"):
                # TelegramNotifier
                for chat_id in getattr(self.notifier, "_owner_chat_ids", []):
                    await self.notifier.send_message(chat_id, message)
            elif hasattr(self.notifier, "send_custom_message"):
                # WhatsAppNotifier — notify sales team
                for phone in getattr(self.notifier, "_sales_phones", []):
                    await self.notifier.send_custom_message(phone, message)
        except Exception as exc:
            self._log.warning("cs.alert_failed", error=str(exc))

    def _parse_date(self, value: Any) -> Optional[datetime]:
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00").replace("+00:00", ""))
        except Exception:
            return None
