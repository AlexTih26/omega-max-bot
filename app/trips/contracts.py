"""Закрытые HTTP-контракты создания рейса диспетчером."""

from __future__ import annotations

from datetime import date, datetime
import re
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator
from pydantic_core import PydanticCustomError


class _ClosedModel(BaseModel):
    """Базовая модель закрытого контракта без неявных полей."""

    model_config = ConfigDict(extra="forbid")


class CreateTripBlock(_ClosedModel):
    """Канонизируемый блок из команды создания рейса."""

    letter: str
    number: Annotated[str, Field(min_length=1, max_length=40)]
    suffix: Literal["", "к", "а", "тк", "скол"] = ""

    @field_validator("letter", mode="before")
    @classmethod
    def normalize_letter(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("letter must be a string")
        normalized = value.upper()
        if normalized not in {"A", "B", "C", "D", "E", "F", "K"}:
            raise ValueError("letter is not allowed")
        return normalized

    @field_validator("number", mode="before")
    @classmethod
    def normalize_number(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("number must be a string")
        return "".join(value.split()).upper()


class CreateTripRequest(_ClosedModel):
    """Полностью заполненная команда создания рейса без client-side snapshots."""

    trip_date: date
    vehicle_id: Annotated[StrictInt, Field(gt=0)]
    carrier_id: Annotated[StrictInt, Field(gt=0)]
    driver_id: Annotated[StrictInt, Field(gt=0)]
    blocks: Annotated[list[CreateTripBlock], Field(min_length=1, max_length=16)]

    @field_validator("trip_date", mode="before")
    @classmethod
    def require_iso_date_only(cls, value: object) -> object:
        if not isinstance(value, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
            raise ValueError("trip_date must be an ISO date")
        return value

    @model_validator(mode="after")
    def reject_duplicate_normalized_blocks(self) -> "CreateTripRequest":
        keys = {(block.letter, block.number) for block in self.blocks}
        if len(keys) != len(self.blocks):
            raise PydanticCustomError("duplicate_block", "duplicate normalized block")
        return self


class TripVehicleResponse(_ClosedModel):
    """Снимок машины в публичном ответе."""

    id: Annotated[StrictInt, Field(gt=0)]
    plate: Annotated[str, Field(min_length=1, max_length=32)]


class TripCarrierResponse(_ClosedModel):
    """Снимок перевозчика в публичном ответе."""

    id: Annotated[StrictInt, Field(gt=0)]
    legal_form: Annotated[str, Field(min_length=1, max_length=32)]
    name: Annotated[str, Field(min_length=1, max_length=255)]
    inn: Annotated[str, Field(min_length=1, max_length=20)]


class TripDriverResponse(_ClosedModel):
    """Снимок водителя в публичном ответе."""

    id: Annotated[StrictInt, Field(gt=0)]
    full_name: Annotated[str, Field(min_length=1, max_length=255)]
    license_number: Annotated[str, Field(min_length=1, max_length=64)]


class TripBlockResponse(_ClosedModel):
    """Снимок блока в публичном ответе."""

    letter: Literal["A", "B", "C", "D", "E", "F", "K"]
    number: Annotated[str, Field(min_length=1, max_length=40)]
    suffix: Literal["", "к", "а", "тк", "скол"]
    weight_kg: Annotated[StrictInt, Field(ge=0)]
    price_rub: Annotated[str, Field(pattern=r"^-?\d+\.\d{2}$")]


class TripResponse(_ClosedModel):
    """Публичный снимок только что созданного рейса."""

    public_id: UUID
    status: Literal["draft"]
    trip_date: date
    vehicle: TripVehicleResponse
    carrier: TripCarrierResponse
    driver: TripDriverResponse
    block_count: Annotated[StrictInt, Field(ge=1, le=16)]
    total_weight_kg: Annotated[StrictInt, Field(ge=0)]
    blocks: Annotated[list[TripBlockResponse], Field(min_length=1, max_length=16)]
    created_at: datetime


class TripCreateResponse(_ClosedModel):
    """Успешный ответ создания или повтора создания рейса."""

    trip: TripResponse


class ValidationErrorFieldResponse(_ClosedModel):
    """Безопасная ссылка на одно поле, не отражающая входное значение."""

    field: str
    code: str


class ErrorDetailsResponse(_ClosedModel):
    """Безопасная ошибка нового HTTP-контура."""

    code: str
    message: str
    fields: list[ValidationErrorFieldResponse] | None = None


class ErrorResponse(_ClosedModel):
    """Конверт безопасных ошибок, которыми владеет новый роутер."""

    error: ErrorDetailsResponse
