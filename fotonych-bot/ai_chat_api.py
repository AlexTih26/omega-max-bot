"""HTTP API OMEGA Chat mini-app (изолирован от Таксimo / водителей / Румекс)."""

from __future__ import annotations

import logging
import os

from aiohttp import web

from ai_chat_service import ChatError, send_chat_message
from ai_chat_store import (
    clear_conversation_messages,
    create_conversation,
    ensure_active_conversation,
    list_conversations,
    list_messages,
    log_event,
    status_payload,
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


async def handle_ai_status(request: web.Request) -> web.Response:
    user, uid = _parse_user(request)
    if uid is None or user is None:
        return _json({"error": "open in MAX mini-app"}, 401)
    log_event(max_user_id=uid, event_type="app_open")
    return _json(status_payload(max_user_id=uid, display_name=display_name_from_user(user)))


async def handle_ai_messages(request: web.Request) -> web.Response:
    user, uid = _parse_user(request)
    if uid is None:
        return _json({"error": "open in MAX mini-app"}, 401)
    try:
        cid = int(request.query.get("conversation_id") or "0")
    except ValueError:
        cid = 0
    if cid <= 0:
        conv = ensure_active_conversation(max_user_id=uid)
        cid = int(conv["id"])
    items = list_messages(conversation_id=cid, max_user_id=uid)
    return _json({"conversation_id": cid, "messages": items})


async def handle_ai_conversations(request: web.Request) -> web.Response:
    _, uid = _parse_user(request)
    if uid is None:
        return _json({"error": "open in MAX mini-app"}, 401)
    return _json({"conversations": list_conversations(max_user_id=uid)})


async def handle_ai_conversation_create(request: web.Request) -> web.Response:
    user, uid = _parse_user(request)
    if uid is None:
        return _json({"error": "open in MAX mini-app"}, 401)
    title = "Новый диалог"
    try:
        body = await request.json()
        if isinstance(body, dict) and body.get("title"):
            title = str(body["title"])
    except Exception:
        pass
    conv = create_conversation(max_user_id=uid, title=title)
    if user:
        log_event(max_user_id=uid, event_type="conversation_create", meta={"id": conv["id"]})
    return _json({"conversation": conv}, 201)


async def handle_ai_chat(request: web.Request) -> web.Response:
    user, uid = _parse_user(request)
    if uid is None:
        return _json({"error": "open in MAX mini-app"}, 401)
    try:
        body = await request.json()
    except Exception:
        return _json({"error": "invalid json"}, 400)
    if not isinstance(body, dict):
        return _json({"error": "invalid body"}, 400)
    message = str(body.get("message") or "").strip()
    try:
        cid = int(body.get("conversation_id") or 0)
    except (TypeError, ValueError):
        cid = 0
    if cid <= 0:
        conv = ensure_active_conversation(max_user_id=uid)
        cid = int(conv["id"])
    try:
        result = await send_chat_message(
            max_user_id=uid,
            display_name=display_name_from_user(user),
            conversation_id=cid,
            user_message=message,
        )
    except ChatError as e:
        code = e.code
        status = 429 if code == "daily_limit" else 400
        if code == "openai_error":
            status = 503
        return _json({"error": str(e), "code": code}, status)
    return _json({"ok": True, "conversation_id": cid, **result})


async def handle_ai_clear(request: web.Request) -> web.Response:
    _, uid = _parse_user(request)
    if uid is None:
        return _json({"error": "open in MAX mini-app"}, 401)
    try:
        cid = int(request.match_info.get("conversation_id") or "0")
    except ValueError:
        return _json({"error": "bad conversation id"}, 400)
    if not clear_conversation_messages(conversation_id=cid, max_user_id=uid):
        return _json({"error": "not found"}, 404)
    log_event(max_user_id=uid, event_type="conversation_clear", meta={"id": cid})
    return _json({"ok": True})


def register_ai_chat_routes(app: web.Application) -> None:
    app.router.add_get("/api/ai/status", handle_ai_status)
    app.router.add_get("/api/ai/messages", handle_ai_messages)
    app.router.add_get("/api/ai/conversations", handle_ai_conversations)
    app.router.add_post("/api/ai/conversations", handle_ai_conversation_create)
    app.router.add_post("/api/ai/chat", handle_ai_chat)
    app.router.add_post("/api/ai/conversations/{conversation_id}/clear", handle_ai_clear)
