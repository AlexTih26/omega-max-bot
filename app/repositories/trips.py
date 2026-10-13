"""Граница атомарного хранилища создания рейса без реализации SQL до `016`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.auth.models import AuthenticatedUser
from app.trips.contracts import CreateTripRequest, TripResponse


@dataclass(frozen=True, slots=True)
class CreateTripCommand:
    """Нормализованная команда, которую репозиторий обязан сохранить атомарно."""

    actor: AuthenticatedUser
    idempotency_key: UUID
    fingerprint: str
    request: CreateTripRequest


@dataclass(frozen=True, slots=True)
class TripCreationResult:
    """Сохранённый публичный ответ и признак безопасного повтора."""

    trip: TripResponse
    replayed: bool


class TripRepository(Protocol):
    """Граница одной транзакции создания рейса, паспортов и идемпотентности."""

    async def create_trip(self, command: CreateTripCommand) -> TripCreationResult:
        """Атомарно создать рейс либо вернуть сохранённый результат повтора."""


class NotImplementedTripRepository:
    """Production-looking заглушка, допустимая до появления миграции `016`."""

    async def create_trip(self, command: CreateTripCommand) -> TripCreationResult:
        raise NotImplementedError("Репозиторий рейсов недоступен до миграции 016")
