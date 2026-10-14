"""Изолированные unit-тесты SQL-проверок репозитория сессий без MySQL."""

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
from app.repositories.sessions import AsyncMySessionRepository, SessionRepositoryUnavailable


class AsyncMySessionRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_readiness_checks_mysql_read_only_session_table(self) -> None:
        cursor = _Cursor()
        repository = AsyncMySessionRepository(
            Settings(auth_secret="test-secret", mysql_user="test", mysql_password="test"),
            pool_factory=_pool_factory(_Connection(cursor)),
        )

        await repository.check_ready()

        self.assertEqual(cursor.statement, "SELECT 1 FROM tn_operator_sessions LIMIT 1")

    async def test_readiness_hides_query_failure(self) -> None:
        cursor = _Cursor(error=RuntimeError("private MySQL error"))
        repository = AsyncMySessionRepository(
            Settings(auth_secret="test-secret", mysql_user="test", mysql_password="test"),
            pool_factory=_pool_factory(_Connection(cursor)),
        )

        with self.assertRaises(SessionRepositoryUnavailable):
            await repository.check_ready()

    async def test_readiness_requires_auth_and_mysql_configuration(self) -> None:
        async def pool_factory(**kwargs: object) -> object:
            raise AssertionError("Пул не должен создаваться без настроек")

        repository = AsyncMySessionRepository(Settings(), pool_factory=pool_factory)

        with self.assertRaises(SessionRepositoryUnavailable):
            await repository.check_ready()


class _Cursor:
    def __init__(self, *, error: Exception | None = None) -> None:
        self._error = error
        self.statement = ""

    async def execute(self, query: str) -> None:
        if self._error is not None:
            raise self._error
        self.statement = " ".join(query.split())

    async def fetchone(self) -> None:
        return None


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
