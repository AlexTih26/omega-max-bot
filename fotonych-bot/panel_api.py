"""Маршрутизация единой панели MAX по роли пользователя."""

from __future__ import annotations

import logging
import os

from aiohttp import web

from drivers_chat import notify_admin_plain
from max_webapp import display_name_from_user, user_id_from_user, validate_init_data
from panel_feedback import append_panel_feedback, format_panel_feedback_message
from rumex_chat import panel_redirect, panel_role

logger = logging.getLogger(__name__)

def _bot_token() -> str:
    return os.getenv("MAX_BOT_TOKEN", "")


def _json(data: dict, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _parse_user_id(request: web.Request) -> int | None:
    init_data = request.headers.get("X-Max-Init-Data", "").strip()
    if not init_data:
        init_data = request.rel_url.query.get("initData", "").strip()
    if not init_data:
        return None
    parsed = validate_init_data(init_data, _bot_token())
    if parsed is None:
        return None
    user = parsed.get("user")
    if not isinstance(user, dict):
        return None
    return user_id_from_user(user)


async def handle_panel_role(request: web.Request) -> web.Response:
    uid = _parse_user_id(request)
    if uid is None:
        return _json({"error": "open in MAX mini-app"}, 401)
    role = panel_role(uid)
    return _json({"role": role, "redirect": panel_redirect(role)})


async def handle_panel_feedback(request: web.Request) -> web.Response:
    init_data = request.headers.get("X-Max-Init-Data", "").strip()
    if not init_data:
        return _json({"error": "open in MAX mini-app"}, 401)
    parsed = validate_init_data(init_data, _bot_token())
    if parsed is None:
        return _json({"error": "invalid init data"}, 401)
    user = parsed.get("user")
    if not isinstance(user, dict):
        return _json({"error": "user required"}, 401)
    uid = user_id_from_user(user)
    if uid is None:
        return _json({"error": "user id required"}, 401)

    try:
        body = await request.json()
    except Exception:
        return _json({"error": "invalid json"}, 400)

    try:
        entry = append_panel_feedback(
            max_user_id=uid,
            author=display_name_from_user(user),
            app=str(body.get("app") or "panel"),
            kind=str(body.get("kind") or "bug"),
            text=str(body.get("text") or ""),
            meta=body.get("meta") if isinstance(body.get("meta"), dict) else None,
        )
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)

    try:
        await notify_admin_plain(format_panel_feedback_message(entry))
    except Exception:
        logger.exception("Не удалось отправить feedback админу user_id=%s", uid)

    return _json({"ok": True, "id": entry.get("id")})


def register_panel_routes(app: web.Application) -> None:
    app.router.add_get("/api/panel/role", handle_panel_role)
    app.router.add_post("/api/panel/feedback", handle_panel_feedback)
