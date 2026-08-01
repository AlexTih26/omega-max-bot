"""HTTP API панели водителей (mini-app MAX)."""

from __future__ import annotations

import logging
import os

from aiohttp import web

from drivers_chat import (
    ACTION_PAYLOADS,
    apply_driver_action,
    driver_journal_payload,
    driver_status_payload,
    drivers_registry_payload,
    notify_dispatchers_registration,
    publish_driver_action,
    submit_driver_registration,
)
from max_webapp import display_name_from_user, user_id_from_user, validate_init_data

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


async def handle_drivers_meta(_request: web.Request) -> web.Response:
    return _json(
        {
            "steps": [
                {"id": "factory_arrival", "label": "Прибыл на завод", "icon": "📍"},
                {"id": "factory", "label": "Выехал с завода", "icon": "🏭"},
                {
                    "id": "taksimo_arrival",
                    "label": "Прибыл в Таксimo",
                    "icon": "📍",
                    "auto_fallback": True,
                },
                {
                    "id": "yard_depart",
                    "label": "Выехал с площадки Таксимо",
                    "icon": "🚛",
                    "auto": True,
                },
            ],
        }
    )


async def handle_drivers_status(request: web.Request) -> web.Response:
    _, uid = _parse_user(request)
    if uid is None:
        return _json({"error": "open in MAX mini-app"}, 401)
    return _json(driver_status_payload(uid))


async def handle_drivers_journal(request: web.Request) -> web.Response:
    _, uid = _parse_user(request)
    if uid is None:
        return _json({"error": "open in MAX mini-app"}, 401)
    try:
        days = int(request.query.get("days", "7"))
    except ValueError:
        days = 7
    days = max(1, min(days, 30))
    return _json(driver_journal_payload(uid, days=days))


async def handle_drivers_registry(request: web.Request) -> web.Response:
    _, uid = _parse_user(request)
    if uid is None:
        return _json({"error": "open in MAX mini-app"}, 401)
    try:
        limit = int(request.query.get("limit", "50"))
    except ValueError:
        limit = 50
    limit = max(1, min(limit, 100))
    return _json(drivers_registry_payload(uid, limit=limit))


async def handle_drivers_register(request: web.Request) -> web.Response:
    user, uid = _parse_user(request)
    if uid is None or user is None:
        return _json({"error": "open in MAX mini-app"}, 401)

    ok, notification = submit_driver_registration(uid, display_name_from_user(user))
    if ok:
        await notify_dispatchers_registration(uid, display_name_from_user(user))

    return _json(
        {
            "ok": ok,
            "notification": notification,
            "status": driver_status_payload(uid),
        },
        200 if ok else 409,
    )


async def handle_drivers_action(request: web.Request) -> web.Response:
    _, uid = _parse_user(request)
    if uid is None:
        return _json({"error": "open in MAX mini-app"}, 401)

    try:
        body = await request.json()
    except Exception:
        return _json({"error": "invalid json"}, 400)

    action = (body.get("action") or "").strip().lower()
    payload = ACTION_PAYLOADS.get(action)
    if not payload:
        return _json({"error": "unknown action"}, 400)

    result = apply_driver_action(uid, payload)
    if result.ok and result.public_message:
        await publish_driver_action(result)

    payload = {
        "ok": result.ok,
        "notification": result.notification,
        "status": driver_status_payload(uid),
    }
    if result.ok:
        payload["journal"] = driver_journal_payload(uid)
    return _json(payload, 200 if result.ok else 409)


def register_drivers_routes(app: web.Application) -> None:
    app.router.add_get("/api/drivers/meta", handle_drivers_meta)
    app.router.add_get("/api/drivers/status", handle_drivers_status)
    app.router.add_get("/api/drivers/journal", handle_drivers_journal)
    app.router.add_get("/api/drivers/registry", handle_drivers_registry)
    app.router.add_post("/api/drivers/register", handle_drivers_register)
    app.router.add_post("/api/drivers/action", handle_drivers_action)
