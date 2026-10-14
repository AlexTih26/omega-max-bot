"""HTTP-роутер атомарного создания рейсов диспетчером."""

from __future__ import annotations

import json
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.auth.dependencies import require_permission
from app.auth.models import AuthenticatedUser
from app.trips.contracts import (
    CreateTripRequest,
    ErrorDetailsResponse,
    ErrorResponse,
    TripCreateResponse,
    ValidationErrorFieldResponse,
)
from app.trips.service import (
    BlockAlreadyRegistered,
    IdempotencyKeyReused,
    TripBlockTypeNotFound,
    TripReferenceNotFound,
    TripService,
    TripStorageUnavailable,
)


TRUSTED_ORIGIN = "https://avtmsk.ru"


async def require_trusted_origin(request: Request) -> None:
    """Разрешить изменяющую команду только из утверждённого browser-origin."""

    if request.headers.get("origin") != TRUSTED_ORIGIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Источник запроса не разрешён")


def create_dispatcher_trips_router(service: TripService) -> APIRouter:
    """Создать роутер опубликованного маршрута создания рейса."""

    router = APIRouter(
        prefix="/api/v1/dispatcher",
        tags=["dispatcher"],
        dependencies=[Depends(require_trusted_origin)],
    )

    @router.post(
        "/trips",
        status_code=status.HTTP_201_CREATED,
        response_model=TripCreateResponse,
        summary="Создать рейс диспетчера",
        responses={
            status.HTTP_200_OK: {"model": TripCreateResponse, "description": "Повтор сохранённой команды"},
            status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse, "description": "Некорректный JSON или ключ идемпотентности"},
            status.HTTP_403_FORBIDDEN: {"description": "Неверный источник запроса"},
            status.HTTP_404_NOT_FOUND: {"model": ErrorResponse, "description": "Справочник не найден"},
            status.HTTP_409_CONFLICT: {"model": ErrorResponse, "description": "Конфликт блока или идемпотентности"},
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: {"model": ErrorResponse, "description": "Ожидается JSON"},
            status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse, "description": "Неверные поля запроса"},
            status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse, "description": "Хранилище временно недоступно"},
        },
    )
    async def create_trip(
        request: Request,
        actor: Annotated[AuthenticatedUser, Depends(require_permission("trip.create", "edit"))],
    ) -> JSONResponse:
        """Вручную проверить HTTP-границу до вызова прикладного сервиса."""
        if not _is_json_content_type(request.headers.get("content-type")):
            return _error_response(
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                "unsupported_media_type",
                "Ожидается Content-Type application/json",
            )

        idempotency_key = _parse_idempotency_key(request.headers.get("idempotency-key"))
        if idempotency_key is None:
            return _error_response(
                status.HTTP_400_BAD_REQUEST,
                "invalid_idempotency_key",
                "Заголовок Idempotency-Key должен содержать канонический UUID",
            )

        try:
            payload = json.loads(await request.body(), parse_constant=_reject_json_constant)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return _error_response(
                status.HTTP_400_BAD_REQUEST,
                "invalid_json",
                "Тело запроса должно быть JSON-объектом",
            )
        if not isinstance(payload, dict):
            return _error_response(
                status.HTTP_400_BAD_REQUEST,
                "invalid_json",
                "Тело запроса должно быть JSON-объектом",
            )

        try:
            create_request = CreateTripRequest.model_validate(payload)
        except ValidationError as exc:
            return _validation_error_response(exc)

        try:
            result = await service.create_trip(
                request=create_request,
                actor=actor,
                idempotency_key=idempotency_key,
                remote_address=request.client.host if request.client is not None else "",
            )
        except TripReferenceNotFound as exc:
            code, message = {
                "vehicle": ("vehicle_not_found", "Машина не найдена"),
                "carrier": ("carrier_not_found", "Перевозчик не найден"),
                "driver": ("driver_not_found", "Водитель не найден"),
            }[exc.reference]
            return _error_response(status.HTTP_404_NOT_FOUND, code, message)
        except BlockAlreadyRegistered:
            return _error_response(
                status.HTTP_409_CONFLICT,
                "block_already_registered",
                "Паспорт блока уже зарегистрирован",
            )
        except TripBlockTypeNotFound:
            return _error_response(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "validation_error",
                "Проверьте поля запроса",
                fields=[ValidationErrorFieldResponse(field="blocks", code="block_type_not_found")],
            )
        except IdempotencyKeyReused:
            return _error_response(
                status.HTTP_409_CONFLICT,
                "idempotency_key_reused",
                "Ключ идемпотентности уже использован с другим запросом",
            )
        except TripStorageUnavailable:
            return _error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "storage_unavailable",
                "Хранилище рейсов временно недоступно",
            )

        response = TripCreateResponse(trip=result.trip)
        response_status = status.HTTP_200_OK if result.replayed else status.HTTP_201_CREATED
        return JSONResponse(status_code=response_status, content=response.model_dump(mode="json"))

    return router


def _is_json_content_type(content_type: str | None) -> bool:
    """Принять `application/json` с допустимыми стандартными параметрами."""

    if content_type is None:
        return False
    media_type = content_type.split(";", maxsplit=1)[0].strip().lower()
    return media_type == "application/json"


def _parse_idempotency_key(value: str | None) -> UUID | None:
    """Принять только строковое каноническое нижнерегистровое UUID-представление."""

    if value is None:
        return None
    try:
        parsed = UUID(value)
    except ValueError:
        return None
    return parsed if str(parsed) == value else None


def _reject_json_constant(value: str) -> Any:
    """Не принимать не-JSON константы `NaN`, `Infinity` и `-Infinity`."""

    raise ValueError(f"Invalid JSON constant: {value}")


def _validation_error_response(exc: ValidationError) -> JSONResponse:
    """Скрыть все значения и сообщения Pydantic, оставив стабильный код поля."""

    fields = [
        ValidationErrorFieldResponse(
            field=_format_error_location(error["loc"]),
            code=_validation_error_code(error["type"]),
        )
        for error in exc.errors()
    ]
    return _error_response(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "validation_error",
        "Проверьте поля запроса",
        fields=fields,
    )


def _format_error_location(location: tuple[Any, ...]) -> str:
    """Преобразовать Pydantic location в стабильный путь поля контракта."""

    if not location:
        return "blocks"
    result = ""
    for item in location:
        if isinstance(item, int):
            result += f"[{item}]"
        elif result:
            result += f".{item}"
        else:
            result = str(item)
    return result


def _validation_error_code(error_type: str) -> str:
    """Свести детали Pydantic к компактным и документированным кодам."""

    if error_type == "extra_forbidden":
        return "unknown_field"
    if error_type == "missing":
        return "required"
    if error_type == "duplicate_block":
        return "duplicate"
    if error_type.endswith("_type"):
        return "invalid_type"
    return "invalid_value"


def _error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    fields: list[ValidationErrorFieldResponse] | None = None,
) -> JSONResponse:
    """Сформировать закрытый конверт ошибки без данных пользовательского ввода."""

    body = ErrorResponse(error=ErrorDetailsResponse(code=code, message=message, fields=fields))
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json", exclude_none=True))
