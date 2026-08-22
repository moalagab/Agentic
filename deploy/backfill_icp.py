"""
تعبئة رجعية لحقول ICP للعملاء الموجودين.

يُشغَّل مرة واحدة بعد تطبيق هجرة 2026-08-22_add_icp_columns.sql.
العملاء الذين دخلوا قبل الإصلاح لا يحملون تصنيفًا لأن الخريطة كانت
تُسقطه؛ هذا السكربت يعيد حسابه من نفس المحرّك ويكتبه.

آمن للتشغيل أكثر من مرة: يقرأ الحالة ويكتب النتيجة نفسها.
    python3 deploy/backfill_icp.py --dry-run   # عرض دون كتابة
    python3 deploy/backfill_icp.py             # تنفيذ
"""
from __future__ import annotations

import sys
from collections import Counter

sys.path.insert(0, ".")

from config import get_settings
from models.lead import Lead
from processors.icp_engine import score_lead_icp, detect_buying_signals
from supabase import create_client

DRY = "--dry-run" in sys.argv


def main() -> int:
    s = get_settings()
    sb = create_client(s.SUPABASE_URL, s.SUPABASE_KEY)

    rows = sb.table("leads").select("*").limit(5000).execute().data or []
    print(f"عملاء مقروءون: {len(rows)}")

    seg_count: Counter[str] = Counter()
    updated = skipped = failed = 0

    for r in rows:
        raw = r.get("raw_data") or {}
        place = raw.get("place") or {}
        try:
            tmp = Lead(
                name=r.get("name") or "unnamed",
                phone=r.get("phone") or None,
                email=None if r.get("phone") else "noreply@placeholder.com",
                raw_data={
                    "rating": place.get("rating") or raw.get("rating"),
                    "review_count": place.get("user_ratings_total") or raw.get("review_count"),
                    "description": raw.get("reason", ""),
                    "gemini_category": raw.get("gemini_category", ""),
                },
            )
            score, segment = score_lead_icp(tmp)
            signals = detect_buying_signals(tmp)
        except Exception as exc:
            print(f"  ⚠️  فشل تقييم {r.get('name')!r}: {exc}")
            failed += 1
            continue

        seg_count[segment] += 1

        # لا تكتب إن كانت القيمة مطابقة أصلًا
        if r.get("icp_segment") == segment and (r.get("icp_score") or 0) == score:
            skipped += 1
            continue

        if not DRY:
            try:
                sb.table("leads").update({
                    "icp_score": score,
                    "icp_segment": segment,
                    "buying_signals": signals,
                }).eq("id", r["id"]).execute()
            except Exception as exc:
                print(f"  ⚠️  فشل تحديث {r.get('name')!r}: {exc}")
                failed += 1
                continue
        updated += 1

    print()
    print(f"{'(تجريبي) ' if DRY else ''}محدَّث: {updated} | مطابق مسبقًا: {skipped} | فشل: {failed}")
    print("\nتوزيع الشرائح:")
    total = sum(seg_count.values()) or 1
    for seg, n in seg_count.most_common():
        print(f"  {seg:<20} {n:>5}  ({n/total*100:>5.1f}%)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
