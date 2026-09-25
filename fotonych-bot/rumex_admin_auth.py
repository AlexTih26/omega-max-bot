"""Авторизация отдельного кабинета управления РУМЕКС.

MAX-подпись служит только для начального входа супер-администратора. После неё
кабинет работает по отдельной серверной cookie-сессии и не использует сессии
бухгалтеров, диспетчеров или Таксимо.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time

from aiohttp import web

from max_webapp import display_name_from_user, user_id_from_user, validate_init_data
from rumex_registry_store import (
    create_rumex_admin_session,
    revoke_rumex_admin_session,
    rumex_admin_access_by_max_user_id,
    rumex_admin_session_identity,
)
from super_admin import is_super_admin

COOKIE_NAME = "rumex_management_auth"
USER_KEY = "rumex_management_user"
SESSION_HOURS = 8


def _secret() -> str:
    """Секрет сессий; отдельно от всех остальных контуров."""
    return (os.getenv("RUMEX_ADMIN_AUTH_SECRET") or "").strip()


def _token_hash(token: str) -> str:
    return hmac.new(_secret().encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()


def _user_from_max(request: web.Request) -> tuple[dict | None, int | None]:
    init_data = (request.headers.get("X-Max-Init-Data") or "").strip()
    parsed = validate_init_data(init_data, os.getenv("MAX_BOT_TOKEN", "")) if init_data else None
    user = parsed.get("user") if isinstance(parsed, dict) else None
    return (user, user_id_from_user(user)) if isinstance(user, dict) else (None, None)


def user_from_request(request: web.Request) -> dict | None:
    cached = request.get(USER_KEY)
    if isinstance(cached, dict):
        return cached
    token = request.cookies.get(COOKIE_NAME)
    if not token or not _secret():
        return None
    identity = rumex_admin_session_identity(
        token_hash=_token_hash(token), renewal_seconds=SESSION_HOURS * 3600
    )
    if identity:
        request[USER_KEY] = identity
    return identity


@web.middleware
async def rumex_admin_auth_middleware(request: web.Request, handler):
    if not request.path.startswith("/api/rumex-management/"):
        return await handler(request)
    response = await handler(request)
    token = request.cookies.get(COOKIE_NAME)
    if token and user_from_request(request):
        response.set_cookie(
            COOKIE_NAME, token, max_age=SESSION_HOURS * 3600, httponly=True,
            secure=True, samesite="Lax", path="/",
        )
    return response


async def handle_auth_check(request: web.Request) -> web.Response:
    return web.json_response({"configured": bool(_secret()), "authenticated": bool(user_from_request(request))})


async def handle_auth_login(request: web.Request) -> web.Response:
    if not _secret():
        return web.json_response({"error": "Доступ управления РУМЕКС не настроен"}, status=503)
    user, max_user_id = _user_from_max(request)
    if user is None or max_user_id is None:
        return web.json_response({"error": "Откройте кабинет из MAX"}, status=401)
    delegated = rumex_admin_access_by_max_user_id(max_user_id)
    if not is_super_admin(max_user_id) and delegated is None:
        return web.json_response({"error": "Доступ разрешён только супер-администратору"}, status=403)
    token = secrets.token_urlsafe(32)
    name = str(delegated["full_name"]) if delegated is not None else display_name_from_user(user)
    create_rumex_admin_session(
        token_hash=_token_hash(token), username=name, max_user_id=max_user_id,
        expires_at=time.time() + SESSION_HOURS * 3600, remote_address=request.remote or "",
        user_agent=request.headers.get("User-Agent") or "",
    )
    response = web.json_response({"ok": True, "user": name})
    response.set_cookie(COOKIE_NAME, token, max_age=SESSION_HOURS * 3600, httponly=True,
                        secure=True, samesite="Lax", path="/")
    return response


async def handle_auth_logout(request: web.Request) -> web.Response:
    token = request.cookies.get(COOKIE_NAME)
    if token and _secret():
        revoke_rumex_admin_session(token_hash=_token_hash(token))
    response = web.json_response({"ok": True})
    response.del_cookie(COOKIE_NAME, path="/")
    return response


def register_rumex_admin_auth_routes(app: web.Application) -> None:
    app.router.add_get("/api/rumex-management/auth/check", handle_auth_check)
    app.router.add_post("/api/rumex-management/auth", handle_auth_login)
    app.router.add_post("/api/rumex-management/auth/logout", handle_auth_logout)
