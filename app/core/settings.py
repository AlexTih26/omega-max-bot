"""Настройки корневого FastAPI-контура без загрузки закрытых файлов."""

from __future__ import annotations

from dataclasses import dataclass
import os


def _setting(*names: str, default: str = "") -> str:
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return default


@dataclass(frozen=True, slots=True)
class Settings:
    """Значения из окружения процесса FastAPI.

    Закрытые настройки не читаются из репозитория. Развёртывание передаёт их
    процессу явно; без них маршрут с cookie отвечает безопасным `503`.
    """

    auth_cookie_name: str = "taksimo_new_auth"
    auth_secret: str = ""
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = ""
    mysql_password: str = ""
    mysql_database: str = "taksimo_new"

    @classmethod
    def from_environment(cls) -> "Settings":
        try:
            port = int(_setting("TAKSIMO_NEW_MYSQL_PORT", default="3306"))
        except ValueError:
            port = 0
        return cls(
            auth_secret=_setting("TAKSIMO_NEW_AUTH_SECRET"),
            mysql_host=_setting("TAKSIMO_NEW_MYSQL_HOST", default="127.0.0.1"),
            mysql_port=port,
            mysql_user=_setting("TAKSIMO_NEW_MYSQL_USER"),
            mysql_password=_setting("TAKSIMO_NEW_MYSQL_PASSWORD"),
            mysql_database=_setting("TAKSIMO_NEW_MYSQL_DATABASE", default="taksimo_new"),
        )
