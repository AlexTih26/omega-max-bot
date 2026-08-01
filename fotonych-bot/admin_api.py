"""HTTP API админ-панели объявлений (mini-app MAX)."""

from __future__ import annotations

import logging
import os

from aiohttp import web

from admin_announce import admin_meta_payload, list_recent_announcements, submit_drivers_announcement
from admin_fleet import apply_fleet_action, fleet_list_payload
from admin_wagons import add_wagons_admin, wagon_fleet_payload
from max_webapp import user_id_from_user, validate_init_data
from super_admin import is_super_admin

logger = logging.getLogger(__name__)


def _json(data: dict, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _bot_token() -> str:
    return os.getenv("MAX_BOT_TOKEN", "")


def _parse_user(request: web.Request) -> tuple[dict | None, int | None]:
    init_data = request.headers.get("X-Max-Init-Data", "").strip()
    if not init_data:
        return None, None
    parsed = validate_init_data(init_data, _bot_token())
    if parsed is None:
        return None, None
    user = parsed.get("user")
    if not isinstance(user, dict):
        return None, None
    return user, user_id_from_user(user)


async def handle_admin_profile(request: web.Request) -> web.Response:
    user, uid = _parse_user(request)
    if uid is None or user is None:
        return _json({"error": "open in MAX mini-app"}, 401)
    if not is_super_admin(uid):
        return _json({"error": "forbidden", "admin": False}, 403)
    return _json({"ok": True, "admin": True, **admin_meta_payload()})


async def handle_admin_announcements_list(request: web.Request) -> web.Response:
    _, uid = _parse_user(request)
    if uid is None:
        return _json({"error": "open in MAX mini-app"}, 401)
    if not is_super_admin(uid):
        return _json({"error": "forbidden"}, 403)
    try:
        limit = int(request.query.get("limit", "5"))
    except ValueError:
        limit = 5
    limit = max(1, min(limit, 20))
    return _json({"ok": True, "items": list_recent_announcements(limit)})


async def handle_admin_announcements_post(request: web.Request) -> web.Response:
    user, uid = _parse_user(request)
    if uid is None or user is None:
        return _json({"error": "open in MAX mini-app"}, 401)
    if not is_super_admin(uid):
        return _json({"error": "forbidden"}, 403)

    try:
        body = await request.json()
    except Exception:
        return _json({"error": "invalid json"}, 400)

    text = str(body.get("text") or "")
    ok, notification, entry = await submit_drivers_announcement(
        max_user_id=uid,
        user=user,
        text=text,
    )
    if not ok:
        return _json({"ok": False, "notification": notification}, 409 if text.strip() else 400)

    return _json({"ok": True, "notification": notification, "entry": entry})


async def handle_admin_fleet_list(request: web.Request) -> web.Response:
    _, uid = _parse_user(request)
    if uid is None:
        return _json({"error": "open in MAX mini-app"}, 401)
    if not is_super_admin(uid):
        return _json({"error": "forbidden"}, 403)
    return _json({"ok": True, **fleet_list_payload()})


async def handle_admin_fleet_action(request: web.Request) -> web.Response:
    _, uid = _parse_user(request)
    if uid is None:
        return _json({"error": "open in MAX mini-app"}, 401)
    if not is_super_admin(uid):
        return _json({"error": "forbidden"}, 403)
    try:
        body = await request.json()
    except Exception:
        return _json({"error": "invalid json"}, 400)
    if not isinstance(body, dict):
        return _json({"error": "invalid body"}, 400)
    ok, notification = apply_fleet_action(body)
    payload: dict = {"ok": ok, "notification": notification}
    if ok:
        payload.update(fleet_list_payload())
    return _json(payload, 409 if not ok else 200)


async def handle_admin_wagons_list(request: web.Request) -> web.Response:
    _, uid = _parse_user(request)
    if uid is None:
        return _json({"error": "open in MAX mini-app"}, 401)
    if not is_super_admin(uid):
        return _json({"error": "forbidden"}, 403)
    return _json({"ok": True, **wagon_fleet_payload()})


async def handle_admin_wagons_add(request: web.Request) -> web.Response:
    _, uid = _parse_user(request)
    if uid is None:
        return _json({"error": "open in MAX mini-app"}, 401)
    if not is_super_admin(uid):
        return _json({"error": "forbidden"}, 403)
    try:
        body = await request.json()
    except Exception:
        return _json({"error": "invalid json"}, 400)
    numbers = body.get("numbers") or body.get("number") or ""
    if isinstance(numbers, str):
        numbers = [n.strip() for n in numbers.replace(";", ",").split(",") if n.strip()]
    elif not isinstance(numbers, list):
        numbers = []
    ok, notification = add_wagons_admin(
        numbers,
        stage=str(body.get("stage") or "available"),
        planned_zone=str(body.get("planned_zone") or ""),
    )
    payload: dict = {"ok": ok, "notification": notification}
    if ok:
        payload.update(wagon_fleet_payload())
    return _json(payload, 409 if not ok else 200)


def register_admin_routes(app: web.Application) -> None:
    app.router.add_get("/api/admin/profile", handle_admin_profile)
    app.router.add_get("/api/admin/announcements", handle_admin_announcements_list)
    app.router.add_post("/api/admin/announcements", handle_admin_announcements_post)
    app.router.add_get("/api/admin/fleet", handle_admin_fleet_list)
    app.router.add_post("/api/admin/fleet", handle_admin_fleet_action)
    app.router.add_get("/api/admin/wagons", handle_admin_wagons_list)
    app.router.add_post("/api/admin/wagons", handle_admin_wagons_add)
