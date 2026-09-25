"""API изолированного PWA управления РУМЕКС."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets

from aiohttp import web

from rumex_admin_auth import user_from_request
from rumex_registry_store import (
    create_rumex_dispatcher_account,
    get_rumex_dispatcher_account,
    list_block_types,
    list_carriers,
    list_document_fleet_vehicles,
    list_rumex_dispatcher_accounts,
    list_rumex_management_audit_events,
    list_test_shipments,
    reset_rumex_dispatcher_password,
    revoke_rumex_dispatcher_sessions,
    rumex_dispatcher_security_summary,
    set_rumex_vehicle_shipment_block,
    update_rumex_dispatcher_account,
)


def _json(data: dict, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _admin(request: web.Request) -> tuple[dict | None, web.Response | None]:
    identity = user_from_request(request)
    if identity is None:
        return None, _json({"error": "Требуется вход в управление РУМЕКС"}, 401)
    return identity, None


def _password_hash(password: str) -> str:
    if not 12 <= len(password) <= 256:
        raise ValueError("Пароль должен содержать от 12 до 256 символов")
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt.encode("ascii"), n=2**14, r=8, p=1).hex()
    return salt + "$" + digest


def _issued_password() -> str:
    return secrets.token_urlsafe(18)


def _step_up_confirmed(body: dict) -> bool:
    configured = (os.getenv("RUMEX_ADMIN_STEP_UP_PIN") or "").strip()
    supplied = str(body.get("step_up_pin") or "")
    return bool(configured) and hmac.compare_digest(supplied, configured)


async def handle_profile(request: web.Request) -> web.Response:
    identity, denied = _admin(request)
    if denied:
        return denied
    return _json({"ok": True, "user": identity, "site_label": "РУМЕКС · управление"})


async def handle_overview(request: web.Request) -> web.Response:
    _identity, denied = _admin(request)
    if denied:
        return denied
    return _json({
        "dispatchers": list_rumex_dispatcher_accounts(),
        "vehicles": list_document_fleet_vehicles(),
        "shipments": list_test_shipments(),
        "carriers": list_carriers(),
        "block_types": list_block_types(),
        "audit_events": list_rumex_management_audit_events(limit=50),
    })


async def handle_dispatcher_create(request: web.Request) -> web.Response:
    identity, denied = _admin(request)
    if denied:
        return denied
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("Некорректный запрос")
        password = _issued_password()
        account = create_rumex_dispatcher_account(
            username=body.get("username"), full_name=body.get("full_name"),
            max_user_id=body.get("max_user_id"), password_hash=_password_hash(password),
            actor_name=str(identity["username"]),
        )
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)
    return _json({"ok": True, "dispatcher": account, "issued_password": password}, 201)


async def handle_dispatcher_update(request: web.Request) -> web.Response:
    identity, denied = _admin(request)
    if denied:
        return denied
    username = request.match_info.get("username", "")
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError("Некорректный запрос")
        account = update_rumex_dispatcher_account(
            username=username, full_name=body.get("full_name"), max_user_id=body.get("max_user_id"),
            active=body.get("active"), actor_name=str(identity["username"]),
        )
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)
    return _json({"ok": True, "dispatcher": account})


async def handle_dispatcher_reset_password(request: web.Request) -> web.Response:
    identity, denied = _admin(request)
    if denied:
        return denied
    username = request.match_info.get("username", "")
    try:
        body = await request.json()
        if not isinstance(body, dict) or not _step_up_confirmed(body):
            return _json({"error": "Требуется PIN подтверждения"}, 403)
        password = _issued_password()
        reset_rumex_dispatcher_password(
            username=username, password_hash=_password_hash(password), actor_name=str(identity["username"])
        )
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)
    return _json({"ok": True, "issued_password": password})


async def handle_dispatcher_security(request: web.Request) -> web.Response:
    _identity, denied = _admin(request)
    if denied:
        return denied
    username = request.match_info.get("username", "")
    if get_rumex_dispatcher_account(username) is None:
        return _json({"error": "Диспетчер не найден"}, 404)
    return _json(rumex_dispatcher_security_summary(username))


async def handle_dispatcher_sessions_revoke(request: web.Request) -> web.Response:
    identity, denied = _admin(request)
    if denied:
        return denied
    username = request.match_info.get("username", "")
    try:
        body = await request.json()
        if not isinstance(body, dict) or not _step_up_confirmed(body):
            return _json({"error": "Требуется PIN подтверждения"}, 403)
        count = revoke_rumex_dispatcher_sessions(username=username, actor_name=str(identity["username"]))
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)
    return _json({"ok": True, "revoked_sessions": count})


async def handle_vehicle_block(request: web.Request) -> web.Response:
    identity, denied = _admin(request)
    if denied:
        return denied
    tail = request.match_info.get("plate_tail", "")
    try:
        body = await request.json()
        if not isinstance(body, dict) or not _step_up_confirmed(body):
            return _json({"error": "Требуется PIN подтверждения"}, 403)
        if not isinstance(body.get("blocked"), bool):
            raise ValueError("Укажите статус блокировки")
        set_rumex_vehicle_shipment_block(
            plate_tail=tail, blocked=body["blocked"], reason=body.get("reason"),
            actor_name=str(identity["username"]),
        )
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)
    return _json({"ok": True, "vehicles": list_document_fleet_vehicles()})


def register_rumex_management_routes(app: web.Application) -> None:
    app.router.add_get("/api/rumex-management/profile", handle_profile)
    app.router.add_get("/api/rumex-management/overview", handle_overview)
    app.router.add_post("/api/rumex-management/dispatchers", handle_dispatcher_create)
    app.router.add_put("/api/rumex-management/dispatchers/{username}", handle_dispatcher_update)
    app.router.add_get("/api/rumex-management/dispatchers/{username}/security", handle_dispatcher_security)
    app.router.add_post("/api/rumex-management/dispatchers/{username}/password", handle_dispatcher_reset_password)
    app.router.add_post("/api/rumex-management/dispatchers/{username}/sessions/revoke", handle_dispatcher_sessions_revoke)
    app.router.add_post("/api/rumex-management/vehicles/{plate_tail}/shipment-block", handle_vehicle_block)
