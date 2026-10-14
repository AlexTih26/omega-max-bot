"""Непривилегированные liveness- и readiness-проверки FastAPI-контура."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from app.auth.dependencies import get_session_repository, get_settings
from app.core.settings import Settings
from app.repositories.sessions import SessionRepository, SessionRepositoryUnavailable


logger = logging.getLogger(__name__)


def create_health_router() -> APIRouter:
    """Создать служебные маршруты без доступа к пользовательским данным."""

    router = APIRouter(prefix="/api/v1", tags=["system"], include_in_schema=False)

    @router.get("/healthz")
    async def healthz() -> dict[str, str]:
        """Подтвердить, что ASGI-процесс принимает запросы."""

        return {"status": "ok"}

    @router.get("/readyz")
    async def readyz(
        settings: Annotated[Settings, Depends(get_settings)],
        sessions: Annotated[SessionRepository, Depends(get_session_repository)],
    ) -> JSONResponse:
        """Подтвердить конфигурацию и read-only доступ к MySQL."""

        if not _is_configured(settings):
            return _not_ready()
        try:
            await sessions.check_ready()
        except SessionRepositoryUnavailable:
            logger.warning("Readiness-проверка FastAPI-контура не прошла")
            return _not_ready()
        return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "ready"})

    return router


def _is_configured(settings: Settings) -> bool:
    return bool(
        settings.auth_secret
        and settings.mysql_host
        and settings.mysql_port
        and settings.mysql_user
        and settings.mysql_password
        and settings.mysql_database
    )


def _not_ready() -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "unavailable"},
    )
