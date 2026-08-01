"""Обратная связь из mini-app панели (ошибки и идеи)."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path

from drivers_chat import _now_label, _tz

logger = logging.getLogger(__name__)

_FEEDBACK_PATH = Path(__file__).resolve().parent.parent / "data" / "panel_feedback.jsonl"

KIND_LABELS = {
    "bug": "ошибка",
    "feature": "идея",
}

APP_LABELS = {
    "rumex": "Румекс",
    "drivers": "Водители",
    "ipdocs": "Счёт и акт",
}


def _kind_label(kind: str) -> str:
    return KIND_LABELS.get(kind, kind or "сообщение")


def _app_label(app: str) -> str:
    return APP_LABELS.get(app, app or "панель")


def append_panel_feedback(
    *,
    max_user_id: int,
    author: str,
    app: str,
    kind: str,
    text: str,
    meta: dict | None = None,
) -> dict:
    body = (text or "").strip()
    if not body:
        raise ValueError("text required")
    if len(body) > 2000:
        raise ValueError("text too long")

    kind = (kind or "bug").strip().lower()
    if kind not in KIND_LABELS:
        kind = "bug"

    app = (app or "panel").strip().lower()
    if app not in APP_LABELS:
        app = "panel"

    entry = {
        "id": f"{int(time.time())}-{max_user_id}",
        "at": datetime.now(_tz()).isoformat(timespec="minutes"),
        "at_label": _now_label(),
        "max_user_id": max_user_id,
        "author": (author or "").strip()[:120],
        "app": app,
        "kind": kind,
        "text": body,
        "meta": meta if isinstance(meta, dict) else {},
    }

    _FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _FEEDBACK_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    logger.info(
        "Панель feedback: app=%s kind=%s user=%s",
        app,
        kind,
        max_user_id,
    )
    return entry


def format_panel_feedback_message(entry: dict) -> str:
    app = _app_label(str(entry.get("app") or ""))
    kind = _kind_label(str(entry.get("kind") or ""))
    author = str(entry.get("author") or "—")
    uid = entry.get("max_user_id")
    when = str(entry.get("at_label") or entry.get("at") or "")
    text = str(entry.get("text") or "")
    return (
        "📋 Панель · обратная связь\n\n"
        f"{app} · {kind}\n"
        f"От: {author} (id {uid})\n"
        f"{when}\n\n"
        f"{text}"
    )
