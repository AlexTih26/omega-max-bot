"""Подключение нового изолированного контура Таксимо к MySQL.

Старые SQLite-хранилища Таксимо намеренно не импортируются и не используются.
Схему применяет отдельный migrator-пользователь, а приложение работает только
под ограниченной учётной записью с DML-правами.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Iterator

from dotenv import load_dotenv

try:
    import pymysql
    from pymysql.cursors import DictCursor
except ModuleNotFoundError:  # Проверяется при первом реальном подключении.
    pymysql = None
    DictCursor = None


PRIVATE_ENV_PATH = Path("/root/.config/taksimo-new/mysql-app.env")


class TaksimoNewDatabaseError(RuntimeError):
    """MySQL нового контура недоступен или не настроен."""


def _load_private_environment() -> None:
    """Подключить закрытые настройки без передачи их в репозиторий."""
    if PRIVATE_ENV_PATH.is_file():
        load_dotenv(PRIVATE_ENV_PATH, override=False)


def _setting(*names: str, default: str = "") -> str:
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return default


def _connection_settings() -> dict[str, object]:
    _load_private_environment()
    user = _setting("TAKSIMO_NEW_MYSQL_USER", "MYSQL_USER")
    password = _setting("TAKSIMO_NEW_MYSQL_PASSWORD", "MYSQL_PASSWORD")
    database = _setting("TAKSIMO_NEW_MYSQL_DATABASE", "MYSQL_DATABASE", default="taksimo_new")
    if not user or not password:
        raise TaksimoNewDatabaseError("Не настроено закрытое подключение MySQL нового контура")
    try:
        port = int(_setting("TAKSIMO_NEW_MYSQL_PORT", "MYSQL_PORT", default="3306"))
    except ValueError as exc:
        raise TaksimoNewDatabaseError("Некорректный порт MySQL нового контура") from exc
    return {
        "host": _setting("TAKSIMO_NEW_MYSQL_HOST", "MYSQL_HOST", default="127.0.0.1"),
        "port": port,
        "user": user,
        "password": password,
        "database": database,
    }


def connect():
    """Открыть новое короткоживущее MySQL-подключение в UTC."""
    if pymysql is None or DictCursor is None:
        raise TaksimoNewDatabaseError("Не установлен драйвер PyMySQL нового контура")
    try:
        return pymysql.connect(
            **_connection_settings(),
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=False,
            connect_timeout=5,
            read_timeout=15,
            write_timeout=15,
            init_command="SET time_zone = '+00:00'",
        )
    except Exception as exc:
        raise TaksimoNewDatabaseError("Не удалось подключиться к MySQL нового контура") from exc


@contextmanager
def transaction() -> Iterator[object]:
    """Выполнить связанные изменения атомарно."""
    connection = connect()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def utc_now() -> datetime:
    """Наивный UTC для MySQL DATETIME; в API всегда сериализуется с Z."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso_utc(value: object) -> str | None:
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
