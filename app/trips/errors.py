"""Предметные ошибки создания рейса без деталей хранилища."""

from __future__ import annotations

from typing import Literal


class TripReferenceNotFound(RuntimeError):
    """Не найдена или неактивна запрошенная справочная запись."""

    def __init__(self, reference: Literal["vehicle", "carrier", "driver"]) -> None:
        self.reference = reference
        super().__init__(reference)


class TripBlockTypeNotFound(RuntimeError):
    """В утверждённом справочнике нет типа одного из допустимых блоков."""


class BlockAlreadyRegistered(RuntimeError):
    """Канонический паспорт блока уже занят."""


class IdempotencyKeyReused(RuntimeError):
    """Один ключ идемпотентности использован с другим отпечатком команды."""


class TripStorageUnavailable(RuntimeError):
    """Нельзя безопасно сохранить рейс в доступном хранилище."""
