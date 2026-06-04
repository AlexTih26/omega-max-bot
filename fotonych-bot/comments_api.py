"""HTTP API комментариев для max.avtmsk.ru (aiohttp)."""

from __future__ import annotations

import json
import logging
import os

from aiohttp import web

from comments_store import add_comment, get_post, init_db, list_comments
from max_webapp import display_name_from_user, validate_init_data

logger = logging.getLogger(__name__)


def _json(data: dict, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _bot_token() -> str:
    return os.getenv("MAX_BOT_TOKEN", "")


async def handle_get_post(request: web.Request) -> web.Response:
    post_id = request.match_info.get("post_id", "").strip()
    if not post_id:
        return _json({"error": "post_id required"}, 400)
    post = get_post(post_id)
    if post is None:
        return _json({"error": "not found"}, 404)
    comments = list_comments(post_id)
    return _json({"post": post, "comments": comments})


async def handle_post_comment(request: web.Request) -> web.Response:
    post_id = request.match_info.get("post_id", "").strip()
    if not post_id:
        return _json({"error": "post_id required"}, 400)

    init_data = request.headers.get("X-Max-Init-Data", "").strip()
    if not init_data:
        return _json({"error": "open in MAX mini-app"}, 401)

    parsed = validate_init_data(init_data, _bot_token())
    if parsed is None:
        logger.warning("invalid initData for post %s", post_id)
        return _json({"error": "invalid init data"}, 401)

    try:
        body = await request.json()
    except json.JSONDecodeError:
        return _json({"error": "invalid json"}, 400)

    text = (body.get("text") or "").strip()
    if not text:
        return _json({"error": "text required"}, 400)

    user = parsed.get("user")
    author = display_name_from_user(user if isinstance(user, dict) else None)
    user_id = user.get("id") if isinstance(user, dict) else None

    try:
        comment = add_comment(post_id, text, author, max_user_id=user_id)
    except KeyError:
        return _json({"error": "post not found"}, 404)
    except ValueError as e:
        return _json({"error": str(e)}, 400)

    return _json({"comment": comment}, 201)


async def handle_health(_request: web.Request) -> web.Response:
    return _json({"ok": True})


def create_app() -> web.Application:
    init_db()
    app = web.Application()
    app.router.add_get("/api/health", handle_health)
    app.router.add_get("/api/posts/{post_id}", handle_get_post)
    app.router.add_post("/api/posts/{post_id}/comments", handle_post_comment)
    return app


async def start_comments_api() -> web.AppRunner:
    port = int(os.getenv("COMMENTS_PORT", "8765"))
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    logger.info("Comments API на http://127.0.0.1:%s", port)
    return runner
