"""HTTP API панели диспетчера Румекс и бухгалтера (mini-app MAX)."""

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
from rumex_loading import (
    apply_loading_kontur,
    apply_loading_kontur_undo,
    apply_loading_loaded,
    apply_loading_release,
    is_rumex_accountant,
    kontur_copy_for_loading,
    loading_registry_payload,
    lookup_registry_by_tail,
    publish_rumex_loading_result,
    suggest_rumex_mode,
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


async def handle_rumex_lookup(request: web.Request) -> web.Response:
    uid = _parse_user_id(request)
    if uid is None:
        return _json({"error": "open in MAX mini-app"}, 401)
    if not is_rumex_dispatcher(uid):
        return _json({"error": "forbidden"}, 403)
    tail = str(request.rel_url.query.get("tail") or "").strip()
    if not tail:
        return _json({"error": "tail required"}, 400)
    info = lookup_registry_by_tail(tail)
    if info is None:
        return _json({"found": False, "suggested_mode": "load"})
    return _json(
        {
            "found": True,
            "driver": info,
            "suggested_mode": suggest_rumex_mode(tail),
        }
    )


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

    if action == "loaded":
        try:
            block_count = int(body.get("block_count") or 0)
        except (TypeError, ValueError):
            block_count = 0
        blocks = body.get("blocks") if isinstance(body.get("blocks"), list) else []
        result = apply_loading_loaded(
            uid,
            plate_tail,
            block_count=block_count,
            blocks=blocks,
            is_dispatcher=True,
        )
        if result.ok:
            await publish_rumex_loading_result(result)
        return _json(
            {
                "ok": result.ok,
                "notification": result.notification,
                "registry": rumex_registry_payload(),
                "loading": result.loading,
            },
            200 if result.ok else 409,
        )

    if action == "release":
        result = apply_loading_release(uid, plate_tail, is_dispatcher=True)
        if result.ok:
            await publish_rumex_loading_result(result)
        return _json(
            {
                "ok": result.ok,
                "notification": result.notification,
                "registry": rumex_registry_payload(),
                "loading": result.loading,
            },
            200 if result.ok else 409,
        )

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


async def handle_rumex_accountant_registry(request: web.Request) -> web.Response:
    uid = _parse_user_id(request)
    if uid is None:
        return _json({"error": "open in MAX mini-app"}, 401)
    if not is_rumex_accountant(uid):
        return _json({"error": "forbidden"}, 403)
    payload = loading_registry_payload()
    payload["site_label"] = "Омега-М · бухгалтер"
    return _json(payload)


async def handle_rumex_accountant_action(request: web.Request) -> web.Response:
    uid = _parse_user_id(request)
    if uid is None:
        return _json({"error": "open in MAX mini-app"}, 401)
    if not is_rumex_accountant(uid):
        return _json({"error": "forbidden"}, 403)

    try:
        body = await request.json()
    except Exception:
        return _json({"error": "invalid json"}, 400)

    action = str(body.get("action") or "").strip().lower()
    loading_id = str(body.get("loading_id") or "").strip()
    plate_tail = str(body.get("plate_tail") or "").strip()

    if action == "kontur":
        result = apply_loading_kontur(uid, loading_id=loading_id, plate_tail=plate_tail)
    elif action in {"kontur_undo", "undo_kontur"}:
        result = apply_loading_kontur_undo(uid, loading_id=loading_id)
    else:
        return _json({"error": "unknown action"}, 400)

    if result.ok:
        await publish_rumex_loading_result(result)

    payload = loading_registry_payload()
    payload["site_label"] = "Омега-М · бухгалтер"
    return _json(
        {
            "ok": result.ok,
            "notification": result.notification,
            "registry": payload,
            "loading": result.loading,
        },
        200 if result.ok else 409,
    )


async def handle_rumex_accountant_copy(request: web.Request) -> web.Response:
    uid = _parse_user_id(request)
    if uid is None:
        return _json({"error": "open in MAX mini-app"}, 401)
    if not is_rumex_accountant(uid):
        return _json({"error": "forbidden"}, 403)

    loading_id = str(request.match_info.get("loading_id") or "").strip()
    text = kontur_copy_for_loading(loading_id)
    if text is None:
        return _json({"error": "not found"}, 404)
    return _json({"text": text})


def register_rumex_routes(app: web.Application) -> None:
    app.router.add_get("/api/rumex/registry", handle_rumex_registry)
    app.router.add_get("/api/rumex/lookup", handle_rumex_lookup)
    app.router.add_get("/api/rumex/export", handle_rumex_export)
    app.router.add_post("/api/rumex/action", handle_rumex_action)
    app.router.add_get("/api/rumex/accountant/registry", handle_rumex_accountant_registry)
    app.router.add_post("/api/rumex/accountant/action", handle_rumex_accountant_action)
    app.router.add_get("/api/rumex/accountant/copy/{loading_id}", handle_rumex_accountant_copy)
