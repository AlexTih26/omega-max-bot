"""Отдельный PIN-вход нового контура PWA Таксимо.

Ни cookie, ни учётные записи не пересекаются со старой Таксимо или РУМЕКС.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import time

from aiohttp import web

from taksimo_new_db import TaksimoNewDatabaseError
import taksimo_new_store as store


logger = logging.getLogger(__name__)

COOKIE_NAME = "taksimo_new_auth"
USER_KEY = "taksimo_new_operator"
SESSION_DAYS = 7
MAX_FAILED_ATTEMPTS = 5
ATTEMPT_WINDOW_SECONDS = 15 * 60
PIN_RE = re.compile(r"^\d{6,12}$")


def _secret() -> str:
    return (os.getenv("TAKSIMO_NEW_AUTH_SECRET") or "").strip()


def hash_pin(pin: str) -> str:
    """Хэшировать PIN для технической утилиты, не сохраняя его открытым."""
    if not PIN_RE.fullmatch(pin):
        raise ValueError("PIN должен содержать от 6 до 12 цифр")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(pin.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
    return "scrypt$16384$8$1$" + salt.hex() + "$" + digest.hex()


def verify_pin(pin: str, encoded: str) -> bool:
    if not PIN_RE.fullmatch(pin):
        return False
    try:
        algorithm, n, r, p, salt_hex, digest_hex = encoded.split("$")
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(
            pin.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
        )
        return hmac.compare_digest(actual.hex(), digest_hex)
    except (TypeError, ValueError):
        return False


def _token_hash(token: str) -> str:
    return hmac.new(_secret().encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()


def auth_status() -> dict[str, bool]:
    try:
        accounts_ready = store.operator_accounts_ready()
    except TaksimoNewDatabaseError:
        return {"configured": False, "database": False}
    return {"configured": bool(_secret()) and accounts_ready, "database": True}


def operator_from_request(request: web.Request) -> dict | None:
    cached = request.get(USER_KEY)
    if isinstance(cached, dict):
        return cached
    token = request.cookies.get(COOKIE_NAME)
    if not token or not _secret():
        return None
    try:
        operator = store.session_operator(_token_hash(token), renewal_seconds=SESSION_DAYS * 86400)
    except TaksimoNewDatabaseError:
        return None
    if operator:
        request[USER_KEY] = operator
    return operator


def _json(data: dict, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _renew_cookie(request: web.Request, response: web.StreamResponse) -> web.StreamResponse:
    token = request.cookies.get(COOKIE_NAME)
    if token and operator_from_request(request):
        response.set_cookie(
            COOKIE_NAME,
            token,
            max_age=SESSION_DAYS * 86400,
            httponly=True,
            secure=True,
            samesite="Strict",
            path="/",
        )
    return response


@web.middleware
async def taksimo_new_auth_middleware(request: web.Request, handler):
    path = request.path
    if not path.startswith("/api/taksimo-new/") or path.startswith("/api/taksimo-new/integration/"):
        return await handler(request)
    if path in {
        "/api/taksimo-new/auth/check",
        "/api/taksimo-new/auth/me",
        "/api/taksimo-new/auth/logout",
    } or (path == "/api/taksimo-new/auth" and request.method == "POST"):
        return _renew_cookie(request, await handler(request))
    status = auth_status()
    if not status["database"]:
        return _json({"error": "MySQL нового контура недоступен"}, 503)
    if not status["configured"]:
        return _json({"error": "PIN-доступ нового контура не настроен"}, 503)
    if not operator_from_request(request):
        return _json({"error": "Требуется вход", "login": "/taksimo-new/login.html"}, 401)
    return _renew_cookie(request, await handler(request))


async def handle_auth_check(request: web.Request) -> web.Response:
    status = auth_status()
    return _json({**status, "authenticated": bool(operator_from_request(request))}, 200 if status["database"] else 503)


async def handle_auth_me(request: web.Request) -> web.Response:
    status = auth_status()
    if not status["database"]:
        return _json({"error": "MySQL нового контура недоступен"}, 503)
    if not status["configured"]:
        return _json({"configured": False}, 503)
    operator = operator_from_request(request)
    if not operator:
        return _json({"error": "Требуется вход"}, 401)
    return _json({"configured": True, "operator": operator})


async def handle_auth_login(request: web.Request) -> web.Response:
    status = auth_status()
    remote = (request.remote or "").strip()
    agent = (request.headers.get("User-Agent") or "").strip()[:500]
    if not status["database"]:
        return _json({"error": "MySQL нового контура недоступен"}, 503)
    if not status["configured"]:
        return _json({"error": "PIN-доступ нового контура не настроен"}, 503)
    try:
        if store.failed_login_count(remote, since_seconds=ATTEMPT_WINDOW_SECONDS) >= MAX_FAILED_ATTEMPTS:
            return _json({"error": "Слишком много неверных попыток. Попробуйте позже."}, 429)
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError
    except ValueError:
        return _json({"error": "Некорректный запрос"}, 400)
    pin = str(body.get("pin") or "")
    operator = store.find_operator_for_pin(pin, verify_pin)
    if not operator:
        store.record_login_attempt(success=False, remote_address=remote, user_agent=agent)
        return _json({"error": "Неверный PIN"}, 401)
    token = secrets.token_urlsafe(32)
    store.create_session(
        token_hash=_token_hash(token),
        operator_id=int(operator["id"]),
        expires_at=time.time() + SESSION_DAYS * 86400,
        remote_address=remote,
        user_agent=agent,
    )
    store.record_login_attempt(success=True, remote_address=remote, user_agent=agent, operator_id=int(operator["id"]))
    response = _json({"ok": True, "operator": operator})
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_DAYS * 86400,
        httponly=True,
        secure=True,
        samesite="Strict",
        path="/",
    )
    logger.info("Новая Таксимо: выполнен вход роль=%s", operator["role"])
    return response


async def handle_auth_logout(request: web.Request) -> web.Response:
    token = request.cookies.get(COOKIE_NAME)
    if token and _secret():
        try:
            store.revoke_session(_token_hash(token))
        except TaksimoNewDatabaseError:
            pass
    response = _json({"ok": True})
    response.del_cookie(COOKIE_NAME, path="/")
    return response


def register_taksimo_new_auth_routes(app: web.Application) -> None:
    app.router.add_get("/api/taksimo-new/auth/check", handle_auth_check)
    app.router.add_get("/api/taksimo-new/auth/me", handle_auth_me)
    app.router.add_post("/api/taksimo-new/auth", handle_auth_login)
    app.router.add_post("/api/taksimo-new/auth/logout", handle_auth_logout)
