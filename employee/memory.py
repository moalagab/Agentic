"""
Persistent memory for the autonomous agent using SQLite.
ذاكرة دائمة للوكيل المستقل باستخدام SQLite
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

DB_PATH = Path("data/smartfield_memory.db")


def _ensure_db_path():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def _get_conn():
    _ensure_db_path()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """Initialize all database tables."""
    with _get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS lead_follow_ups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id TEXT NOT NULL,
                lead_name TEXT,
                lead_phone TEXT,
                crm_id TEXT,
                stage TEXT NOT NULL DEFAULT 'initial',
                next_follow_up TEXT,
                last_contact TEXT,
                attempts INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                notes TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS agent_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action_type TEXT NOT NULL,
                description TEXT,
                result TEXT,
                lead_id TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS owner_commands (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT NOT NULL,
                command TEXT NOT NULL,
                response TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT NOT NULL
            );
        """)
    logger.info("memory.db_initialized")


# ─── Conversation history ─────────────────────────────────────────────────────

def save_message(phone: str, role: str, content: str):
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO conversations (phone, role, content, created_at) VALUES (?,?,?,?)",
            (phone, role, content, datetime.utcnow().isoformat()),
        )


def get_conversation_history(phone: str, limit: int = 20) -> list[dict]:
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT role, content FROM conversations WHERE phone=? ORDER BY created_at DESC LIMIT ?",
            (phone, limit),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def clear_conversation(phone: str):
    with _get_conn() as conn:
        conn.execute("DELETE FROM conversations WHERE phone=?", (phone,))


# ─── Follow-up tracking ───────────────────────────────────────────────────────

def schedule_follow_up(
    lead_id: str,
    lead_name: str,
    lead_phone: str,
    crm_id: str | None,
    days_until: int = 2,
):
    next_follow_up = (datetime.utcnow() + timedelta(days=days_until)).isoformat()
    with _get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM lead_follow_ups WHERE lead_id=? AND status='pending'",
            (lead_id,),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE lead_follow_ups SET next_follow_up=? WHERE id=?",
                (next_follow_up, existing["id"]),
            )
        else:
            conn.execute(
                """INSERT INTO lead_follow_ups
                   (lead_id, lead_name, lead_phone, crm_id, stage, next_follow_up, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (lead_id, lead_name, lead_phone, crm_id, "initial", next_follow_up,
                 datetime.utcnow().isoformat()),
            )


def get_due_follow_ups() -> list[dict]:
    now = datetime.utcnow().isoformat()
    with _get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM lead_follow_ups
               WHERE status='pending' AND next_follow_up <= ?
               ORDER BY next_follow_up ASC""",
            (now,),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_follow_up_done(follow_up_id: int, next_days: int | None = None, notes: str = ""):
    with _get_conn() as conn:
        if next_days:
            next_dt = (datetime.utcnow() + timedelta(days=next_days)).isoformat()
            conn.execute(
                """UPDATE lead_follow_ups
                   SET attempts=attempts+1, last_contact=?, next_follow_up=?,
                       stage='follow_up_' || CAST(attempts+1 AS TEXT), notes=?
                   WHERE id=?""",
                (datetime.utcnow().isoformat(), next_dt, notes, follow_up_id),
            )
        else:
            conn.execute(
                """UPDATE lead_follow_ups
                   SET status='done', last_contact=?, notes=?
                   WHERE id=?""",
                (datetime.utcnow().isoformat(), notes, follow_up_id),
            )


# ─── Action log ──────────────────────────────────────────────────────────────

def log_action(action_type: str, description: str, result: str = "", lead_id: str = ""):
    with _get_conn() as conn:
        conn.execute(
            "INSERT INTO agent_actions (action_type, description, result, lead_id, created_at) VALUES (?,?,?,?,?)",
            (action_type, description, result, lead_id, datetime.utcnow().isoformat()),
        )


def get_today_actions() -> list[dict]:
    today = datetime.utcnow().date().isoformat()
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM agent_actions WHERE created_at LIKE ? ORDER BY created_at DESC",
            (f"{today}%",),
        ).fetchall()
    return [dict(r) for r in rows]


def get_actions_since(hours: int = 24) -> list[dict]:
    since = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM agent_actions WHERE created_at >= ? ORDER BY created_at DESC",
            (since,),
        ).fetchall()
    return [dict(r) for r in rows]
