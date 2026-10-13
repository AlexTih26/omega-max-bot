"""Read-only проверка активной сессии в MySQL `taksimo_new`."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from app.auth.models import AuthenticatedUser
from app.core.settings import Settings


class SessionRepositoryUnavailable(RuntimeError):
    """Хранилище сессий недоступно или не сконфигурировано."""


class SessionRepository(Protocol):
    """Минимальная граница хранилища, достаточная для `GET /auth/me`."""

    async def find_active_user(self, token_hash: str) -> AuthenticatedUser | None:
        """Найти активного пользователя по HMAC-хэшу серверной сессии."""


PoolFactory = Callable[..., Awaitable[Any]]


class AsyncMySessionRepository:
    """Ленивый пул `asyncmy` для read-only проверки новых сессий Таксимо."""

    def __init__(self, settings: Settings, *, pool_factory: PoolFactory | None = None) -> None:
        self._settings = settings
        self._pool_factory = pool_factory
        self._pool: Any | None = None
        self._pool_lock = asyncio.Lock()

    async def find_active_user(self, token_hash: str) -> AuthenticatedUser | None:
        pool = await self._get_pool()
        try:
            from asyncmy.cursors import DictCursor

            async with pool.acquire() as connection:
                async with connection.cursor(cursor=DictCursor) as cursor:
                    await cursor.execute(
                        """SELECT a.id, a.login, a.display_name, a.role_code
                           FROM tn_operator_sessions AS s
                           JOIN tn_operator_accounts AS a ON a.id = s.operator_id
                           WHERE s.token_hash = %s
                             AND s.revoked_at IS NULL
                             AND s.expires_at > UTC_TIMESTAMP(6)
                             AND a.active = 1
                           LIMIT 1""",
                        (token_hash,),
                    )
                    row = await cursor.fetchone()
        except SessionRepositoryUnavailable:
            raise
        except Exception as exc:
            raise SessionRepositoryUnavailable("Хранилище сессий недоступно") from exc
        if row is None:
            return None
        return AuthenticatedUser(
            account_id=int(row["id"]),
            login=str(row["login"]) if row["login"] else None,
            name=str(row["display_name"]),
            role=str(row["role_code"]),
        )

    async def close(self) -> None:
        """Корректно закрыть пул при остановке ASGI-процесса."""
        if self._pool is not None:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None

    async def _get_pool(self) -> Any:
        if self._pool is not None:
            return self._pool
        async with self._pool_lock:
            if self._pool is None:
                self._pool = await self._create_pool()
            return self._pool

    async def _create_pool(self) -> Any:
        settings = self._settings
        if not settings.auth_secret or not settings.mysql_user or not settings.mysql_password or not settings.mysql_port:
            raise SessionRepositoryUnavailable("Сервис авторизации не сконфигурирован")
        try:
            pool_factory = self._pool_factory
            if pool_factory is None:
                from asyncmy import create_pool

                pool_factory = create_pool
            return await pool_factory(
                host=settings.mysql_host,
                port=settings.mysql_port,
                user=settings.mysql_user,
                password=settings.mysql_password,
                db=settings.mysql_database,
                charset="utf8mb4",
                autocommit=True,
                minsize=1,
                maxsize=5,
                connect_timeout=5,
            )
        except SessionRepositoryUnavailable:
            raise
        except Exception as exc:
            raise SessionRepositoryUnavailable("Хранилище сессий недоступно") from exc
