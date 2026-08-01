"""Авторизация Таксимо: несколько пользователей, PIN 5 цифр."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import time

from aiohttp import web

logger = logging.getLogger(__name__)

COOKIE_NAME = "taksimo_auth"
SESSION_DAYS = 7
USER_KEY = "taksimo_user"


def _secret() -> str:
    return (
        os.getenv("TAKSIMO_AUTH_SECRET")
        or os.getenv("MAX_BOT_TOKEN")
        or "taksimo-dev-secret"
    )


def _admin_names() -> set[str]:
    raw = (os.getenv("TAKSIMO_ADMIN_USERS") or "оператор1").strip()
    return {name.strip() for name in raw.split(",") if name.strip()}


def _parse_user_chunk(chunk: str) -> tuple[str, str, bool] | None:
    """Имя, PIN, явный admin-суффикс (:admin)."""
    chunk = chunk.strip()
    if ":" not in chunk:
        return None
    parts = [part.strip() for part in chunk.split(":")]
    if len(parts) == 3 and parts[2].lower() == "admin":
        name, pin = parts[0], parts[1]
        explicit_admin = True
    elif len(parts) == 2:
        name, pin = parts[0], parts[1]
        explicit_admin = False
    else:
        return None
    if not name or len(pin) != 5 or not pin.isdigit():
        return None
    return name, pin, explicit_admin


def load_users() -> dict[str, str]:
    """Имя пользователя → PIN (5 цифр)."""
    users: dict[str, str] = {}
    raw = (os.getenv("TAKSIMO_USERS") or "").strip()
    for chunk in raw.split(","):
        parsed = _parse_user_chunk(chunk)
        if not parsed:
            continue
        name, pin, _ = parsed
        users[name] = pin
    if not users:
        legacy = (os.getenv("TAKSIMO_PIN") or "").strip()
        if len(legacy) == 5 and legacy.isdigit():
            users["оператор"] = legacy
    return users


def user_role(username: str | None) -> str:
    if not username:
        return "operator"
    if not auth_enabled():
        return "admin"
    if username in _admin_names():
        return "admin"
    raw = (os.getenv("TAKSIMO_USERS") or "").strip()
    for chunk in raw.split(","):
        parsed = _parse_user_chunk(chunk)
        if parsed and parsed[0] == username and parsed[2]:
            return "admin"
    return "operator"


def can_delete(username: str | None) -> bool:
    return user_role(username) == "admin"


def auth_enabled() -> bool:
    return bool(load_users())


def verify_pin(pin: str) -> str | None:
    pin = (pin or "").strip()
    if len(pin) != 5 or not pin.isdigit():
        return None
    for name, expected in load_users().items():
        if hmac.compare_digest(pin, expected):
            return name
    return None


def create_token(username: str) -> str:
    exp = int(time.time()) + SESSION_DAYS * 86400
    payload = f"{exp}:{username}"
    sig = hmac.new(
        _secret().encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()[:24]
    return f"{payload}.{sig}"


def parse_token(token: str | None) -> str | None:
    if not token or not auth_enabled():
        return None if auth_enabled() else "оператор"
    try:
        payload, sig = token.rsplit(".", 1)
        exp_str, username = payload.split(":", 1)
        exp = int(exp_str)
        if time.time() > exp:
            return None
        if not re.fullmatch(r"[\w.\-А-Яа-яЁё ]{1,40}", username):
            return None
        expected = hmac.new(
            _secret().encode(),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()[:24]
        if not hmac.compare_digest(sig, expected):
            return None
        return username
    except (ValueError, TypeError):
        return None


def user_from_request(request: web.Request) -> str | None:
    cached = request.get(USER_KEY)
    if cached:
        return cached
    user = parse_token(request.cookies.get(COOKIE_NAME))
    if user:
        request[USER_KEY] = user
    return user


def _json(data: dict, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


@web.middleware
async def taksimo_auth_middleware(request: web.Request, handler):
    path = request.path
    if path.startswith("/api/taksimo-find"):
        return await handler(request)
    if not path.startswith("/api/taksimo"):
        return await handler(request)
    if not auth_enabled():
        request[USER_KEY] = "оператор"
        return await handler(request)
    if path in ("/api/taksimo/auth/check", "/api/taksimo/auth/me"):
        return await handler(request)
    if path == "/api/taksimo/auth" and request.method == "POST":
        return await handler(request)
    user = parse_token(request.cookies.get(COOKIE_NAME))
    if user:
        request[USER_KEY] = user
        return await handler(request)
    return _json({"error": "auth required", "login": "/taksimo-login.html"}, 401)


async def handle_taksimo_auth_check(request: web.Request) -> web.Response:
    if not auth_enabled():
        return web.Response(status=200)
    if user_from_request(request):
        return web.Response(status=200)
    return web.Response(status=401)


async def handle_taksimo_auth_me(request: web.Request) -> web.Response:
    user = user_from_request(request)
    if not user:
        return _json({"error": "auth required"}, 401)
    role = user_role(user)
    return _json({
        "user": user,
        "role": role,
        "can_delete": role == "admin",
        "can_kodar": role == "admin",
    })


async def handle_taksimo_auth_login(request: web.Request) -> web.Response:
    if not auth_enabled():
        return _json({"error": "users not configured"}, 503)
    try:
        body = await request.json()
    except Exception:
        return _json({"error": "invalid json"}, 400)
    username = verify_pin(str(body.get("pin") or "").strip())
    if not username:
        return _json({"error": "неверный PIN"}, 401)
    token = create_token(username)
    role = user_role(username)
    resp = _json({
        "ok": True,
        "user": username,
        "role": role,
        "can_delete": role == "admin",
        "can_kodar": role == "admin",
    })
    resp.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_DAYS * 86400,
        httponly=True,
        secure=True,
        samesite="Lax",
        path="/",
    )
    logger.info("Таксимо: вход пользователь=%s", username)
    return resp


def register_taksimo_auth_routes(app: web.Application) -> None:
    app.router.add_get("/api/taksimo/auth/check", handle_taksimo_auth_check)
    app.router.add_get("/api/taksimo/auth/me", handle_taksimo_auth_me)
    app.router.add_post("/api/taksimo/auth", handle_taksimo_auth_login)
    users = load_users()
    if users:
        roles = ", ".join(f"{name} ({user_role(name)})" for name in users)
        logger.info("Таксимо: пользователи (%s): %s", len(users), roles)
    else:
        logger.warning("TAKSIMO_USERS не задан — Таксимо без пароля")
