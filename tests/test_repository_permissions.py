"""Изолированные unit-тесты SQL-проверки прав без MySQL."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest
from typing import Generic, TypeVar


T = TypeVar("T")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.settings import Settings
from app.repositories.permissions import AsyncMyPermissionRepository, PermissionRepositoryUnavailable


class AsyncMyPermissionRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_applies_nullable_account_override_over_role_grant(self) -> None:
        cursor = _Cursor({"can_view": 1, "can_edit": 0, "can_print": 0})
        repository = AsyncMyPermissionRepository(
            Settings(mysql_user="test", mysql_password="test"),
            pool_factory=_pool_factory(_Connection(cursor)),
        )

        grant = await repository.get_capability(7, "trip.create")

        self.assertIsNotNone(grant)
        self.assertTrue(grant.can_view)
        self.assertFalse(grant.can_edit)
        self.assertFalse(grant.can_print)
        query, parameters = cursor.statement
        self.assertIn("COALESCE(account_permission.can_edit, role_permission.can_edit, 0)", query)
        self.assertIn("JOIN tn_roles AS role", query)
        self.assertEqual(parameters, ("trip.create", "trip.create", 7))

    async def test_unconfigured_repository_fails_without_creating_a_pool(self) -> None:
        async def pool_factory(**kwargs: object) -> object:
            raise AssertionError("Пул не должен создаваться без настроек")

        repository = AsyncMyPermissionRepository(Settings(), pool_factory=pool_factory)

        with self.assertRaises(PermissionRepositoryUnavailable):
            await repository.get_capability(7, "trip.create")


class _Cursor:
    def __init__(self, row: dict[str, int] | None) -> None:
        self._row = row
        self.statement: tuple[str, tuple[object, ...]] | None = None

    async def execute(self, query: str, parameters: tuple[object, ...]) -> None:
        self.statement = (" ".join(query.split()), parameters)

    async def fetchone(self) -> dict[str, int] | None:
        return self._row


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    def cursor(self, **kwargs: object) -> "_AsyncContext[_Cursor]":
        return _AsyncContext(self._cursor)


class _Pool:
    def __init__(self, connection: _Connection) -> None:
        self._connection = connection

    def acquire(self) -> "_AsyncContext[_Connection]":
        return _AsyncContext(self._connection)


class _AsyncContext(Generic[T]):
    def __init__(self, value: T) -> None:
        self._value = value

    async def __aenter__(self) -> T:
        return self._value

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None


def _pool_factory(connection: _Connection):
    async def factory(**kwargs: object) -> _Pool:
        return _Pool(connection)

    return factory


if __name__ == "__main__":
    unittest.main()
