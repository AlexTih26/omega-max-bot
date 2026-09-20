"""Отдельный парольный вход в изолированный тестовый кабинет РУМЕКС."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time

from aiohttp import web

from rumex_registry_store import (
    create_test_dispatcher_session,
    failed_test_dispatcher_login_count,
    get_rumex_dispatcher_account,
    record_test_dispatcher_login_attempt,
    rumex_dispatcher_account_count,
    revoke_test_dispatcher_session,
    test_dispatcher_session_username,
    touch_rumex_dispatcher_activity,
)

logger = logging.getLogger(__name__)

COOKIE_NAME = "rumex_test_dispatcher_auth"
USER_KEY = "rumex_test_dispatcher_user"
SESSION_HOURS = 12
MAX_FAILED_ATTEMPTS = 5
ATTEMPT_WINDOW_SECONDS = 15 * 60


def _secret() -> str:
    """Вернуть независимый от MAX и бухгалтеров секрет парольных сессий."""
    return (os.getenv("RUMEX_TEST_DISPATCHER_AUTH_SECRET") or "").strip()


def _parse_user_chunk(chunk: str) -> tuple[str, str] | None:
    username, separator, password = chunk.strip().partition(":")
    username = username.strip()
    password = password.strip()
    if not separator or not (2 <= len(username) <= 80) or not (8 <= len(password) <= 256):
        return None
    return username, password


def load_users() -> dict[str, str]:
    """Получить парольные учётные записи только из окружения сервера."""
    users: dict[str, str] = {}
    for chunk in (os.getenv("RUMEX_TEST_DISPATCHER_USERS") or "").split(","):
        parsed = _parse_user_chunk(chunk)
        if parsed is not None:
            username, password = parsed
            users[username] = password
    return users


def auth_enabled() -> bool:
    """Парольный вход доступен лишь с отдельным секретом и хотя бы одной учётной записью."""
    return bool(_secret()) and bool(load_users() or rumex_dispatcher_account_count())


def verify_credentials(username: str, password: str) -> str | None:
    """Проверить учётные данные без раскрытия существования пользователя."""
    if not auth_enabled():
        return None
    candidate_name = (username or "").strip()
    candidate_password = (password or "").strip()
    account = get_rumex_dispatcher_account(candidate_name, include_hash=True)
    if account is not None:
        expected_hash = str(account.get("password_hash") or "")
        try:
            salt, expected = expected_hash.split("$", 1)
            actual = hashlib.scrypt(candidate_password.encode("utf-8"), salt=salt.encode("ascii"), n=2**14, r=8, p=1).hex()
        except (ValueError, UnicodeEncodeError):
            return None
        if not account.get("active") or not hmac.compare_digest(actual, expected):
            return None
        return str(account["username"])
    if rumex_dispatcher_account_count():
        return None
    expected = load_users().get(candidate_name)
    if not expected or not hmac.compare_digest(candidate_password, expected):
        return None
    return candidate_name


def _token_hash(token: str) -> str:
    return hmac.new(
        _secret().encode("utf-8"), token.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _remote_address(request: web.Request) -> str:
    return (request.remote or "").strip()


def _user_agent(request: web.Request) -> str:
    return (request.headers.get("User-Agent") or "").strip()


def user_from_request(request: web.Request) -> str | None:
    """Вернуть пользователя отдельной парольной сессии, если она действительна."""
    cached = request.get(USER_KEY)
    if isinstance(cached, str):
        return cached
    token = request.cookies.get(COOKIE_NAME)
    if not token or not auth_enabled():
        return None
    username = test_dispatcher_session_username(
        token_hash=_token_hash(token), renewal_seconds=SESSION_HOURS * 3600
    )
    account = get_rumex_dispatcher_account(username) if username else None
    if account is not None and not account.get("active"):
        revoke_test_dispatcher_session(token_hash=_token_hash(token))
        return None
    if username:
        touch_rumex_dispatcher_activity(username)
        request[USER_KEY] = username
    return username


def _json(data: dict, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _renew_session_cookie(request: web.Request, response: web.StreamResponse) -> web.StreamResponse:
    """Продлить cookie только для действующей парольной сессии диспетчера."""
    token = request.cookies.get(COOKIE_NAME)
    if token and isinstance(request.get(USER_KEY), str):
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=SESSION_HOURS * 3600,
            httponly=True,
            secure=True,
            samesite="Lax",
            path="/",
        )
    return response


@web.middleware
async def rumex_test_dispatcher_auth_middleware(request: web.Request, handler):
    """Обновить срок парольной сессии на успешных запросах тестового диспетчера."""
    if not request.path.startswith("/api/rumex-registry/test/"):
        return await handler(request)
    if request.path == "/api/rumex-registry/test/auth/logout":
        return await handler(request)
    return _renew_session_cookie(request, await handler(request))


async def handle_auth_check(request: web.Request) -> web.Response:
    configured = auth_enabled()
    return _json(
        {
            "configured": configured,
            "authenticated": bool(user_from_request(request)) if configured else False,
        }
    )


async def handle_auth_me(request: web.Request) -> web.Response:
    if not auth_enabled():
        return _json({"configured": False}, 503)
    username = user_from_request(request)
    if not username:
        return _json({"error": "Требуется вход"}, 401)
    return _json({"configured": True, "user": username})


async def handle_auth_login(request: web.Request) -> web.Response:
    remote = _remote_address(request)
    agent = _user_agent(request)
    if not auth_enabled():
        record_test_dispatcher_login_attempt(
            success=False,
            failure_reason="not_configured",
            remote_address=remote,
            user_agent=agent,
        )
        return _json({"error": "Парольный доступ тестового диспетчера не настроен"}, 503)
    if failed_test_dispatcher_login_count(
        remote_address=remote, since=time.time() - ATTEMPT_WINDOW_SECONDS
    ) >= MAX_FAILED_ATTEMPTS:
        return _json({"error": "Слишком много неверных попыток. Попробуйте позже."}, 429)
    try:
        body = await request.json()
    except Exception:
        return _json({"error": "Некорректный запрос"}, 400)
    if not isinstance(body, dict):
        return _json({"error": "Некорректный запрос"}, 400)
    supplied_username = str(body.get("username") or "")
    username = verify_credentials(supplied_username, str(body.get("password") or ""))
    if not username:
        record_test_dispatcher_login_attempt(
            username=supplied_username,
            success=False,
            failure_reason="invalid_credentials",
            remote_address=remote,
            user_agent=agent,
        )
        return _json({"error": "Неверное имя или пароль"}, 401)

    token = secrets.token_urlsafe(32)
    expires_at = time.time() + SESSION_HOURS * 3600
    create_test_dispatcher_session(
        token_hash=_token_hash(token),
        username=username,
        expires_at=expires_at,
        remote_address=remote,
        user_agent=agent,
    )
    record_test_dispatcher_login_attempt(
        username=username,
        success=True,
        remote_address=remote,
        user_agent=agent,
    )
    response = _json({"ok": True, "user": username})
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_HOURS * 3600,
        httponly=True,
        secure=True,
        samesite="Lax",
        path="/",
    )
    logger.info("РУМЕКС тест: парольный вход диспетчера %s", username)
    return response


async def handle_auth_logout(request: web.Request) -> web.Response:
    token = request.cookies.get(COOKIE_NAME)
    if token and auth_enabled():
        revoke_test_dispatcher_session(token_hash=_token_hash(token))
    response = _json({"ok": True})
    response.del_cookie(COOKIE_NAME, path="/")
    return response


def register_rumex_test_dispatcher_auth_routes(app: web.Application) -> None:
    app.router.add_get("/api/rumex-registry/test/auth/check", handle_auth_check)
    app.router.add_get("/api/rumex-registry/test/auth/me", handle_auth_me)
    app.router.add_post("/api/rumex-registry/test/auth", handle_auth_login)
    app.router.add_post("/api/rumex-registry/test/auth/logout", handle_auth_logout)
    if auth_enabled():
        logger.info("РУМЕКС тест: парольный доступ диспетчера настроен")
    else:
        logger.warning("РУМЕКС тест: парольный доступ диспетчера не настроен; кабинет закрыт")
