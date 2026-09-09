"""PIN-вход в отдельный браузерный кабинет бухгалтеров РУМЕКС."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time

from aiohttp import web

from rumex_registry_store import (
    accountant_session_username,
    create_accountant_session,
    failed_accountant_login_count,
    record_accountant_login_attempt,
    revoke_accountant_session,
)

logger = logging.getLogger(__name__)

COOKIE_NAME = "rumex_accountant_auth"
USER_KEY = "rumex_accountant_user"
SESSION_DAYS = 7
PIN_LENGTH = 5
MAX_FAILED_ATTEMPTS = 5
ATTEMPT_WINDOW_SECONDS = 15 * 60
ACCOUNTANT_NAMES = ("Бухгалтер 1", "Бухгалтер 2", "Бухгалтер 3")
REGISTRY_ADMIN_NAME = "Бухгалтер 1"


def _secret() -> str:
    """Секрет независим от MAX и обязателен для включения PIN-входа."""
    return (os.getenv("RUMEX_ACCOUNTANT_AUTH_SECRET") or "").strip()


def _parse_user_chunk(chunk: str) -> tuple[str, str] | None:
    name, sep, pin = chunk.strip().partition(":")
    if not sep:
        return None
    name = name.strip()
    pin = pin.strip()
    if name not in ACCOUNTANT_NAMES or len(pin) != PIN_LENGTH or not pin.isdigit():
        return None
    return name, pin


def load_users() -> dict[str, str]:
    """Получить все три учётные записи только из окружения, без дефолтных PIN."""
    users: dict[str, str] = {}
    for chunk in (os.getenv("RUMEX_ACCOUNTANT_USERS") or "").split(","):
        parsed = _parse_user_chunk(chunk)
        if parsed is not None:
            name, pin = parsed
            users[name] = pin
    return users


def auth_enabled() -> bool:
    """Вход включается лишь при корректной настройке ровно трёх PIN и секрета."""
    return bool(_secret()) and set(load_users()) == set(ACCOUNTANT_NAMES)


def verify_pin(pin: str) -> str | None:
    """Сопоставить PIN с учётной записью, не раскрывая имя при неудаче."""
    candidate = (pin or "").strip()
    if len(candidate) != PIN_LENGTH or not candidate.isdigit() or not auth_enabled():
        return None
    for name, expected in load_users().items():
        if hmac.compare_digest(candidate, expected):
            return name
    return None


def is_registry_admin(username: str | None) -> bool:
    """Администратор справочника задан явно, а не клиентским признаком роли."""
    return username == REGISTRY_ADMIN_NAME


def _token_hash(token: str) -> str:
    return hmac.new(
        _secret().encode("utf-8"),
        token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _remote_address(request: web.Request) -> str:
    return (request.remote or "").strip()


def _user_agent(request: web.Request) -> str:
    return (request.headers.get("User-Agent") or "").strip()


def user_from_request(request: web.Request) -> str | None:
    cached = request.get(USER_KEY)
    if isinstance(cached, str):
        return cached
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    username = accountant_session_username(token_hash=_token_hash(token))
    if username:
        request[USER_KEY] = username
    return username


def _json(data: dict, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


@web.middleware
async def rumex_registry_auth_middleware(request: web.Request, handler):
    """Не допускать неавторизованные запросы к новому реестру.

    Старые `/api/rumex/...` маршруты намеренно не входят в этот контур.
    """
    path = request.path
    if not path.startswith("/api/rumex-registry"):
        return await handler(request)
    # Тестовый кабинет диспетчера проверяет подписанный initData MAX в своих
    # обработчиках. Нельзя требовать PIN бухгалтера от отдельной роли, но и
    # нельзя оставлять маршруты без их собственной серверной проверки.
    if path.startswith("/api/rumex-registry/test/"):
        return await handler(request)
    if path in {
        "/api/rumex-registry/auth/check",
        "/api/rumex-registry/auth/me",
    }:
        return await handler(request)
    if path == "/api/rumex-registry/auth" and request.method == "POST":
        return await handler(request)
    if not auth_enabled():
        return _json({"error": "PIN-доступ бухгалтеров не настроен"}, 503)
    if user_from_request(request):
        return await handler(request)
    return _json({"error": "Требуется вход", "login": "/rumex-accountant-login.html"}, 401)


async def handle_auth_check(request: web.Request) -> web.Response:
    if not auth_enabled():
        return _json({"configured": False}, 503)
    return _json({"configured": True, "authenticated": bool(user_from_request(request))})


async def handle_auth_me(request: web.Request) -> web.Response:
    if not auth_enabled():
        return _json({"configured": False}, 503)
    username = user_from_request(request)
    if not username:
        return _json({"error": "Требуется вход"}, 401)
    admin = is_registry_admin(username)
    return _json(
        {
            "configured": True,
            "user": username,
            "role": "admin" if admin else "accountant",
            "can_manage_carriers": admin,
        }
    )


async def handle_auth_login(request: web.Request) -> web.Response:
    remote = _remote_address(request)
    agent = _user_agent(request)
    if not auth_enabled():
        record_accountant_login_attempt(
            success=False,
            failure_reason="not_configured",
            remote_address=remote,
            user_agent=agent,
        )
        return _json({"error": "PIN-доступ бухгалтеров не настроен"}, 503)

    if failed_accountant_login_count(
        remote_address=remote,
        since=time.time() - ATTEMPT_WINDOW_SECONDS,
    ) >= MAX_FAILED_ATTEMPTS:
        return _json({"error": "Слишком много неверных попыток. Попробуйте позже."}, 429)
    try:
        body = await request.json()
    except Exception:
        return _json({"error": "Некорректный запрос"}, 400)

    username = verify_pin(str(body.get("pin") or ""))
    if not username:
        record_accountant_login_attempt(
            success=False,
            failure_reason="invalid_pin",
            remote_address=remote,
            user_agent=agent,
        )
        return _json({"error": "Неверный PIN"}, 401)

    token = secrets.token_urlsafe(32)
    expires_at = time.time() + SESSION_DAYS * 86400
    create_accountant_session(
        token_hash=_token_hash(token),
        username=username,
        expires_at=expires_at,
        remote_address=remote,
        user_agent=agent,
    )
    record_accountant_login_attempt(
        username=username,
        success=True,
        remote_address=remote,
        user_agent=agent,
    )
    admin = is_registry_admin(username)
    response = _json(
        {
            "ok": True,
            "user": username,
            "role": "admin" if admin else "accountant",
            "can_manage_carriers": admin,
        }
    )
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_DAYS * 86400,
        httponly=True,
        secure=True,
        samesite="Lax",
        path="/",
    )
    logger.info("РУМЕКС реестр: вход бухгалтера %s", username)
    return response


async def handle_auth_logout(request: web.Request) -> web.Response:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        revoke_accountant_session(token_hash=_token_hash(token))
    response = _json({"ok": True})
    response.del_cookie(COOKIE_NAME, path="/")
    return response


def register_rumex_registry_auth_routes(app: web.Application) -> None:
    app.router.add_get("/api/rumex-registry/auth/check", handle_auth_check)
    app.router.add_get("/api/rumex-registry/auth/me", handle_auth_me)
    app.router.add_post("/api/rumex-registry/auth", handle_auth_login)
    app.router.add_post("/api/rumex-registry/auth/logout", handle_auth_logout)
    if auth_enabled():
        logger.info("РУМЕКС реестр: PIN-доступ бухгалтеров настроен для 3 учётных записей")
    else:
        logger.warning("РУМЕКС реестр: PIN-доступ бухгалтеров не настроен и закрыт")
