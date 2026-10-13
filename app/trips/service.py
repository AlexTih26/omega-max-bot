"""Сервис создания рейса и безопасные ошибки его предметной области."""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Literal, Protocol
from uuid import UUID

from app.auth.models import AuthenticatedUser
from app.repositories.trips import CreateTripCommand, TripCreationResult, TripRepository
from app.trips.contracts import CreateTripRequest


class TripReferenceNotFound(RuntimeError):
    """Не найдена или неактивна запрошенная справочная запись."""

    def __init__(self, reference: Literal["vehicle", "carrier", "driver"]) -> None:
        self.reference = reference
        super().__init__(reference)


class BlockAlreadyRegistered(RuntimeError):
    """Канонический паспорт блока уже занят."""


class IdempotencyKeyReused(RuntimeError):
    """Один ключ идемпотентности использован с другим отпечатком команды."""


class TripStorageUnavailable(RuntimeError):
    """Нельзя безопасно сохранить рейс в доступном хранилище."""


class TripService(Protocol):
    """Прикладная граница, используемая HTTP-роутером."""

    async def create_trip(
        self,
        *,
        request: CreateTripRequest,
        actor: AuthenticatedUser,
        idempotency_key: UUID,
    ) -> TripCreationResult:
        """Создать рейс или безопасно воспроизвести сохранённый ответ."""


class RepositoryTripService:
    """Собирает детерминированную команду и передаёт её атомарному репозиторию."""

    def __init__(self, repository: TripRepository) -> None:
        self._repository = repository

    async def create_trip(
        self,
        *,
        request: CreateTripRequest,
        actor: AuthenticatedUser,
        idempotency_key: UUID,
    ) -> TripCreationResult:
        command = CreateTripCommand(
            actor=actor,
            idempotency_key=idempotency_key,
            fingerprint=_request_fingerprint(request),
            request=request,
        )
        try:
            return await self._repository.create_trip(command)
        except NotImplementedError:
            raise TripStorageUnavailable() from None


def _request_fingerprint(request: CreateTripRequest) -> str:
    """Вернуть SHA-256 канонического JSON уже нормализованной команды."""

    canonical_json = json.dumps(
        request.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical_json.encode("utf-8")).hexdigest()
