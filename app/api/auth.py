"""Маршруты авторизации FastAPI-контура."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.auth.dependencies import get_current_user
from app.auth.models import AuthMeResponse, AuthenticatedUser, UserResponse
from app.auth.permissions import effective_permissions


router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.get("/me", response_model=AuthMeResponse, summary="Текущий пользователь и его права")
async def get_auth_me(
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
) -> AuthMeResponse:
    """Вернуть проверенную учётную запись без PIN, cookie и хэша сессии."""
    return AuthMeResponse(
        user=UserResponse(id=user.account_id, login=user.login, name=user.name, role=user.role),
        permissions=effective_permissions(user.role),
    )
