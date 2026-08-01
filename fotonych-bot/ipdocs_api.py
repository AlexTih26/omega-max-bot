"""HTTP API мини-приложения «Счёт и акт» (ИП → Омега-М)."""

from __future__ import annotations

import logging
import os

from aiohttp import web

from ipdocs_render import render_document_html
from ipdocs_store import (
    create_document,
    get_document,
    list_documents,
    profile_payload,
    update_document_status,
)

logger = logging.getLogger(__name__)


def _json(data: dict, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _bot_token() -> str:
    return os.getenv("MAX_BOT_TOKEN", "")


def _parse_user_id(request: web.Request) -> int | None:
    from max_webapp import user_id_from_user, validate_init_data

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


async def handle_ipdocs_profile(request: web.Request) -> web.Response:
    uid = _parse_user_id(request)
    if uid is None:
        return _json({"error": "open in MAX mini-app"}, 401)
    return _json(profile_payload(uid))


async def handle_ipdocs_documents(request: web.Request) -> web.Response:
    uid = _parse_user_id(request)
    if uid is None:
        return _json({"error": "open in MAX mini-app"}, 401)
    month = request.query.get("month", "")
    return _json({"documents": list_documents(uid, month=month)})


async def handle_ipdocs_create(request: web.Request) -> web.Response:
    uid = _parse_user_id(request)
    if uid is None:
        return _json({"error": "open in MAX mini-app"}, 401)
    try:
        body = await request.json()
    except Exception:
        return _json({"error": "invalid json"}, 400)

    entry, message = create_document(
        uid,
        doc_type=str(body.get("doc_type") or ""),
        date=str(body.get("date") or ""),
        route=str(body.get("route") or ""),
        tariff_id=str(body.get("tariff_id") or ""),
        quantity=body.get("quantity"),
        unit_price=body.get("unit_price"),
        amount=body.get("amount"),
        period=str(body.get("period") or ""),
        note=str(body.get("note") or ""),
        status=str(body.get("status") or "draft"),
    )
    if entry is None:
        return _json({"error": message}, 409)
    from ipdocs_store import public_doc

    return _json({"ok": True, "notification": message, "document": public_doc(entry)}, 201)


async def handle_ipdocs_document(request: web.Request) -> web.Response:
    uid = _parse_user_id(request)
    if uid is None:
        return _json({"error": "open in MAX mini-app"}, 401)
    doc_id = request.match_info.get("doc_id", "")
    entry = get_document(uid, doc_id)
    if entry is None:
        return _json({"error": "not found"}, 404)
    from ipdocs_store import public_doc

    return _json({"document": public_doc(entry)})


async def handle_ipdocs_document_html(request: web.Request) -> web.Response:
    uid = _parse_user_id(request)
    if uid is None:
        return web.Response(text="Unauthorized", status=401)
    doc_id = request.match_info.get("doc_id", "")
    entry = get_document(uid, doc_id)
    if entry is None:
        return web.Response(text="Not found", status=404)
    html = render_document_html(entry)
    return web.Response(text=html, content_type="text/html; charset=utf-8")


async def handle_ipdocs_status(request: web.Request) -> web.Response:
    uid = _parse_user_id(request)
    if uid is None:
        return _json({"error": "open in MAX mini-app"}, 401)
    doc_id = request.match_info.get("doc_id", "")
    try:
        body = await request.json()
    except Exception:
        return _json({"error": "invalid json"}, 400)

    entry, message = update_document_status(
        uid, doc_id, str(body.get("status") or "")
    )
    if entry is None:
        return _json({"error": message}, 409)
    from ipdocs_store import public_doc

    return _json({"ok": True, "notification": message, "document": public_doc(entry)})


def register_ipdocs_routes(app: web.Application) -> None:
    app.router.add_get("/api/ipdocs/profile", handle_ipdocs_profile)
    app.router.add_get("/api/ipdocs/documents", handle_ipdocs_documents)
    app.router.add_post("/api/ipdocs/documents", handle_ipdocs_create)
    app.router.add_get("/api/ipdocs/documents/{doc_id}", handle_ipdocs_document)
    app.router.add_get("/api/ipdocs/documents/{doc_id}/html", handle_ipdocs_document_html)
    app.router.add_post("/api/ipdocs/documents/{doc_id}/status", handle_ipdocs_status)
