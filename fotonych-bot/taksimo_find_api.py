"""API поиска плиты для mini-app MAX (без PIN Таксимо)."""

from __future__ import annotations

import logging
import os

from aiohttp import web

from max_webapp import validate_init_data
from taksimo_store import unified_search

logger = logging.getLogger(__name__)


def _json(data: dict, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _bot_token() -> str:
    return os.getenv("MAX_BOT_TOKEN", "")


def _extract_init_data(request: web.Request) -> str:
    init_data = request.headers.get("X-Max-Init-Data", "").strip()
    if init_data:
        return init_data
    return request.rel_url.query.get("initData", "").strip()


def _require_max_user(request: web.Request) -> bool:
    init_data = _extract_init_data(request)
    if not init_data:
        logger.warning("taksimo-find: нет initData")
        return False
    if validate_init_data(init_data, _bot_token()) is None:
        logger.warning("taksimo-find: initData не прошёл проверку")
        return False
    return True


async def handle_taksimo_find_search(request: web.Request) -> web.Response:
    if not _require_max_user(request):
        return _json({"error": "open in MAX mini-app"}, 401)
    q = request.rel_url.query.get("q", "").strip()
    if not q:
        return _json({"type": "slab", "results": []})
    try:
        limit = int(request.rel_url.query.get("limit", "30"))
    except ValueError:
        limit = 30
    limit = max(1, min(limit, 50))
    data = unified_search(q, limit=limit)
    return _json(data)


def register_taksimo_find_routes(app: web.Application) -> None:
    app.router.add_get("/api/taksimo-find/search", handle_taksimo_find_search)
