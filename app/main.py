"""ASGI-вход корневого FastAPI-контура РУМЕКС / Таксимо."""

from __future__ import annotations

from contextlib import asynccontextmanager
import inspect
from typing import AsyncIterator

from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.core.settings import Settings
from app.repositories.sessions import AsyncMySessionRepository, SessionRepository


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Освободить ресурсы собственного FastAPI-контура при остановке процесса."""
    yield
    close = getattr(app.state.session_repository, "close", None)
    if callable(close):
        result = close()
        if inspect.isawaitable(result):
            await result


def create_app(
    *,
    settings: Settings | None = None,
    session_repository: SessionRepository | None = None,
) -> FastAPI:
    """Собрать минимальное ASGI-приложение без запуска legacy `aiohttp`."""
    resolved_settings = settings or Settings.from_environment()
    app = FastAPI(title="РУМЕКС / Таксимо API", version="0.1.0", lifespan=lifespan)
    app.state.settings = resolved_settings
    app.state.session_repository = session_repository or AsyncMySessionRepository(resolved_settings)
    app.include_router(auth_router)
    return app


app = create_app()
