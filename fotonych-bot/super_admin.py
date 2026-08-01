"""Супер-админы экосистемы (владелец, будущая admin-панель)."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "super_admin.json"

_DEFAULT_IDS = {6676390, 122515011, 195442061}


def _parse_ids_env(name: str) -> set[int]:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return set()
    out: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part))
        except ValueError:
            continue
    return out


def load_super_admin_config() -> dict:
    if not _CONFIG_PATH.is_file():
        return {"super_admin_max_ids": sorted(_DEFAULT_IDS), "owners": []}
    try:
        with _CONFIG_PATH.open(encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        logger.exception("super_admin: read %s", _CONFIG_PATH)
        return {"super_admin_max_ids": sorted(_DEFAULT_IDS), "owners": []}


def super_admin_ids() -> set[int]:
    ids = _parse_ids_env("SUPER_ADMIN_MAX_IDS")
    if ids:
        return ids
    config = load_super_admin_config()
    raw = config.get("super_admin_max_ids") or []
    out = {int(x) for x in raw if str(x).isdigit() or isinstance(x, int)}
    return out or set(_DEFAULT_IDS)


def is_super_admin(user_id: int | None) -> bool:
    if user_id is None:
        return False
    return user_id in super_admin_ids()


def super_admin_labels() -> dict[int, str]:
    config = load_super_admin_config()
    labels: dict[int, str] = {}
    for item in config.get("owners") or []:
        if not isinstance(item, dict):
            continue
        try:
            uid = int(item.get("max_user_id") or 0)
        except (TypeError, ValueError):
            continue
        if uid > 0:
            labels[uid] = str(item.get("label") or f"id {uid}")
    return labels
