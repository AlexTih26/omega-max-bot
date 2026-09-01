"""Резервное копирование sklad_master.db."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sklad_master_store import _db_path

logger = logging.getLogger(__name__)

DB_PATH = _db_path()
BACKUP_DIR = DB_PATH.parent / "backups"
KEEP_DAYS = 90


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv("TAKSIMO_TIMEZONE", "Europe/Moscow"))
    except Exception:
        return ZoneInfo("Europe/Moscow")


def backup_sklad_master_db(*, reason: str = "manual") -> Path | None:
    """Копия БД в data/backups/sklad-master-YYYY-MM-DD_HH-MM-SS.db"""
    path = _db_path()
    if not path.is_file():
        logger.warning("Склад Мастер backup: файл БД не найден %s", path)
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(_tz()).strftime("%Y-%m-%d_%H-%M-%S")
    dest = BACKUP_DIR / f"sklad-master-{stamp}.db"
    shutil.copy2(path, dest)
    meta = dest.with_suffix(".meta.txt")
    meta.write_text(f"reason={reason}\nsource={path}\nsize={dest.stat().st_size}\n", encoding="utf-8")
    _prune_old_backups()
    logger.info("Склад Мастер backup: %s (%s)", dest.name, reason)
    return dest


def _prune_old_backups() -> None:
    if not BACKUP_DIR.is_dir():
        return
    cutoff = time.time() - KEEP_DAYS * 86400
    for path in BACKUP_DIR.glob("sklad-master-*.db"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                path.with_suffix(".meta.txt").unlink(missing_ok=True)
        except OSError:
            logger.exception("Склад Мастер backup: не удалось удалить %s", path)


async def daily_sklad_backup_loop() -> None:
    """Ежедневный бэкап в 03:00 по TAKSIMO_TIMEZONE."""
    logger.info("Склад Мастер: ежедневный бэкап БД в 03:00 (%s)", _tz().key)
    while True:
        now = datetime.now(_tz())
        if now.hour == 3 and now.minute == 0:
            backup_sklad_master_db(reason="daily")
            await asyncio.sleep(61)
        else:
            await asyncio.sleep(25)
