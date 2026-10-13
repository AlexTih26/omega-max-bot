"""FastAPI dependencies для проверки cookie новой Таксимо."""

from __future__ import annotations

import hashlib
import hmac
import logging
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.auth.models import AuthenticatedUser
from app.auth.permissions import is_permission_granted, is_supported_permission
from app.core.settings import Settings
from app.repositories.sessions import SessionRepository, SessionRepositoryUnavailable


logger = logging.getLogger(__name__)


def session_token_hash(token: str, secret: str) -> str:
    """Получить HMAC-хэш cookie тем же алгоритмом, что и новая PWA Таксимо."""
    return hmac.new(secret.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_session_repository(request: Request) -> SessionRepository:
    return request.app.state.session_repository


async def get_current_user(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    sessions: Annotated[SessionRepository, Depends(get_session_repository)],
) -> AuthenticatedUser:
    """Проверить cookie и вернуть активную учётную запись без раскрытия токена."""
    token = request.cookies.get(settings.auth_cookie_name)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется вход")
    if not settings.auth_secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Сервис авторизации недоступен")
    try:
        user = await sessions.find_active_user(session_token_hash(token, settings.auth_secret))
    except SessionRepositoryUnavailable:
        logger.warning("Не удалось проверить сессию FastAPI-контура")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Сервис авторизации недоступен") from None
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется вход")
    return user


PermissionDependency = Callable[..., Awaitable[AuthenticatedUser]]


def require_permission(capability: str, level: str) -> PermissionDependency:
    """Потребовать подтверждённую capability через временную ролевую матрицу.

    Точечные grants и overrides появятся только после отдельной миграции `014+`.
    Неизвестная capability не получает молчаливого доступа.
    """
    if not is_supported_permission(capability, level):
        raise ValueError(f"Для {capability!r}/{level!r} нет настроенного правила доступа")

    async def check_permission(
        user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    ) -> AuthenticatedUser:
        if not is_permission_granted(user.role, capability, level):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Недостаточно прав")
        return user

    return check_permission
