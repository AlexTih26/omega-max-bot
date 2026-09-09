"""Резервное копирование постоянного реестра отгрузок РУМЕКС."""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from rumex_registry_store import DB_PATH

logger = logging.getLogger(__name__)

BACKUP_DIR = DB_PATH.parent / "backups"
KEEP_DAYS = 90


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv("RUMEX_REGISTRY_TIMEZONE", "Asia/Irkutsk"))
    except Exception:
        return ZoneInfo("Asia/Irkutsk")


def backup_rumex_registry_db(*, reason: str = "manual") -> Path | None:
    """Создать согласованную копию базы в ``data/backups``.

    SQLite Backup API включает актуальные страницы из WAL, поэтому копия не
    зависит от того, успел ли сервер выполнить checkpoint в момент бэкапа.
    """
    if not DB_PATH.is_file():
        logger.warning("РУМЕКС реестр: файл БД не найден %s", DB_PATH)
        return None

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(_tz()).strftime("%Y-%m-%d_%H-%M-%S")
    destination = BACKUP_DIR / f"rumex-registry-{stamp}.db"
    source: sqlite3.Connection | None = None
    target: sqlite3.Connection | None = None
    try:
        source = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        target = sqlite3.connect(destination)
        source.backup(target)
        target.commit()
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        if target is not None:
            target.close()
        if source is not None:
            source.close()

    meta = destination.with_suffix(".meta.txt")
    meta.write_text(
        f"reason={reason}\nsource={DB_PATH}\nsize={destination.stat().st_size}\n",
        encoding="utf-8",
    )
    _prune_old_backups()
    logger.info("РУМЕКС реестр: бэкап %s (%s)", destination.name, reason)
    return destination


def _prune_old_backups() -> None:
    if not BACKUP_DIR.is_dir():
        return
    cutoff = time.time() - KEEP_DAYS * 86400
    for path in BACKUP_DIR.glob("rumex-registry-*.db"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                path.with_suffix(".meta.txt").unlink(missing_ok=True)
        except OSError:
            logger.exception("РУМЕКС реестр: не удалось удалить старый бэкап %s", path)


async def daily_rumex_registry_backup_loop() -> None:
    """Выполнять ежедневный бэкап в 03:10 в настроенной часовой зоне."""
    import asyncio

    logger.info("РУМЕКС реестр: ежедневный бэкап в 03:10 (%s)", _tz().key)
    while True:
        now = datetime.now(_tz())
        if now.hour == 3 and now.minute == 10:
            backup_rumex_registry_db(reason="daily")
            await asyncio.sleep(61)
        else:
            await asyncio.sleep(25)
