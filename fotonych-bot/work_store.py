"""Заявки в сервис FOTON (mini-app avtmsk.ru)."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "comments.db"

CATEGORIES = (
    "электрика",
    "акпп",
    "двигатель",
    "пневматика",
    "ходовая",
    "запчасти",
    "диагностика",
    "другое",
)

STATUSES = ("новая", "в работе", "готова")


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_work_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS work_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                max_user_id INTEGER NOT NULL,
                author TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT 'другое',
                title TEXT NOT NULL DEFAULT '',
                text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'новая',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_work_user ON work_requests(max_user_id);
            CREATE INDEX IF NOT EXISTS idx_work_status ON work_requests(status);
            """
        )


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "max_user_id": row["max_user_id"],
        "author": row["author"],
        "category": row["category"],
        "title": row["title"],
        "text": row["text"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_request(
    *,
    max_user_id: int,
    author: str,
    category: str,
    title: str,
    text: str,
) -> dict:
    cat = category.strip().lower() or "другое"
    if cat not in CATEGORIES:
        cat = "другое"
    title = (title or "").strip()[:200] or "Заявка"
    body = text.strip()
    if not body:
        raise ValueError("text required")
    now = time.time()
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO work_requests
                (max_user_id, author, category, title, text, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'новая', ?, ?)
            """,
            (max_user_id, author, cat, title, body, now, now),
        )
        row = conn.execute(
            "SELECT * FROM work_requests WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
    return _row_to_dict(row)


def list_requests_for_user(max_user_id: int, *, limit: int = 50) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM work_requests
            WHERE max_user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max_user_id, limit),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_request(request_id: int, max_user_id: int | None = None) -> dict | None:
    with _connect() as conn:
        if max_user_id is None:
            row = conn.execute(
                "SELECT * FROM work_requests WHERE id = ?", (request_id,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM work_requests WHERE id = ? AND max_user_id = ?",
                (request_id, max_user_id),
            ).fetchone()
    return _row_to_dict(row) if row else None
