"""Атомарное MySQL-хранилище создания рейса с устойчивой идемпотентностью."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from typing import Any, Protocol
from uuid import UUID, uuid4

from app.auth.models import AuthenticatedUser
from app.core.settings import Settings
from app.trips.contracts import (
    CreateTripRequest,
    TripBlockResponse,
    TripCarrierResponse,
    TripCreateResponse,
    TripDriverResponse,
    TripResponse,
    TripVehicleResponse,
)
from app.trips.errors import (
    BlockAlreadyRegistered,
    IdempotencyKeyReused,
    TripBlockTypeNotFound,
    TripReferenceNotFound,
    TripStorageUnavailable,
)


CREATE_TRIP_OPERATION = "dispatcher.trip.create"
CREATE_TRIP_FINGERPRINT_ALGORITHM = "sha256"
CREATE_TRIP_FINGERPRINT_VERSION = "normalized-json-v1"

PoolFactory = Callable[..., Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class CreateTripCommand:
    """Нормализованная команда, которую репозиторий обязан сохранить атомарно."""

    actor: AuthenticatedUser
    idempotency_key: UUID
    fingerprint: str
    request: CreateTripRequest
    remote_address: str = ""


@dataclass(frozen=True, slots=True)
class TripCreationResult:
    """Сохранённый публичный ответ и признак безопасного повтора."""

    trip: TripResponse
    replayed: bool


class TripRepository(Protocol):
    """Граница одной транзакции создания рейса, паспортов и идемпотентности."""

    async def create_trip(self, command: CreateTripCommand) -> TripCreationResult:
        """Атомарно создать рейс либо вернуть сохранённый результат повтора."""


class AsyncMyTripRepository:
    """Записывает рейс, аудит и replay-результат одной MySQL-транзакцией."""

    def __init__(self, settings: Settings, *, pool_factory: PoolFactory | None = None) -> None:
        self._settings = settings
        self._pool_factory = pool_factory
        self._pool: Any | None = None
        self._pool_lock = asyncio.Lock()

    async def create_trip(self, command: CreateTripCommand) -> TripCreationResult:
        """Создать рейс либо воспроизвести завершённую команду с тем же ключом."""

        try:
            pool = await self._get_pool()
            from asyncmy.cursors import DictCursor

            async with pool.acquire() as connection:
                await connection.begin()
                try:
                    async with connection.cursor(cursor=DictCursor) as cursor:
                        replay, idempotency_id = await self._reserve_or_replay(cursor, command)
                        if replay is not None:
                            await connection.commit()
                            return replay

                        if idempotency_id is None:
                            raise TripStorageUnavailable("Не удалось зарезервировать команду идемпотентности")
                        result = await self._create_new_trip(cursor, command, idempotency_id)
                    await connection.commit()
                    return result
                except (
                    BlockAlreadyRegistered,
                    IdempotencyKeyReused,
                    TripBlockTypeNotFound,
                    TripReferenceNotFound,
                    TripStorageUnavailable,
                ):
                    await self._rollback_or_raise(connection)
                    raise
                except Exception as exc:
                    await self._rollback_or_raise(connection)
                    raise TripStorageUnavailable("Хранилище рейсов недоступно") from exc
        except (
            BlockAlreadyRegistered,
            IdempotencyKeyReused,
            TripBlockTypeNotFound,
            TripReferenceNotFound,
            TripStorageUnavailable,
        ):
            raise
        except Exception as exc:
            raise TripStorageUnavailable("Хранилище рейсов недоступно") from exc

    async def close(self) -> None:
        """Закрыть ленивый пул при остановке FastAPI-процесса."""

        if self._pool is not None:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None

    async def _reserve_or_replay(
        self,
        cursor: Any,
        command: CreateTripCommand,
    ) -> tuple[TripCreationResult | None, int | None]:
        """Зарезервировать ключ либо вернуть ранее сохранённый ответ.

        ID новой записи остаётся локальным для текущего вызова и транзакции.
        Экземпляр репозитория разделяется запросами FastAPI, поэтому хранить
        промежуточный ID в ``self`` было бы небезопасно.
        """
        try:
            await cursor.execute(
                """INSERT INTO tn_command_idempotency
                   (actor_account_id, operation_code, idempotency_key, fingerprint_algorithm,
                    fingerprint_version, request_fingerprint, status, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, 'pending', UTC_TIMESTAMP(6))""",
                (
                    command.actor.account_id,
                    CREATE_TRIP_OPERATION,
                    str(command.idempotency_key),
                    CREATE_TRIP_FINGERPRINT_ALGORITHM,
                    CREATE_TRIP_FINGERPRINT_VERSION,
                    command.fingerprint,
                ),
            )
        except Exception as exc:
            if not _is_duplicate_key(exc):
                raise
            await cursor.execute(
                """SELECT id, request_fingerprint, status, response_body_json
                   FROM tn_command_idempotency
                   WHERE actor_account_id = %s
                     AND operation_code = %s
                     AND idempotency_key = %s
                   FOR UPDATE""",
                (command.actor.account_id, CREATE_TRIP_OPERATION, str(command.idempotency_key)),
            )
            row = await cursor.fetchone()
            if row is None:
                raise TripStorageUnavailable("Запись идемпотентности недоступна")
            if str(row["request_fingerprint"]) != command.fingerprint:
                raise IdempotencyKeyReused()
            if row["status"] != "completed":
                raise TripStorageUnavailable("Команда идемпотентности не завершена")
            return TripCreationResult(trip=_replayed_trip(row["response_body_json"]), replayed=True), None

        return None, int(cursor.lastrowid)

    async def _create_new_trip(
        self,
        cursor: Any,
        command: CreateTripCommand,
        idempotency_id: int,
    ) -> TripCreationResult:
        vehicle = await _fetch_reference(
            cursor,
            """SELECT id, plate FROM tn_vehicles
               WHERE id = %s AND active = 1
               FOR UPDATE""",
            command.request.vehicle_id,
            "vehicle",
        )
        carrier = await _fetch_reference(
            cursor,
            """SELECT id, legal_form, name, inn FROM tn_carriers
               WHERE id = %s AND active = 1
               FOR UPDATE""",
            command.request.carrier_id,
            "carrier",
        )
        driver = await _fetch_reference(
            cursor,
            """SELECT id, full_name, license_number FROM tn_drivers
               WHERE id = %s AND active = 1
               FOR UPDATE""",
            command.request.driver_id,
            "driver",
        )

        blocks = tuple(command.request.blocks)
        letters = tuple(dict.fromkeys(block.letter for block in blocks))
        placeholders = ", ".join("%s" for _ in letters)
        await cursor.execute(
            f"""SELECT letter, weight_kg, price_rub FROM tn_block_types
                WHERE letter IN ({placeholders})
                FOR UPDATE""",
            letters,
        )
        block_types = {str(row["letter"]): row for row in await cursor.fetchall()}
        if any(block.letter not in block_types for block in blocks):
            raise TripBlockTypeNotFound()

        block_keys = tuple(f"{block.letter}-{block.number}" for block in blocks)
        key_placeholders = ", ".join("%s" for _ in block_keys)
        await cursor.execute(
            f"""SELECT block_key FROM tn_block_registry
                WHERE block_key IN ({key_placeholders})
                FOR UPDATE""",
            block_keys,
        )
        if await cursor.fetchone() is not None:
            raise BlockAlreadyRegistered()

        public_id = uuid4()
        response_blocks = tuple(
            TripBlockResponse(
                letter=block.letter,
                number=block.number,
                suffix=block.suffix,
                weight_kg=int(block_types[block.letter]["weight_kg"]),
                price_rub=_money(block_types[block.letter]["price_rub"]),
            )
            for block in blocks
        )
        total_weight_kg = sum(block.weight_kg for block in response_blocks)

        await cursor.execute(
            """INSERT INTO tn_trips
               (public_id, trip_date, vehicle_id, vehicle_plate, carrier_id, carrier_name, carrier_inn,
                carrier_legal_form, driver_id, driver_name, driver_license, block_count, total_weight_kg,
                status, created_by_account_id, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       'draft', %s, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))""",
            (
                str(public_id),
                command.request.trip_date,
                int(vehicle["id"]),
                str(vehicle["plate"]),
                int(carrier["id"]),
                str(carrier["name"]),
                str(carrier["inn"]),
                str(carrier["legal_form"]),
                int(driver["id"]),
                str(driver["full_name"]),
                str(driver["license_number"]),
                len(response_blocks),
                total_weight_kg,
                command.actor.account_id,
            ),
        )
        trip_id = int(cursor.lastrowid)

        for block, response_block in zip(blocks, response_blocks, strict=True):
            block_key = f"{block.letter}-{block.number}"
            try:
                await cursor.execute(
                    """INSERT INTO tn_block_registry
                       (block_key, block_letter, block_number, suffix_code, weight_kg, price_rub, state,
                        first_trip_id, first_account_id, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s, 'in_transit', %s, %s,
                               UTC_TIMESTAMP(6), UTC_TIMESTAMP(6))""",
                    (
                        block_key,
                        block.letter,
                        block.number,
                        block.suffix,
                        response_block.weight_kg,
                        response_block.price_rub,
                        trip_id,
                        command.actor.account_id,
                    ),
                )
            except Exception as exc:
                if _is_duplicate_key(exc):
                    raise BlockAlreadyRegistered() from exc
                raise
            registry_block_id = int(cursor.lastrowid)
            await cursor.execute(
                """INSERT INTO tn_trip_blocks
                   (trip_id, block_id, registry_block_id, block_letter, block_number, suffix_code,
                    weight_kg, price_rub, created_at)
                   VALUES (%s, NULL, %s, %s, %s, %s, %s, %s, UTC_TIMESTAMP(6))""",
                (
                    trip_id,
                    registry_block_id,
                    block.letter,
                    block.number,
                    block.suffix,
                    response_block.weight_kg,
                    response_block.price_rub,
                ),
            )
            await cursor.execute(
                """INSERT INTO tn_block_history
                   (block_id, event_type, location_kind, trip_id, wagon_number, details_json,
                    actor_kind, actor_id, actor_name, occurred_at)
                   VALUES (%s, 'trip_declared', 'in_transit', %s, '', CAST(%s AS JSON),
                           'account', %s, %s, UTC_TIMESTAMP(6))""",
                (
                    registry_block_id,
                    trip_id,
                    _json_value({"source": CREATE_TRIP_OPERATION}),
                    str(command.actor.account_id),
                    command.actor.name,
                ),
            )

        await cursor.execute("SELECT created_at FROM tn_trips WHERE id = %s", (trip_id,))
        saved_trip = await cursor.fetchone()
        if saved_trip is None:
            raise TripStorageUnavailable("Созданный рейс недоступен")
        trip = TripResponse(
            public_id=public_id,
            status="draft",
            trip_date=command.request.trip_date,
            vehicle=TripVehicleResponse(id=int(vehicle["id"]), plate=str(vehicle["plate"])),
            carrier=TripCarrierResponse(
                id=int(carrier["id"]),
                legal_form=str(carrier["legal_form"]),
                name=str(carrier["name"]),
                inn=str(carrier["inn"]),
            ),
            driver=TripDriverResponse(
                id=int(driver["id"]),
                full_name=str(driver["full_name"]),
                license_number=str(driver["license_number"]),
            ),
            block_count=len(response_blocks),
            total_weight_kg=total_weight_kg,
            blocks=list(response_blocks),
            created_at=_utc_datetime(saved_trip["created_at"]),
        )
        response_body = TripCreateResponse(trip=trip).model_dump(mode="json")
        await cursor.execute(
            """INSERT INTO tn_action_log
               (account_id, role_code, actor_display_name, action, capability_code, subject_type,
                subject_id, idempotency_id, details_json, remote_address, created_at)
               VALUES (%s, %s, %s, 'trip_created', 'trip.create', 'trip', %s, %s,
                       CAST(%s AS JSON), %s, UTC_TIMESTAMP(6))""",
            (
                command.actor.account_id,
                command.actor.role,
                command.actor.name,
                str(public_id),
                idempotency_id,
                _json_value(
                    {
                        "block_count": len(response_blocks),
                        "result_code": "created",
                        "total_weight_kg": total_weight_kg,
                    }
                ),
                command.remote_address[:128],
            ),
        )
        await cursor.execute(
            """UPDATE tn_command_idempotency
               SET status = 'completed',
                   initial_response_status = 201,
                   response_body_json = CAST(%s AS JSON),
                   completed_at = UTC_TIMESTAMP(6)
               WHERE id = %s AND status = 'pending'""",
            (_json_value(response_body), idempotency_id),
        )
        if cursor.rowcount != 1:
            raise TripStorageUnavailable("Не удалось завершить команду идемпотентности")
        return TripCreationResult(trip=trip, replayed=False)

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
            raise TripStorageUnavailable("Хранилище рейсов не сконфигурировано")
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
        except TripStorageUnavailable:
            raise
        except Exception as exc:
            raise TripStorageUnavailable("Хранилище рейсов недоступно") from exc

    @staticmethod
    async def _rollback_or_raise(connection: Any) -> None:
        try:
            await connection.rollback()
        except Exception as exc:
            raise TripStorageUnavailable("Не удалось отменить транзакцию рейса") from exc


class NotImplementedTripRepository:
    """Совместимая заглушка для изолированных тестов без MySQL."""

    async def create_trip(self, command: CreateTripCommand) -> TripCreationResult:
        raise NotImplementedError("Репозиторий рейсов недоступен")


async def _fetch_reference(
    cursor: Any,
    query: str,
    identifier: int,
    reference: str,
) -> Mapping[str, Any]:
    await cursor.execute(query, (identifier,))
    row = await cursor.fetchone()
    if row is None:
        raise TripReferenceNotFound(reference)  # type: ignore[arg-type]
    return row


def _is_duplicate_key(exc: Exception) -> bool:
    """Распознать только стандартное MySQL-нарушение уникального ключа 1062."""

    return bool(getattr(exc, "args", ())) and exc.args[0] == 1062


def _json_value(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _money(value: object) -> str:
    try:
        return format(Decimal(str(value)).quantize(Decimal("0.01")), ".2f")
    except (InvalidOperation, ValueError) as exc:
        raise TripStorageUnavailable("Некорректная цена в справочнике блоков") from exc


def _utc_datetime(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise TripStorageUnavailable("Некорректное время создания рейса")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _replayed_trip(value: object) -> TripResponse:
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise TripStorageUnavailable("Сохранённый ответ идемпотентности повреждён") from exc
    if not isinstance(value, Mapping):
        raise TripStorageUnavailable("Сохранённый ответ идемпотентности повреждён")
    try:
        return TripCreateResponse.model_validate(value).trip
    except Exception as exc:
        raise TripStorageUnavailable("Сохранённый ответ идемпотентности повреждён") from exc
