"""
A/B Test Engine — اختبار صياغات رسائل الـ Outreach
ثلاث نسخ (A/B/C) من الرسالة، تتناوب دورياً وتُتتبع نسبة الرد لكل نسخة.
"""
from __future__ import annotations

import asyncio
import random
from datetime import datetime
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)

# ── Variant Definitions ────────────────────────────────────────────────────────

VARIANTS = {
    "A": {
        "name": "عاطفي — خطر الخسارة",
        "description": "يركز على المشكلة والخطر المحتمل",
        "hook": "خسارة",
        "template": (
            "أهلاً {company}،\n\n"
            "كل يوم تأخير في التبريد = خطر على بضاعتك وسمعتك.\n"
            "Smart Field تضمن لك سلسلة تبريد موثّقة من الباب للباب.\n"
            "هل تودّ تجربة رحلة واحدة مجانية هذا الأسبوع؟"
        ),
    },
    "B": {
        "name": "اجتماعي — الدليل الاجتماعي",
        "description": "يستخدم أرقام العملاء الحاليين",
        "hook": "ثقة",
        "template": (
            "أهلاً {company}،\n\n"
            "أكثر من 40 شركة في الرياض تعتمد على Smart Field لنقل بضاعتها المبردة.\n"
            "أسطول Thermo King + تتبع GPS + معتمد ISO 22000.\n"
            "هل تحب تعرف كيف نخدم شركات مثل شركتك؟"
        ),
    },
    "C": {
        "name": "مباشر — CTA عرض",
        "description": "عرض سعر مباشر بدون مقدمة",
        "hook": "سعر",
        "template": (
            "أهلاً {company}،\n\n"
            "نقل مبرد داخل الرياض يبدأ من 160 ريال/رحلة.\n"
            "GPS + شهادة حرارة + ضمان جودة.\n"
            "أرسل لي وجهتك وأعطيك سعر خلال دقيقتين."
        ),
    },
}


def pick_variant(lead_index: int = 0) -> str:
    """Round-robin: A → B → C → A..."""
    keys = list(VARIANTS.keys())
    return keys[lead_index % len(keys)]


def render_variant(variant_key: str, company: str) -> str:
    variant = VARIANTS.get(variant_key, VARIANTS["A"])
    return variant["template"].format(company=company)


class ABTestEngine:
    def __init__(self, supabase_client):
        self.sb = supabase_client
        self._log = logger.bind(component="ABTestEngine")

    async def get_stats(self) -> dict:
        """جلب إحصائيات A/B من قاعدة البيانات."""
        loop = asyncio.get_running_loop()
        stats = {k: {"sent": 0, "responded": 0, "rate": 0.0, **v} for k, v in VARIANTS.items()}

        try:
            result = await loop.run_in_executor(
                None,
                lambda: self.sb.table("leads")
                    .select("raw_data, status, approval_status")
                    .eq("approval_status", "SENT")
                    .execute()
            )
            rows = result.data or []
            for row in rows:
                raw = row.get("raw_data") or {}
                variant = raw.get("ab_variant")
                if variant not in stats:
                    continue
                stats[variant]["sent"] += 1
                if row.get("status") not in ("new", "contacted"):
                    # responded = any progress beyond initial contact
                    pass
                if row.get("status") in ("qualified", "proposal_sent", "won", "negotiation"):
                    stats[variant]["responded"] += 1

            for k in stats:
                s = stats[k]["sent"]
                r = stats[k]["responded"]
                stats[k]["rate"] = round(r / s * 100, 1) if s > 0 else 0.0

        except Exception as exc:
            self._log.error("ab_test.stats_error", error=str(exc))

        return stats

    async def record_variant(self, lead_id: str, variant: str, current_raw: dict) -> None:
        """تسجيل الـ variant المستخدم في سجل العميل."""
        loop = asyncio.get_running_loop()
        updated_raw = {**(current_raw or {}), "ab_variant": variant, "ab_sent_at": datetime.utcnow().isoformat()}
        try:
            await loop.run_in_executor(
                None,
                lambda: self.sb.table("leads")
                    .update({"raw_data": updated_raw, "updated_at": datetime.utcnow().isoformat()})
                    .eq("id", lead_id)
                    .execute()
            )
            self._log.info("ab_test.variant_recorded", lead_id=lead_id, variant=variant)
        except Exception as exc:
            self._log.warning("ab_test.record_failed", lead_id=lead_id, error=str(exc))

    def render_stats_html(self, stats: dict) -> str:
        colors = {"A": "#3b82f6", "B": "#22c55e", "C": "#f59e0b"}
        rows = ""
        for k, s in stats.items():
            color = colors.get(k, "#64748b")
            bar_w = min(int(s["rate"] * 3), 100)
            rows += f"""
            <tr>
              <td><span style="font-weight:700;color:{color}">نسخة {k}</span></td>
              <td style="color:#64748b;font-size:12px">{s['name']}</td>
              <td style="font-weight:600">{s['sent']}</td>
              <td style="font-weight:600;color:#16a34a">{s['responded']}</td>
              <td>
                <div style="background:#f1f5f9;border-radius:4px;height:8px;width:100px;overflow:hidden">
                  <div style="background:{color};height:8px;width:{bar_w}px;border-radius:4px"></div>
                </div>
                <span style="font-size:11px;color:{color};font-weight:700">{s['rate']}%</span>
              </td>
            </tr>"""
        return f"""
        <table style="width:100%;border-collapse:collapse;font-size:13px">
          <thead><tr style="background:#f8fafc">
            <th style="padding:8px;text-align:right">النسخة</th>
            <th style="padding:8px;text-align:right">النوع</th>
            <th style="padding:8px;text-align:center">أُرسل</th>
            <th style="padding:8px;text-align:center">استجاب</th>
            <th style="padding:8px;text-align:center">معدل التحويل</th>
          </tr></thead>
          <tbody>{rows}</tbody>
        </table>"""
