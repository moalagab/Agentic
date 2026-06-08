"""
Nightly Backup Engine — نسخ احتياطي ليلي لقاعدة بيانات Supabase
يُصدّر جميع الجداول كملفات JSON محلية مع الاحتفاظ بآخر 7 أيام.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import structlog

if TYPE_CHECKING:
    from channels.telegram import TelegramHandler

logger = structlog.get_logger(__name__)

BACKUP_DIR = Path("/home/smartfield/backups")
RETENTION_DAYS = 7

TABLES = [
    "leads",
    "deals",
    "deal_stage_history",
    "conversations",
    "activities",
    "notifications_log",
    "outbound_leads",
]


class BackupEngine:
    def __init__(
        self,
        supabase_client,
        telegram: Optional["TelegramHandler"] = None,
        owner_chat_ids: Optional[list[str]] = None,
    ):
        self.sb = supabase_client
        self.telegram = telegram
        self.owner_chat_ids = owner_chat_ids or []
        self._log = logger.bind(component="BackupEngine")

    async def run_nightly_backup(self) -> dict:
        loop = asyncio.get_running_loop()
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
        backup_path = BACKUP_DIR / date_str
        backup_path.mkdir(parents=True, exist_ok=True)

        summary = {"date": date_str, "tables": {}, "total_rows": 0, "size_bytes": 0, "errors": []}

        for table in TABLES:
            try:
                result = await loop.run_in_executor(
                    None,
                    lambda t=table: self.sb.table(t).select("*").execute()
                )
                rows = result.data or []
                file_path = backup_path / f"{table}.json"
                file_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
                size = file_path.stat().st_size
                summary["tables"][table] = {"rows": len(rows), "size_bytes": size}
                summary["total_rows"] += len(rows)
                summary["size_bytes"] += size
                self._log.info("backup.table_done", table=table, rows=len(rows))
            except Exception as exc:
                self._log.error("backup.table_failed", table=table, error=str(exc))
                summary["errors"].append(f"{table}: {exc}")

        # Cleanup old backups
        await self._cleanup_old_backups()

        self._log.info("backup.complete", **{k: v for k, v in summary.items() if k != "tables"})
        await self._notify(summary)
        return summary

    async def _cleanup_old_backups(self):
        cutoff = datetime.utcnow() - timedelta(days=RETENTION_DAYS)
        if not BACKUP_DIR.exists():
            return
        for entry in BACKUP_DIR.iterdir():
            if not entry.is_dir():
                continue
            try:
                folder_date = datetime.strptime(entry.name, "%Y-%m-%d")
                if folder_date < cutoff:
                    shutil.rmtree(entry)
                    self._log.info("backup.old_removed", folder=entry.name)
            except ValueError:
                pass

    async def _notify(self, summary: dict):
        if not self.telegram or not self.owner_chat_ids:
            return
        size_kb = summary["size_bytes"] / 1024
        errors = summary["errors"]
        status = "✅" if not errors else "⚠️"
        tables_line = "  ".join(
            f"{t}: {v['rows']}" for t, v in summary["tables"].items()
        )
        msg = (
            f"{status} *Nightly Backup — {summary['date']}*\n"
            f"━━━━━━━━━━━━━━\n"
            f"📦 الجداول: {len(summary['tables'])}\n"
            f"📊 إجمالي السجلات: *{summary['total_rows']:,}*\n"
            f"💾 الحجم: {size_kb:.1f} KB\n"
            f"📁 المسار: /home/smartfield/backups/{summary['date']}/\n"
        )
        if errors:
            msg += f"\n⛔ أخطاء: {', '.join(errors)}"
        for chat_id in self.owner_chat_ids:
            try:
                await self.telegram.send_message(chat_id, msg)
            except Exception:
                pass

    def get_latest_backup_info(self) -> Optional[dict]:
        if not BACKUP_DIR.exists():
            return None
        dirs = sorted(
            [d for d in BACKUP_DIR.iterdir() if d.is_dir()],
            key=lambda d: d.name,
            reverse=True,
        )
        if not dirs:
            return None
        latest = dirs[0]
        info = {"date": latest.name, "tables": {}}
        for f in latest.glob("*.json"):
            try:
                rows = json.loads(f.read_text(encoding="utf-8"))
                info["tables"][f.stem] = len(rows)
            except Exception:
                pass
        return info
