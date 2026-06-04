"""Хранение постов канала и комментариев (SQLite)."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "comments.db"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS posts (
                post_id TEXT PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id TEXT NOT NULL,
                max_user_id INTEGER,
                author TEXT NOT NULL DEFAULT 'Гость',
                text TEXT NOT NULL,
                created_at REAL NOT NULL,
                FOREIGN KEY (post_id) REFERENCES posts(post_id)
            );
            CREATE INDEX IF NOT EXISTS idx_comments_post ON comments(post_id);
            """
        )
        try:
            conn.execute("ALTER TABLE comments ADD COLUMN max_user_id INTEGER")
        except sqlite3.OperationalError:
            pass


def upsert_post(post_id: str, chat_id: int, title: str) -> None:
    title = (title or "Пост в канале")[:500]
    now = time.time()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO posts (post_id, chat_id, title, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(post_id) DO UPDATE SET
                title = excluded.title,
                chat_id = excluded.chat_id
            """,
            (post_id, chat_id, title, now),
        )


def get_post(post_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT post_id, chat_id, title, created_at FROM posts WHERE post_id = ?",
            (post_id,),
        ).fetchone()
    if row is None:
        return None
    return dict(row)


def list_comments(post_id: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, post_id, author, text, created_at, max_user_id
            FROM comments WHERE post_id = ?
            ORDER BY created_at ASC
            """,
            (post_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def add_comment(
    post_id: str,
    text: str,
    author: str = "Гость",
    max_user_id: int | None = None,
) -> dict:
    text = text.strip()
    if not text:
        raise ValueError("empty text")
    if len(text) > 2000:
        raise ValueError("text too long")
    author = (author or "Гость").strip()[:80] or "Гость"
    now = time.time()
    with _connect() as conn:
        if conn.execute(
            "SELECT 1 FROM posts WHERE post_id = ?", (post_id,)
        ).fetchone() is None:
            raise KeyError("post not found")
        cur = conn.execute(
            """
            INSERT INTO comments (post_id, max_user_id, author, text, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (post_id, max_user_id, author, text, now),
        )
        comment_id = cur.lastrowid
    return {
        "id": comment_id,
        "post_id": post_id,
        "max_user_id": max_user_id,
        "author": author,
        "text": text,
        "created_at": now,
    }
