"""Изолированные unit-тесты SQL-репозитория рейсов без MySQL."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import unittest
from typing import Generic, TypeVar
from uuid import UUID


T = TypeVar("T")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.auth.models import AuthenticatedUser
from app.core.settings import Settings
from app.repositories.trips import AsyncMyTripRepository, CreateTripCommand
from app.trips.contracts import CreateTripRequest
from app.trips.errors import IdempotencyKeyReused, TripReferenceNotFound


class AsyncMyTripRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_commands_keep_their_transaction_local_idempotency_ids(self) -> None:
        pool = _CreationPool()
        repository = AsyncMyTripRepository(_configured_settings(), pool_factory=pool.factory)

        first, second = await asyncio.gather(
            repository.create_trip(_command("a" * 64)),
            repository.create_trip(_command("b" * 64)),
        )

        self.assertFalse(first.replayed)
        self.assertFalse(second.replayed)
        self.assertEqual([connection.commit_count for connection in pool.connections], [1, 1])
        self.assertEqual([connection.rollback_count for connection in pool.connections], [0, 0])
        self.assertNotIn("_pending_idempotency_id", vars(repository))
        for index, connection in enumerate(pool.connections, start=1):
            idempotency_id = 100 + index
            cursor = connection._cursor
            self.assertIsInstance(cursor, _CreationCursor)
            audit_parameters = cursor.audit_parameters
            complete_parameters = cursor.complete_parameters
            self.assertIsNotNone(audit_parameters)
            self.assertIsNotNone(complete_parameters)
            self.assertEqual(audit_parameters[4], idempotency_id)
            self.assertEqual(complete_parameters[1], idempotency_id)

    async def test_completed_idempotency_record_replays_without_creating_facts(self) -> None:
        cursor = _ReplayCursor(fingerprint="a" * 64, response=_response_json())
        connection = _Connection(cursor)
        repository = AsyncMyTripRepository(_configured_settings(), pool_factory=_single_pool_factory(connection))

        result = await repository.create_trip(_command("a" * 64))

        self.assertTrue(result.replayed)
        self.assertEqual(result.trip.public_id, UUID("08f1b37d-a5a3-4d32-8ee2-4ccd31a0231f"))
        self.assertEqual(connection.commit_count, 1)
        self.assertEqual(connection.rollback_count, 0)
        self.assertEqual(len(cursor.statements), 2)
        self.assertIn("FOR UPDATE", cursor.statements[1][0])

    async def test_changed_fingerprint_rolls_back_duplicate_key_transaction(self) -> None:
        cursor = _ReplayCursor(fingerprint="b" * 64, response=_response_json())
        connection = _Connection(cursor)
        repository = AsyncMyTripRepository(_configured_settings(), pool_factory=_single_pool_factory(connection))

        with self.assertRaises(IdempotencyKeyReused):
            await repository.create_trip(_command("a" * 64))

        self.assertEqual(connection.commit_count, 0)
        self.assertEqual(connection.rollback_count, 1)

    async def test_missing_active_reference_rolls_back_the_pending_key(self) -> None:
        cursor = _CreationCursor(index=1, missing_vehicle=True)
        connection = _Connection(cursor)
        repository = AsyncMyTripRepository(_configured_settings(), pool_factory=_single_pool_factory(connection))

        with self.assertRaises(TripReferenceNotFound):
            await repository.create_trip(_command("a" * 64))

        self.assertEqual(connection.commit_count, 0)
        self.assertEqual(connection.rollback_count, 1)
        self.assertTrue(cursor.inserted_pending_key)


def _configured_settings() -> Settings:
    return Settings(mysql_user="test", mysql_password="test")


def _command(fingerprint: str) -> CreateTripCommand:
    return CreateTripCommand(
        actor=AuthenticatedUser(account_id=7, login="dispatcher", name="Диспетчер", role="dispatcher"),
        idempotency_key=UUID("11111111-1111-4111-8111-111111111111"),
        fingerprint=fingerprint,
        request=CreateTripRequest.model_validate({
            "trip_date": "2026-10-13",
            "vehicle_id": 42,
            "carrier_id": 7,
            "driver_id": 19,
            "blocks": [{"letter": "A", "number": "901", "suffix": ""}],
        }),
        remote_address="127.0.0.1",
    )


def _response_json() -> str:
    return json.dumps({
        "trip": {
            "public_id": "08f1b37d-a5a3-4d32-8ee2-4ccd31a0231f",
            "status": "draft",
            "trip_date": "2026-10-13",
            "vehicle": {"id": 42, "plate": "А123ВС75"},
            "carrier": {"id": 7, "legal_form": "ООО", "name": "Перевозчик", "inn": "1234567890"},
            "driver": {"id": 19, "full_name": "Иванов Иван Иванович", "license_number": "75 АА 123456"},
            "block_count": 1,
            "total_weight_kg": 7780,
            "blocks": [{"letter": "A", "number": "901", "suffix": "", "weight_kg": 7780, "price_rub": "0.00"}],
            "created_at": "2026-10-13T19:27:08Z",
        },
    }, ensure_ascii=False)


class _CreationPool:
    def __init__(self) -> None:
        self.connections: list[_Connection] = []

    async def factory(self, **kwargs: object) -> "_CreationPool":
        return self

    def acquire(self) -> "_AsyncContext[_Connection]":
        index = len(self.connections) + 1
        connection = _Connection(_CreationCursor(index=index))
        self.connections.append(connection)
        return _AsyncContext(connection)


class _CreationCursor:
    def __init__(self, *, index: int, missing_vehicle: bool = False) -> None:
        self.index = index
        self.missing_vehicle = missing_vehicle
        self.lastrowid = 0
        self.rowcount = 0
        self._one: dict[str, object] | None = None
        self._many: list[dict[str, object]] = []
        self.statements: list[tuple[str, tuple[object, ...] | None]] = []
        self.audit_parameters: tuple[object, ...] | None = None
        self.complete_parameters: tuple[object, ...] | None = None
        self.inserted_pending_key = False

    async def execute(self, query: str, parameters: tuple[object, ...] | None = None) -> None:
        normalized = " ".join(query.split())
        self.statements.append((normalized, parameters))
        if normalized.startswith("INSERT INTO tn_command_idempotency"):
            self.lastrowid = 100 + self.index
            self.inserted_pending_key = True
            await asyncio.sleep(0)
        elif "FROM tn_vehicles" in normalized:
            self._one = None if self.missing_vehicle else {"id": 42, "plate": "А123ВС75"}
        elif "FROM tn_carriers" in normalized:
            self._one = {"id": 7, "legal_form": "ООО", "name": "Перевозчик", "inn": "1234567890"}
        elif "FROM tn_drivers" in normalized:
            self._one = {"id": 19, "full_name": "Иванов Иван Иванович", "license_number": "75 АА 123456"}
        elif "FROM tn_block_types" in normalized:
            self._many = [{"letter": "A", "weight_kg": 7780, "price_rub": "0.00"}]
        elif "FROM tn_block_registry" in normalized:
            self._one = None
        elif normalized.startswith("INSERT INTO tn_trips"):
            self.lastrowid = 200 + self.index
        elif normalized.startswith("INSERT INTO tn_block_registry"):
            self.lastrowid = 300 + self.index
        elif normalized.startswith("SELECT created_at FROM tn_trips"):
            self._one = {"created_at": datetime(2026, 10, 13, 19, 27, 8, tzinfo=timezone.utc)}
        elif normalized.startswith("INSERT INTO tn_action_log"):
            self.audit_parameters = parameters
        elif normalized.startswith("UPDATE tn_command_idempotency"):
            self.complete_parameters = parameters
            self.rowcount = 1

    async def fetchone(self) -> dict[str, object] | None:
        return self._one

    async def fetchall(self) -> list[dict[str, object]]:
        return self._many


class _ReplayCursor:
    def __init__(self, *, fingerprint: str, response: str) -> None:
        self.fingerprint = fingerprint
        self.response = response
        self.statements: list[tuple[str, tuple[object, ...] | None]] = []

    async def execute(self, query: str, parameters: tuple[object, ...] | None = None) -> None:
        normalized = " ".join(query.split())
        self.statements.append((normalized, parameters))
        if normalized.startswith("INSERT INTO tn_command_idempotency"):
            raise _DuplicateKeyError(1062, "duplicate")

    async def fetchone(self) -> dict[str, object]:
        return {
            "id": 500,
            "request_fingerprint": self.fingerprint,
            "status": "completed",
            "response_body_json": self.response,
        }


class _DuplicateKeyError(Exception):
    pass


class _Connection:
    def __init__(self, cursor: object) -> None:
        self._cursor = cursor
        self.begin_count = 0
        self.commit_count = 0
        self.rollback_count = 0

    async def begin(self) -> None:
        self.begin_count += 1

    async def commit(self) -> None:
        self.commit_count += 1

    async def rollback(self) -> None:
        self.rollback_count += 1

    def cursor(self, **kwargs: object) -> "_AsyncContext[object]":
        return _AsyncContext(self._cursor)


class _AsyncContext(Generic[T]):
    def __init__(self, value: T) -> None:
        self._value = value

    async def __aenter__(self) -> T:
        return self._value

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None


def _single_pool_factory(connection: _Connection):
    class _Pool:
        async def factory(self, **kwargs: object) -> "_Pool":
            return self

        def acquire(self) -> _AsyncContext[_Connection]:
            return _AsyncContext(connection)

    return _Pool().factory


if __name__ == "__main__":
    unittest.main()
