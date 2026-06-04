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
            CREATE TABLE IF NOT EXISTS comment_likes (
                comment_id INTEGER NOT NULL,
                max_user_id INTEGER NOT NULL,
                author TEXT NOT NULL DEFAULT '',
                author_photo TEXT,
                created_at REAL NOT NULL,
                PRIMARY KEY (comment_id, max_user_id),
                FOREIGN KEY (comment_id) REFERENCES comments(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_likes_comment ON comment_likes(comment_id);
            """
        )
        for ddl in (
            "ALTER TABLE comments ADD COLUMN max_user_id INTEGER",
            "ALTER TABLE comments ADD COLUMN parent_id INTEGER REFERENCES comments(id)",
            "ALTER TABLE comments ADD COLUMN author_photo TEXT",
        ):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_comments_parent ON comments(parent_id)"
        )


def upsert_post(
    post_id: str,
    chat_id: int,
    title: str,
    message_text: str | None = None,
    media_attachments_json: str | None = None,
) -> None:
    title = (title or "Пост в канале")[:500]
    msg = (message_text or "").strip()[:4000] if message_text else None
    media = (media_attachments_json or "").strip() or None
    now = time.time()
    with _connect() as conn:
        for ddl in (
            "ALTER TABLE posts ADD COLUMN message_text TEXT",
            "ALTER TABLE posts ADD COLUMN media_attachments_json TEXT",
        ):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass
        conn.execute(
            """
            INSERT INTO posts (
                post_id, chat_id, title, message_text, media_attachments_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(post_id) DO UPDATE SET
                title = excluded.title,
                chat_id = excluded.chat_id,
                message_text = COALESCE(excluded.message_text, posts.message_text),
                media_attachments_json = COALESCE(
                    excluded.media_attachments_json, posts.media_attachments_json
                )
            """,
            (post_id, chat_id, title, msg, media, now),
        )


def get_post(post_id: str) -> dict | None:
    with _connect() as conn:
        for ddl in (
            "ALTER TABLE posts ADD COLUMN message_text TEXT",
            "ALTER TABLE posts ADD COLUMN media_attachments_json TEXT",
        ):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass
        row = conn.execute(
            """
            SELECT post_id, chat_id, title, message_text, media_attachments_json, created_at
            FROM posts WHERE post_id = ?
            """,
            (post_id,),
        ).fetchone()
    if row is None:
        return None
    return dict(row)


def count_comments(post_id: str) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM comments WHERE post_id = ?",
            (post_id,),
        ).fetchone()
    return int(row["n"]) if row else 0


def _likes_map(conn: sqlite3.Connection, comment_ids: list[int]) -> dict[int, list[dict]]:
    if not comment_ids:
        return {}
    placeholders = ",".join("?" * len(comment_ids))
    rows = conn.execute(
        f"""
        SELECT comment_id, max_user_id, author, author_photo, created_at
        FROM comment_likes
        WHERE comment_id IN ({placeholders})
        ORDER BY created_at ASC
        """,
        comment_ids,
    ).fetchall()
    out: dict[int, list[dict]] = {}
    for row in rows:
        cid = row["comment_id"]
        out.setdefault(cid, []).append(
            {
                "max_user_id": row["max_user_id"],
                "author": row["author"],
                "author_photo": row["author_photo"],
            }
        )
    return out


def _attach_likes(
    comments: list[dict], likes_map: dict[int, list[dict]], viewer_id: int | None
) -> None:
    for c in comments:
        likers = likes_map.get(c["id"], [])
        c["likes"] = {
            "count": len(likers),
            "liked": viewer_id is not None
            and any(l["max_user_id"] == viewer_id for l in likers),
            "users": likers[:5],
        }


def list_comments(post_id: str, viewer_id: int | None = None) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, post_id, parent_id, author, author_photo, text, created_at, max_user_id
            FROM comments WHERE post_id = ?
            ORDER BY created_at ASC
            """,
            (post_id,),
        ).fetchall()
        comments = [dict(r) for r in rows]
        ids = [c["id"] for c in comments]
        likes_map = _likes_map(conn, ids)
    _attach_likes(comments, likes_map, viewer_id)
    return comments


def build_comment_tree(comments: list[dict]) -> list[dict]:
    """Плоский список → дерево с replies и reply_to (цитата родителя)."""
    nodes: dict[int, dict] = {}
    for row in comments:
        nodes[row["id"]] = {**row, "replies": []}

    def reply_to_block(parent: dict) -> dict:
        text = (parent.get("text") or "").strip()
        if len(text) > 140:
            text = text[:137] + "…"
        return {
            "id": parent["id"],
            "author": parent.get("author") or "Пользователь",
            "author_photo": parent.get("author_photo"),
            "text": text,
        }

    roots: list[dict] = []
    for row in comments:
        node = nodes[row["id"]]
        parent_id = row.get("parent_id")
        if parent_id is not None and parent_id in nodes:
            parent = nodes[parent_id]
            node["reply_to"] = reply_to_block(parent)
            parent["replies"].append(node)
        else:
            roots.append(node)
    return roots


def get_comment(comment_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT id, post_id, parent_id, author, author_photo, text, created_at, max_user_id
            FROM comments WHERE id = ?
            """,
            (comment_id,),
        ).fetchone()
    if row is None:
        return None
    return dict(row)


def add_comment(
    post_id: str,
    text: str,
    author: str = "Гость",
    max_user_id: int | None = None,
    parent_id: int | None = None,
    author_photo: str | None = None,
) -> dict:
    text = text.strip()
    if not text:
        raise ValueError("empty text")
    if len(text) > 2000:
        raise ValueError("text too long")
    author = (author or "Гость").strip()[:80] or "Гость"
    if author_photo:
        author_photo = author_photo.strip()[:500]
    now = time.time()
    with _connect() as conn:
        if conn.execute(
            "SELECT 1 FROM posts WHERE post_id = ?", (post_id,)
        ).fetchone() is None:
            raise KeyError("post not found")
        if parent_id is not None:
            parent = conn.execute(
                "SELECT post_id FROM comments WHERE id = ?", (parent_id,)
            ).fetchone()
            if parent is None:
                raise ValueError("parent comment not found")
            if parent["post_id"] != post_id:
                raise ValueError("parent comment belongs to another post")
        cur = conn.execute(
            """
            INSERT INTO comments (
                post_id, parent_id, max_user_id, author, author_photo, text, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (post_id, parent_id, max_user_id, author, author_photo, text, now),
        )
        comment_id = cur.lastrowid
    return {
        "id": comment_id,
        "post_id": post_id,
        "parent_id": parent_id,
        "max_user_id": max_user_id,
        "author": author,
        "author_photo": author_photo,
        "text": text,
        "created_at": now,
        "likes": {"count": 0, "liked": False, "users": []},
    }


def toggle_like(
    comment_id: int,
    max_user_id: int,
    author: str,
    author_photo: str | None = None,
) -> dict:
    with _connect() as conn:
        if conn.execute("SELECT 1 FROM comments WHERE id = ?", (comment_id,)).fetchone() is None:
            raise KeyError("comment not found")
        existing = conn.execute(
            """
            SELECT 1 FROM comment_likes
            WHERE comment_id = ? AND max_user_id = ?
            """,
            (comment_id, max_user_id),
        ).fetchone()
        if existing:
            conn.execute(
                "DELETE FROM comment_likes WHERE comment_id = ? AND max_user_id = ?",
                (comment_id, max_user_id),
            )
            liked = False
        else:
            conn.execute(
                """
                INSERT INTO comment_likes (comment_id, max_user_id, author, author_photo, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (comment_id, max_user_id, author, author_photo, time.time()),
            )
            liked = True
        rows = conn.execute(
            """
            SELECT max_user_id, author, author_photo
            FROM comment_likes WHERE comment_id = ?
            ORDER BY created_at ASC
            """,
            (comment_id,),
        ).fetchall()
    likers = [
        {
            "max_user_id": r["max_user_id"],
            "author": r["author"],
            "author_photo": r["author_photo"],
        }
        for r in rows
    ]
    return {"liked": liked, "count": len(likers), "users": likers[:5]}
