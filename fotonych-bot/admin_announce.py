"""Объявления супер-админа в чат водителей."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path

from drivers_chat import _now_label, _tz, _tz_label, publish_drivers_announcement
from max_webapp import display_name_from_user
from super_admin import is_super_admin

logger = logging.getLogger(__name__)

_LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "drivers_announcements.jsonl"

MAX_TEXT_LEN = 1000

TEMPLATES = [
    {
        "id": "taksimo_time",
        "label": "Таксimo с …:…",
        "text": "📢 {date} · Таксimo · приём с 09:00 ·",
    },
    {
        "id": "crane_wait",
        "label": "Кран ждёт",
        "text": "📢 {date} · Таксimo · кран ждёт · ждём машины",
    },
    {
        "id": "factory_delay",
        "label": "Задержка на заводе",
        "text": "📢 {date} · Завод · задержка выдачи документов",
    },
    {
        "id": "custom",
        "label": "Свой текст",
        "text": "📢 {date} · ",
    },
]


def _date_label() -> str:
    return datetime.now(_tz()).strftime("%d.%m")


def admin_meta_payload() -> dict:
    date = _date_label()
    return {
        "date_label": date,
        "timezone_label": _tz_label(),
        "max_length": MAX_TEXT_LEN,
        "templates": [
            {**item, "text": item["text"].format(date=date)} for item in TEMPLATES
        ],
    }


def _read_log_lines(limit: int) -> list[dict]:
    if not _LOG_PATH.is_file():
        return []
    try:
        raw = _LOG_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        logger.exception("admin_announce: read %s", _LOG_PATH)
        return []
    if not raw:
        return []
    out: list[dict] = []
    for line in reversed(raw.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            out.append(item)
        if len(out) >= limit:
            break
    return out


def list_recent_announcements(limit: int = 5) -> list[dict]:
    limit = max(1, min(limit, 20))
    return _read_log_lines(limit)


def _append_log(entry: dict) -> None:
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


async def submit_drivers_announcement(
    *,
    max_user_id: int,
    user: dict,
    text: str,
) -> tuple[bool, str, dict | None]:
    if not is_super_admin(max_user_id):
        return False, "Нет доступа", None

    body = (text or "").strip()
    if not body:
        return False, "Введите текст объявления", None
    if len(body) > MAX_TEXT_LEN:
        return False, f"Слишком длинно (макс. {MAX_TEXT_LEN} символов)", None

    ok = await publish_drivers_announcement(body)
    if not ok:
        return False, "Не удалось отправить — проверьте чат водителей", None

    author = display_name_from_user(user)
    entry = {
        "id": f"{int(time.time())}-{max_user_id}",
        "at": datetime.now(_tz()).isoformat(timespec="minutes"),
        "at_label": _now_label(),
        "max_user_id": max_user_id,
        "author": author[:120],
        "text": body,
    }
    _append_log(entry)
    logger.info("Объявление в чат водителей: user=%s len=%s", max_user_id, len(body))
    return True, "Объявление отправлено · меню обновлено", entry
