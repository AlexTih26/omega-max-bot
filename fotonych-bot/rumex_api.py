"""HTTP API панели диспетчера Румекс (mini-app MAX)."""

from __future__ import annotations

import logging
import os

from aiohttp import web

from max_webapp import user_id_from_user, validate_init_data
from rumex_chat import (
    apply_rumex_action,
    is_rumex_dispatcher,
    publish_rumex_action,
    rumex_registry_payload,
    rumex_shift_export_text,
)

logger = logging.getLogger(__name__)


def _json(data: dict, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _bot_token() -> str:
    return os.getenv("MAX_BOT_TOKEN", "")


def _parse_user_id(request: web.Request) -> int | None:
    init_data = request.headers.get("X-Max-Init-Data", "").strip()
    if not init_data:
        return None
    parsed = validate_init_data(init_data, _bot_token())
    if parsed is None:
        return None
    user = parsed.get("user")
    if not isinstance(user, dict):
        return None
    return user_id_from_user(user)


async def handle_rumex_registry(request: web.Request) -> web.Response:
    uid = _parse_user_id(request)
    if uid is None:
        return _json({"error": "open in MAX mini-app"}, 401)
    if not is_rumex_dispatcher(uid):
        return _json({"error": "forbidden"}, 403)
    return _json(rumex_registry_payload())


async def handle_rumex_export(request: web.Request) -> web.Response:
    uid = _parse_user_id(request)
    if uid is None:
        return _json({"error": "open in MAX mini-app"}, 401)
    if not is_rumex_dispatcher(uid):
        return _json({"error": "forbidden"}, 403)
    return _json({"text": rumex_shift_export_text()})


async def handle_rumex_action(request: web.Request) -> web.Response:
    uid = _parse_user_id(request)
    if uid is None:
        return _json({"error": "open in MAX mini-app"}, 401)
    if not is_rumex_dispatcher(uid):
        return _json({"error": "forbidden"}, 403)

    try:
        body = await request.json()
    except Exception:
        return _json({"error": "invalid json"}, 400)

    plate_tail = str(body.get("plate_tail") or "").strip()
    action = str(body.get("action") or "").strip().lower()
    if not plate_tail or not action:
        return _json({"error": "plate_tail and action required"}, 400)

    result = apply_rumex_action(uid, plate_tail, action)
    if result.ok:
        await publish_rumex_action(result)

    return _json(
        {
            "ok": result.ok,
            "notification": result.notification,
            "registry": rumex_registry_payload(),
        },
        200 if result.ok else 409,
    )


def register_rumex_routes(app: web.Application) -> None:
    app.router.add_get("/api/rumex/registry", handle_rumex_registry)
    app.router.add_get("/api/rumex/export", handle_rumex_export)
    app.router.add_post("/api/rumex/action", handle_rumex_action)
