"""Проверка role grants и account-level overrides в MySQL `taksimo_new`."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from app.core.settings import Settings


class PermissionRepositoryUnavailable(RuntimeError):
    """Хранилище прав недоступно или не сконфигурировано."""


@dataclass(frozen=True, slots=True)
class CapabilityGrant:
    """Итог одного capability после применения nullable override учётки."""

    can_view: bool
    can_edit: bool
    can_print: bool


class PermissionRepository(Protocol):
    """Граница проверки одного capability действующей учётной записи."""

    async def get_capability(self, account_id: int, capability: str) -> CapabilityGrant | None:
        """Вернуть итоговые права либо ``None`` для неактивной/неизвестной учётки."""


PoolFactory = Callable[..., Awaitable[Any]]


class AsyncMyPermissionRepository:
    """Ленивый asyncmy-пул для проверки прав непосредственно перед командой."""

    def __init__(self, settings: Settings, *, pool_factory: PoolFactory | None = None) -> None:
        self._settings = settings
        self._pool_factory = pool_factory
        self._pool: Any | None = None
        self._pool_lock = asyncio.Lock()

    async def get_capability(self, account_id: int, capability: str) -> CapabilityGrant | None:
        pool = await self._get_pool()
        try:
            from asyncmy.cursors import DictCursor

            async with pool.acquire() as connection:
                async with connection.cursor(cursor=DictCursor) as cursor:
                    await cursor.execute(
                        """SELECT COALESCE(account_permission.can_view, role_permission.can_view, 0) AS can_view,
                                  COALESCE(account_permission.can_edit, role_permission.can_edit, 0) AS can_edit,
                                  COALESCE(account_permission.can_print, role_permission.can_print, 0) AS can_print
                           FROM tn_operator_accounts AS account
                           JOIN tn_roles AS role ON role.id = account.role_id AND role.active = 1
                           LEFT JOIN tn_role_permissions AS role_permission
                             ON role_permission.role_id = role.id
                            AND role_permission.capability = %s
                           LEFT JOIN tn_account_permissions AS account_permission
                             ON account_permission.account_id = account.id
                            AND account_permission.capability = %s
                           WHERE account.id = %s AND account.active = 1
                           LIMIT 1""",
                        (capability, capability, account_id),
                    )
                    row = await cursor.fetchone()
        except PermissionRepositoryUnavailable:
            raise
        except Exception as exc:
            raise PermissionRepositoryUnavailable("Хранилище прав недоступно") from exc
        if row is None:
            return None
        return CapabilityGrant(
            can_view=bool(row["can_view"]),
            can_edit=bool(row["can_edit"]),
            can_print=bool(row["can_print"]),
        )

    async def close(self) -> None:
        """Корректно закрыть созданный пул при остановке ASGI-процесса."""
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
        if not settings.mysql_user or not settings.mysql_password or not settings.mysql_port:
            raise PermissionRepositoryUnavailable("Хранилище прав не сконфигурировано")
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
        except PermissionRepositoryUnavailable:
            raise
        except Exception as exc:
            raise PermissionRepositoryUnavailable("Хранилище прав недоступно") from exc
