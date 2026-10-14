"""ASGI-вход корневого FastAPI-контура РУМЕКС / Таксимо."""

from __future__ import annotations

from contextlib import asynccontextmanager
import inspect
from typing import AsyncIterator

from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.api.dispatcher_trips import create_dispatcher_trips_router
from app.core.settings import Settings
from app.repositories.permissions import AsyncMyPermissionRepository, PermissionRepository
from app.repositories.sessions import AsyncMySessionRepository, SessionRepository
from app.repositories.trips import AsyncMyTripRepository, TripRepository
from app.trips.service import RepositoryTripService


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Освободить ресурсы собственного FastAPI-контура при остановке процесса."""
    yield
    for repository_name in ("session_repository", "permission_repository", "trip_repository"):
        close = getattr(getattr(app.state, repository_name), "close", None)
        if callable(close):
            result = close()
            if inspect.isawaitable(result):
                await result


def create_app(
    *,
    settings: Settings | None = None,
    session_repository: SessionRepository | None = None,
    permission_repository: PermissionRepository | None = None,
    trip_repository: TripRepository | None = None,
) -> FastAPI:
    """Собрать FastAPI-приложение без запуска legacy `aiohttp`."""
    resolved_settings = settings or Settings.from_environment()
    app = FastAPI(title="РУМЕКС / Таксимо API", version="0.1.0", lifespan=lifespan)
    app.state.settings = resolved_settings
    app.state.session_repository = session_repository or AsyncMySessionRepository(resolved_settings)
    app.state.permission_repository = permission_repository or AsyncMyPermissionRepository(resolved_settings)
    resolved_trip_repository = trip_repository or AsyncMyTripRepository(resolved_settings)
    app.state.trip_repository = resolved_trip_repository
    app.include_router(auth_router)
    app.include_router(create_dispatcher_trips_router(RepositoryTripService(resolved_trip_repository)))
    return app


app = create_app()
