"""Резервное копирование taksimo.db."""

from __future__ import annotations

import logging
import shutil
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from taksimo_store import DB_PATH

logger = logging.getLogger(__name__)

BACKUP_DIR = DB_PATH.parent / "backups"
KEEP_DAYS = 90


def _tz() -> ZoneInfo:
    import os

    try:
        return ZoneInfo(os.getenv("TAKSIMO_TIMEZONE", "Europe/Moscow"))
    except Exception:
        return ZoneInfo("Europe/Moscow")


def backup_taksimo_db(*, reason: str = "manual") -> Path | None:
    """Копия БД в data/backups/taksimo-YYYY-MM-DD_HH-MM-SS.db"""
    if not DB_PATH.is_file():
        logger.warning("Таксимо backup: файл БД не найден %s", DB_PATH)
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(_tz()).strftime("%Y-%m-%d_%H-%M-%S")
    dest = BACKUP_DIR / f"taksimo-{stamp}.db"
    shutil.copy2(DB_PATH, dest)
    meta = dest.with_suffix(".meta.txt")
    meta.write_text(f"reason={reason}\nsource={DB_PATH}\nsize={dest.stat().st_size}\n", encoding="utf-8")
    _prune_old_backups()
    logger.info("Таксимо backup: %s (%s)", dest.name, reason)
    return dest


def _prune_old_backups() -> None:
    if not BACKUP_DIR.is_dir():
        return
    cutoff = time.time() - KEEP_DAYS * 86400
    for path in BACKUP_DIR.glob("taksimo-*.db"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                path.with_suffix(".meta.txt").unlink(missing_ok=True)
        except OSError:
            logger.exception("Таксимо backup: не удалось удалить %s", path)


def list_backups() -> list[dict]:
    if not BACKUP_DIR.is_dir():
        return []
    out: list[dict] = []
    for path in sorted(BACKUP_DIR.glob("taksimo-*.db"), reverse=True):
        st = path.stat()
        out.append({"name": path.name, "size": st.st_size, "modified": st.st_mtime})
    return out


async def daily_backup_loop() -> None:
    """Ежедневный бэкап в 03:00 по TAKSIMO_TIMEZONE."""
    import asyncio

    logger.info("Таксимо: ежедневный бэкап БД в 03:00 (%s)", _tz().key)
    while True:
        now = datetime.now(_tz())
        if now.hour == 3 and now.minute == 0:
            backup_taksimo_db(reason="daily")
            await asyncio.sleep(61)
        else:
            await asyncio.sleep(25)
