"""HTTP API комментариев для max.avtmsk.ru (aiohttp)."""

from __future__ import annotations

import asyncio
import json
import logging
import os

from aiohttp import web

from comments_button import refresh_comments_button
from comments_store import (
    add_comment,
    build_comment_tree,
    count_comments,
    get_post,
    init_db,
    list_comments,
    toggle_like,
)
from max_webapp import (
    avatar_url_from_user,
    display_name_from_user,
    user_id_from_user,
    validate_init_data,
)
from drivers_api import register_drivers_routes
from panel_api import register_panel_routes
from rumex_api import register_rumex_routes
from taksimo_find_api import register_taksimo_find_routes
from taksimo_api import register_taksimo_routes
from taksimo_auth import register_taksimo_auth_routes, taksimo_auth_middleware
from admin_api import register_admin_routes
from ai_chat_api import register_ai_chat_routes
from ai_chat_store import init_omega_chat_db
from work_store import CATEGORIES, STATUSES, create_request, init_work_db, list_requests_for_user

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


async def handle_get_post(request: web.Request) -> web.Response:
    post_id = request.match_info.get("post_id", "").strip()
    if not post_id:
        return _json({"error": "post_id required"}, 400)
    post = get_post(post_id)
    if post is None:
        return _json({"error": "not found"}, 404)

    _, viewer_id = _parse_user(request)
    flat = list_comments(post_id, viewer_id=viewer_id)
    comments = build_comment_tree(flat)
    total = count_comments(post_id)

    viewer = None
    user, uid = _parse_user(request)
    if user and uid:
        viewer = {
            "id": uid,
            "author": display_name_from_user(user),
            "author_photo": avatar_url_from_user(user),
        }

    return _json({"post": post, "comments": comments, "comment_count": total, "viewer": viewer})


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

    parent_id = body.get("parent_id")
    if parent_id is not None:
        try:
            parent_id = int(parent_id)
        except (TypeError, ValueError):
            return _json({"error": "invalid parent_id"}, 400)

    user = parsed.get("user")
    if not isinstance(user, dict):
        return _json({"error": "user required"}, 401)

    author = display_name_from_user(user)
    user_id = user_id_from_user(user)
    photo = avatar_url_from_user(user)

    try:
        comment = add_comment(
            post_id,
            text,
            author,
            max_user_id=user_id,
            parent_id=parent_id,
            author_photo=photo,
        )
    except KeyError:
        return _json({"error": "post not found"}, 404)
    except ValueError as e:
        return _json({"error": str(e)}, 400)

    asyncio.create_task(refresh_comments_button(post_id))

    return _json({"comment": comment}, 201)


async def handle_toggle_like(request: web.Request) -> web.Response:
    comment_id_raw = request.match_info.get("comment_id", "").strip()
    try:
        comment_id = int(comment_id_raw)
    except ValueError:
        return _json({"error": "invalid comment_id"}, 400)

    init_data = request.headers.get("X-Max-Init-Data", "").strip()
    if not init_data:
        return _json({"error": "open in MAX mini-app"}, 401)

    parsed = validate_init_data(init_data, _bot_token())
    if parsed is None:
        return _json({"error": "invalid init data"}, 401)

    user = parsed.get("user")
    if not isinstance(user, dict):
        return _json({"error": "user required"}, 401)

    user_id = user_id_from_user(user)
    if user_id is None:
        return _json({"error": "user id required"}, 401)

    author = display_name_from_user(user)
    photo = avatar_url_from_user(user)

    try:
        likes = toggle_like(comment_id, user_id, author, author_photo=photo)
    except KeyError:
        return _json({"error": "comment not found"}, 404)

    return _json({"likes": likes})


async def handle_health(_request: web.Request) -> web.Response:
    return _json({"ok": True})


async def handle_work_meta(_request: web.Request) -> web.Response:
    return _json({"categories": list(CATEGORIES), "statuses": list(STATUSES)})


async def handle_work_list(request: web.Request) -> web.Response:
    user, uid = _parse_user(request)
    if not user or uid is None:
        return _json({"error": "open in MAX mini-app"}, 401)
    requests = list_requests_for_user(uid)
    return _json(
        {
            "requests": requests,
            "viewer": {
                "id": uid,
                "author": display_name_from_user(user),
                "author_photo": avatar_url_from_user(user),
            },
        }
    )


async def handle_work_create(request: web.Request) -> web.Response:
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
    except json.JSONDecodeError:
        return _json({"error": "invalid json"}, 400)

    try:
        item = create_request(
            max_user_id=uid,
            author=display_name_from_user(user),
            category=(body.get("category") or "другое"),
            title=(body.get("title") or ""),
            text=(body.get("text") or ""),
        )
    except ValueError as e:
        return _json({"error": str(e)}, 400)

    return _json({"request": item}, 201)


def create_app() -> web.Application:
    init_db()
    init_work_db()
    init_omega_chat_db()
    app = web.Application(middlewares=[taksimo_auth_middleware])
    app.router.add_get("/api/health", handle_health)
    app.router.add_get("/api/posts/{post_id}", handle_get_post)
    app.router.add_post("/api/posts/{post_id}/comments", handle_post_comment)
    app.router.add_post("/api/comments/{comment_id}/like", handle_toggle_like)
    app.router.add_get("/api/work/meta", handle_work_meta)
    app.router.add_get("/api/work/requests", handle_work_list)
    app.router.add_post("/api/work/requests", handle_work_create)
    register_taksimo_auth_routes(app)
    register_taksimo_routes(app)
    register_drivers_routes(app)
    register_panel_routes(app)
    register_admin_routes(app)
    register_rumex_routes(app)
    from ipdocs_api import register_ipdocs_routes

    register_ipdocs_routes(app)
    register_taksimo_find_routes(app)
    register_ai_chat_routes(app)
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
