"""OMEGA Chat — отдельная SQLite (не Таксimo / водители / Румекс)."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "omega_chat.db"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _tz() -> ZoneInfo:
    name = (os.getenv("OMEGA_CHAT_TIMEZONE") or "Europe/Moscow").strip()
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("Europe/Moscow")


def _today_key() -> str:
    return datetime.now(_tz()).date().isoformat()


def _now_iso() -> str:
    return datetime.now(_tz()).isoformat(timespec="seconds")


def init_omega_chat_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS ai_users (
                max_user_id INTEGER PRIMARY KEY,
                display_name TEXT NOT NULL DEFAULT '',
                plan TEXT NOT NULL DEFAULT 'free',
                plan_until TEXT,
                daily_limit INTEGER,
                is_blocked INTEGER NOT NULL DEFAULT 0,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                total_messages INTEGER NOT NULL DEFAULT 0,
                notes TEXT
            );

            CREATE TABLE IF NOT EXISTS ai_conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                max_user_id INTEGER NOT NULL,
                title TEXT NOT NULL DEFAULT 'Новый диалог',
                model TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                message_count INTEGER NOT NULL DEFAULT 0,
                is_archived INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (max_user_id) REFERENCES ai_users(max_user_id)
            );
            CREATE INDEX IF NOT EXISTS idx_ai_conv_user
                ON ai_conversations(max_user_id, updated_at DESC);

            CREATE TABLE IF NOT EXISTS ai_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                max_user_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                model TEXT,
                prompt_tokens INTEGER,
                completion_tokens INTEGER,
                created_at TEXT NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES ai_conversations(id)
            );
            CREATE INDEX IF NOT EXISTS idx_ai_msg_conv
                ON ai_messages(conversation_id, id);

            CREATE TABLE IF NOT EXISTS ai_usage_daily (
                max_user_id INTEGER NOT NULL,
                usage_date TEXT NOT NULL,
                message_count INTEGER NOT NULL DEFAULT 0,
                token_in INTEGER NOT NULL DEFAULT 0,
                token_out INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (max_user_id, usage_date)
            );

            CREATE TABLE IF NOT EXISTS ai_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                max_user_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                meta_json TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ai_events_type
                ON ai_events(event_type, created_at);
            """
        )
        conn.commit()
    logger.info("OMEGA Chat DB ready: %s", DB_PATH)


def _daily_limit_for_plan(plan: str, override: int | None) -> int:
    if override is not None and override > 0:
        return int(override)
    if plan == "pro":
        raw = (os.getenv("OMEGA_CHAT_DAILY_LIMIT_PRO") or "200").strip()
    else:
        raw = (os.getenv("OMEGA_CHAT_DAILY_LIMIT_FREE") or "50").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 50 if plan != "pro" else 200


def _is_pro(row: sqlite3.Row | dict) -> bool:
    plan = str(row["plan"] if isinstance(row, sqlite3.Row) else row.get("plan") or "free")
    if plan != "pro":
        return False
    until = row["plan_until"] if isinstance(row, sqlite3.Row) else row.get("plan_until")
    if not until:
        return True
    try:
        end = datetime.fromisoformat(str(until))
        if end.tzinfo is None:
            end = end.replace(tzinfo=_tz())
        return datetime.now(_tz()) <= end.astimezone(_tz())
    except ValueError:
        return True


def _unlimited_ids() -> set[int]:
    raw = (os.getenv("OMEGA_CHAT_UNLIMITED_MAX_IDS") or "").strip()
    out: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return out


def upsert_user(*, max_user_id: int, display_name: str) -> dict:
    now = _now_iso()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM ai_users WHERE max_user_id = ?",
            (max_user_id,),
        ).fetchone()
        name = (display_name or "").strip() or "Пользователь MAX"
        if row is None:
            conn.execute(
                """
                INSERT INTO ai_users (
                    max_user_id, display_name, plan, first_seen_at, last_seen_at
                ) VALUES (?, ?, 'free', ?, ?)
                """,
                (max_user_id, name, now, now),
            )
        else:
            conn.execute(
                """
                UPDATE ai_users
                SET display_name = ?, last_seen_at = ?
                WHERE max_user_id = ?
                """,
                (name, now, max_user_id),
            )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM ai_users WHERE max_user_id = ?",
            (max_user_id,),
        ).fetchone()
    return dict(row) if row else {}


def get_user(max_user_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM ai_users WHERE max_user_id = ?",
            (max_user_id,),
        ).fetchone()
    return dict(row) if row else None


def log_event(*, max_user_id: int, event_type: str, meta: dict | None = None) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO ai_events (max_user_id, event_type, meta_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                max_user_id,
                event_type,
                json.dumps(meta or {}, ensure_ascii=False) if meta else None,
                _now_iso(),
            ),
        )
        conn.commit()


def get_usage_today(max_user_id: int) -> dict:
    day = _today_key()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT message_count, token_in, token_out
            FROM ai_usage_daily
            WHERE max_user_id = ? AND usage_date = ?
            """,
            (max_user_id, day),
        ).fetchone()
    if not row:
        return {"usage_date": day, "message_count": 0, "token_in": 0, "token_out": 0}
    return {
        "usage_date": day,
        "message_count": int(row["message_count"]),
        "token_in": int(row["token_in"]),
        "token_out": int(row["token_out"]),
    }


def can_send_message(max_user_id: int, user_row: dict | None = None) -> tuple[bool, str, int, int]:
    """(ok, reason, used_today, daily_limit)."""
    if max_user_id in _unlimited_ids():
        usage = get_usage_today(max_user_id)
        return True, "", usage["message_count"], 999999

    user = user_row or get_user(max_user_id)
    if not user:
        return False, "Пользователь не найден", 0, 0
    if int(user.get("is_blocked") or 0):
        return False, "Доступ ограничен", 0, 0

    plan = "pro" if _is_pro(user) else "free"
    limit = _daily_limit_for_plan(plan, user.get("daily_limit"))
    usage = get_usage_today(max_user_id)
    used = int(usage["message_count"])
    if used >= limit:
        return False, "daily_limit", used, limit
    return True, "", used, limit


def increment_usage(
    *,
    max_user_id: int,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> None:
    day = _today_key()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO ai_usage_daily (max_user_id, usage_date, message_count, token_in, token_out)
            VALUES (?, ?, 1, ?, ?)
            ON CONFLICT(max_user_id, usage_date) DO UPDATE SET
                message_count = message_count + 1,
                token_in = token_in + excluded.token_in,
                token_out = token_out + excluded.token_out
            """,
            (max_user_id, day, prompt_tokens, completion_tokens),
        )
        conn.execute(
            """
            UPDATE ai_users
            SET total_messages = total_messages + 1, last_seen_at = ?
            WHERE max_user_id = ?
            """,
            (_now_iso(), max_user_id),
        )
        conn.commit()


def list_conversations(*, max_user_id: int, limit: int = 20) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, title, updated_at, message_count
            FROM ai_conversations
            WHERE max_user_id = ? AND is_archived = 0
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (max_user_id, max(1, min(limit, 50))),
        ).fetchall()
    return [dict(r) for r in rows]


def get_conversation(*, conversation_id: int, max_user_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM ai_conversations
            WHERE id = ? AND max_user_id = ? AND is_archived = 0
            """,
            (conversation_id, max_user_id),
        ).fetchone()
    return dict(row) if row else None


def ensure_active_conversation(*, max_user_id: int) -> dict:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM ai_conversations
            WHERE max_user_id = ? AND is_archived = 0
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (max_user_id,),
        ).fetchone()
        if row:
            return dict(row)
        now = _now_iso()
        cur = conn.execute(
            """
            INSERT INTO ai_conversations (
                max_user_id, title, model, created_at, updated_at
            ) VALUES (?, 'Новый диалог', '', ?, ?)
            """,
            (max_user_id, now, now),
        )
        conn.commit()
        cid = int(cur.lastrowid)
        row = conn.execute(
            "SELECT * FROM ai_conversations WHERE id = ?",
            (cid,),
        ).fetchone()
    return dict(row)


def create_conversation(*, max_user_id: int, title: str = "Новый диалог") -> dict:
    now = _now_iso()
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO ai_conversations (
                max_user_id, title, model, created_at, updated_at
            ) VALUES (?, ?, '', ?, ?)
            """,
            (max_user_id, (title or "Новый диалог")[:120], now, now),
        )
        conn.commit()
        cid = int(cur.lastrowid)
        row = conn.execute(
            "SELECT * FROM ai_conversations WHERE id = ?",
            (cid,),
        ).fetchone()
    return dict(row)


def list_messages(*, conversation_id: int, max_user_id: int, limit: int = 100) -> list[dict]:
    if not get_conversation(conversation_id=conversation_id, max_user_id=max_user_id):
        return []
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, role, content, created_at
            FROM ai_messages
            WHERE conversation_id = ? AND max_user_id = ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (conversation_id, max_user_id, max(1, min(limit, 200))),
        ).fetchall()
    return [dict(r) for r in rows]


def list_context_messages(*, conversation_id: int, max_user_id: int, limit: int) -> list[dict]:
    if not get_conversation(conversation_id=conversation_id, max_user_id=max_user_id):
        return []
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT role, content FROM ai_messages
            WHERE conversation_id = ? AND max_user_id = ?
              AND role IN ('user', 'assistant')
            ORDER BY id DESC
            LIMIT ?
            """,
            (conversation_id, max_user_id, max(2, limit)),
        ).fetchall()
    items = [{"role": str(r["role"]), "content": str(r["content"])} for r in reversed(rows)]
    return items


def add_message(
    *,
    conversation_id: int,
    max_user_id: int,
    role: str,
    content: str,
    model: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
) -> dict:
    now = _now_iso()
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO ai_messages (
                conversation_id, max_user_id, role, content, model,
                prompt_tokens, completion_tokens, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                conversation_id,
                max_user_id,
                role,
                content,
                model,
                prompt_tokens,
                completion_tokens,
                now,
            ),
        )
        conn.execute(
            """
            UPDATE ai_conversations
            SET updated_at = ?, message_count = message_count + 1
            WHERE id = ? AND max_user_id = ?
            """,
            (now, conversation_id, max_user_id),
        )
        if role == "user":
            conn.execute(
                """
                UPDATE ai_conversations
                SET title = CASE
                    WHEN message_count <= 1 THEN ?
                    ELSE title
                END
                WHERE id = ?
                """,
                (content[:80].strip() or "Новый диалог", conversation_id),
            )
        conn.commit()
        mid = int(cur.lastrowid)
        row = conn.execute(
            "SELECT id, role, content, created_at FROM ai_messages WHERE id = ?",
            (mid,),
        ).fetchone()
    return dict(row) if row else {}


def clear_conversation_messages(*, conversation_id: int, max_user_id: int) -> bool:
    if not get_conversation(conversation_id=conversation_id, max_user_id=max_user_id):
        return False
    with _connect() as conn:
        conn.execute(
            "DELETE FROM ai_messages WHERE conversation_id = ? AND max_user_id = ?",
            (conversation_id, max_user_id),
        )
        conn.execute(
            """
            UPDATE ai_conversations
            SET message_count = 0, updated_at = ?, title = 'Новый диалог'
            WHERE id = ? AND max_user_id = ?
            """,
            (_now_iso(), conversation_id, max_user_id),
        )
        conn.commit()
    return True


def status_payload(*, max_user_id: int, display_name: str) -> dict:
    user = upsert_user(max_user_id=max_user_id, display_name=display_name)
    conv = ensure_active_conversation(max_user_id=max_user_id)
    ok, reason, used, limit = can_send_message(max_user_id, user)
    plan = "pro" if _is_pro(user) else "free"
    usage = get_usage_today(max_user_id)
    tz_label = (os.getenv("OMEGA_CHAT_TIMEZONE_LABEL") or "MSK").strip()
    return {
        "max_user_id": max_user_id,
        "display_name": user.get("display_name") or display_name,
        "plan": plan,
        "conversation_id": conv["id"],
        "conversation_title": conv.get("title") or "Новый диалог",
        "can_send": ok,
        "limit_reason": reason,
        "messages_today": used,
        "daily_limit": limit,
        "usage_date": usage["usage_date"],
        "tz_label": tz_label,
        "model_free": (os.getenv("OMEGA_CHAT_MODEL_FREE") or os.getenv("OPENAI_MODEL") or "gpt-4.1-mini").strip(),
        "model_pro": (os.getenv("OMEGA_CHAT_MODEL_PRO") or "gpt-4.1").strip(),
        "model_current": (os.getenv("OMEGA_CHAT_MODEL_PRO") if plan == "pro" else (os.getenv("OMEGA_CHAT_MODEL_FREE") or os.getenv("OPENAI_MODEL") or "gpt-4.1-mini")).strip(),
    }
