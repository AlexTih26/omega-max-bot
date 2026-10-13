"""Типы текущего пользователя и ответа маршрута авторизации."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """Проверенная активная учётная запись из MySQL `taksimo_new`."""

    account_id: int
    login: str | None
    name: str
    role: str


class UserResponse(BaseModel):
    """Безопасная публичная часть учётной записи."""

    model_config = ConfigDict(extra="forbid")

    id: int = Field(gt=0)
    login: str | None = None
    name: str = Field(min_length=1, max_length=120)
    role: str = Field(min_length=1, max_length=20)


class AuthMeResponse(BaseModel):
    """Ответ `GET /api/v1/auth/me`."""

    model_config = ConfigDict(extra="forbid")

    user: UserResponse
    permissions: dict[str, bool]
