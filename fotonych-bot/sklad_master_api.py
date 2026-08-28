"""HTTP API for the ``Склад Мастер`` MAX mini application."""

from __future__ import annotations

import logging
import os
from typing import Any, Callable

from aiohttp import web

from drivers_chat import notify_admin_plain
from materials_chat import notify_event
from materials_receipt_chat import notify_materials_role_users
from max_webapp import display_name_from_user, user_id_from_user, validate_init_data
from sklad_master_store import (
    create_delivery, create_material, create_request, create_supplier, dashboard,
    get_access, get_delivery, get_request, init_sklad_master_db, list_materials,
    list_movements, list_requests,
    receive_delivery, record_inventory_adjustment, record_issue,
    record_receipt_batch, record_transfer, set_role, set_site_material_minimum, transition_request,
    update_material, update_site,
)

logger = logging.getLogger(__name__)


def _json(data: dict, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _parse_user(request: web.Request) -> tuple[dict | None, int | None]:
    raw = request.headers.get("X-Max-Init-Data", "").strip()
    raw = raw or request.rel_url.query.get("initData", "").strip()
    parsed = validate_init_data(raw, os.getenv("MAX_BOT_TOKEN", "")) if raw else None
    user = parsed.get("user") if isinstance(parsed, dict) else None
    return (user, user_id_from_user(user)) if isinstance(user, dict) else (None, None)


def _auth(request: web.Request, *actions: str) -> tuple[dict, int, str, dict] | web.Response:
    user, uid = _parse_user(request)
    if user is None or uid is None:
        return _json({"error": "open in MAX mini-app"}, 401)
    access = get_access(uid)
    if not access["roles"]:
        return _json({"error": "Нет доступа к Склад Мастер"}, 403)
    if actions and not any(action in access["allowed_actions"] for action in actions):
        return _json({"error": "Недостаточно прав"}, 403)
    return user, uid, display_name_from_user(user), access


def _site_allowed(access: dict, site_id: int) -> bool:
    return bool(access["all_sites"] or int(site_id) in access["site_ids"])


def _idempotency(request: web.Request, body: dict | None = None) -> str | None:
    value = request.headers.get("Idempotency-Key", "").strip()
    return value or str((body or {}).get("idempotency_key") or "").strip() or None


async def _body(request: web.Request) -> dict:
    data = await request.json()
    if not isinstance(data, dict):
        raise ValueError("invalid json")
    return data


async def _notify(
    lines: list[str],
    *,
    admin: bool = True,
    event: bool = True,
    role: str | None = None,
    exclude_user_id: int | None = None,
) -> None:
    text = "\n".join(x for x in lines if x).strip()
    if not text:
        return
    if event:
        try:
            await notify_event(text)
        except Exception:
            logger.exception("Склад Мастер: уведомление в чат не отправлено")
    if admin:
        try:
            await notify_admin_plain(text)
        except Exception:
            logger.exception("Склад Мастер: уведомление админу не отправлено")
    if role:
        try:
            await notify_materials_role_users(
                text,
                role=role,
                exclude_user_id=exclude_user_id,
            )
        except Exception:
            logger.exception("Склад Мастер: уведомление роли %s не отправлено", role)


async def _notify_stock_alerts(alerts: list[dict], *, actor_id: int | None = None) -> None:
    for alert in alerts or []:
        status = str(alert.get("status") or "")
        title = "🔴 Критический остаток" if status == "critical" else "🟠 Остаток достиг минимума"
        await _notify(
            [
                f"{title} · Склад Мастер",
                f"Площадка: {alert.get('site_name') or '—'}",
                f"Материал: {alert.get('material_name') or '—'}",
                f"Остаток: {alert.get('balance')} {alert.get('unit') or ''}",
                f"Минимум: {alert.get('min_level')} {alert.get('unit') or ''}",
            ],
            admin=False,
            role="supply",
            exclude_user_id=actor_id,
        )


def _role_for_transition(access: dict, requested: str | None = None) -> str:
    if requested and requested in access["roles"]:
        return requested
    for role in ("admin", "supply", "master"):
        if role in access["roles"]:
            return role
    raise ValueError("Нет роли")


STATUS_LABELS = {
    "draft": "Черновик", "submitted": "Новая", "accepted": "Принята",
    "in_transit": "В пути", "partially_received": "Принята частично",
    "received": "Получена", "closed": "Закрыта", "rejected": "Отклонена",
    "cancelled": "Отменена",
}


def _quantity_label(value: Any) -> str:
    number = round(float(value or 0), 3)
    return str(int(number)) if number.is_integer() else f"{number:.3f}".rstrip("0").rstrip(".")


def _request_item_lines(item: dict) -> list[str]:
    return [
        f"• {position.get('material_name') or 'Материал'}: "
        f"{_quantity_label(position.get('ordered', position.get('quantity')))} "
        f"{position.get('material_unit') or ''}".rstrip()
        for position in item.get("items") or []
    ]


def _delivery_item_lines(item: dict) -> list[str]:
    return [
        f"• {position.get('material_name') or 'Материал'}: "
        f"{_quantity_label(position.get('quantity'))} "
        f"{position.get('material_unit') or ''}".rstrip()
        for position in item.get("items") or []
    ]


def _request_actions(item: dict, access: dict) -> list[str]:
    status = item.get("status")
    actions: list[str] = []
    capabilities = access.get("capabilities") or {}
    if status == "draft" and capabilities.get("request_create"):
        actions.extend(["submit", "cancel"])
    if status == "submitted":
        if capabilities.get("request_manage"):
            actions.extend(["accept", "reject"])
        if capabilities.get("request_create"):
            actions.append("cancel")
    if status in {"accepted", "in_transit", "partially_received"} and capabilities.get("delivery_create"):
        actions.append("create_delivery")
    if status == "accepted" and capabilities.get("request_manage"):
        actions.append("mark_in_transit")
    if status in {"in_transit", "partially_received"} and capabilities.get("request_receive"):
        actions.append("receive_delivery")
    if status == "received" and (
        capabilities.get("request_manage") or capabilities.get("request_receive")
    ):
        actions.append("close")
    return list(dict.fromkeys(actions))


def _decorate_request(item: dict, access: dict) -> dict:
    result = dict(item)
    result["status_label"] = STATUS_LABELS.get(str(item.get("status")), str(item.get("status") or ""))
    result["allowed_actions"] = _request_actions(item, access)
    return result


async def handle_bootstrap(request: web.Request) -> web.Response:
    auth = _auth(request, "view")
    if isinstance(auth, web.Response):
        return auth
    _user, uid, name, access = auth
    try:
        raw = request.rel_url.query.get("site_id")
        site_id = int(raw) if raw else None
        if site_id is None and not access["all_sites"] and access["site_ids"]:
            site_id = int(access["site_ids"][0])
        if site_id and not _site_allowed(access, site_id):
            return _json({"error": "Нет доступа к площадке"}, 403)
        data = dashboard(site_id=site_id)
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)
    if not access["all_sites"]:
        allowed = set(access["site_ids"])
        data["sites"] = [x for x in data["sites"] if x["id"] in allowed]
        data["site_summaries"] = [x for x in data["site_summaries"] if x["site_id"] in allowed]
    data.update(user={"id": uid, "name": name}, access=access, roles=access["roles"],
                capabilities=access["capabilities"],
                allowed_actions=access["allowed_actions"])
    if access["capabilities"].get("settings_manage"):
        data["admin_materials"] = list_materials(include_inactive=True)
    data["requests"] = [_decorate_request(item, access) for item in data.get("requests", [])]
    return _json(data)


async def handle_materials(request: web.Request) -> web.Response:
    auth = _auth(request, "settings_manage", "receipt")
    if isinstance(auth, web.Response):
        return auth
    try:
        body = await _body(request)
        item = create_material(name=body.get("name", ""), unit=body.get("unit", "шт"),
                               min_level=body.get("min_level", 0))
        return _json({"material": item}, 201)
    except (TypeError, ValueError) as exc:
        return _json({"error": str(exc)}, 400)


async def handle_suppliers(request: web.Request) -> web.Response:
    auth = _auth(request, "settings_manage", "receipt")
    if isinstance(auth, web.Response):
        return auth
    try:
        item = create_supplier(name=(await _body(request)).get("name", ""))
        return _json({"supplier": item}, 201)
    except (TypeError, ValueError) as exc:
        return _json({"error": str(exc)}, 400)


async def _command(request: web.Request, action: str, function: Callable[..., dict],
                   site_fields: tuple[str, ...], notify_title: str,
                   response_key: str = "movement") -> web.Response:
    auth = _auth(request, action)
    if isinstance(auth, web.Response):
        return auth
    _user, uid, name, access = auth
    try:
        body = await _body(request)
        for field in site_fields:
            if not _site_allowed(access, int(body[field])):
                return _json({"error": "Нет доступа к площадке"}, 403)
        kwargs = dict(body)
        kwargs.pop("idempotency_key", None)
        kwargs.update(actor_max_id=uid, actor_name=name,
                      idempotency_key=_idempotency(request, body))
        result = function(**kwargs)
    except (KeyError, TypeError, ValueError) as exc:
        return _json({"error": str(exc)}, 400)
    replay = bool(result.get("_idempotent_replay"))
    if not replay:
        target_role = "supply" if action == "receipt" else None
        await _notify(
            [notify_title, f"Пользователь: {name} (id {uid})"],
            role=target_role,
            exclude_user_id=uid,
        )
        await _notify_stock_alerts(result.get("stock_alerts") or [], actor_id=uid)
    result.pop("_idempotent_replay", None)
    return _json({response_key: result}, 201)


async def handle_receipt(request: web.Request) -> web.Response:
    auth = _auth(request, "receipt")
    if isinstance(auth, web.Response):
        return auth
    _user, uid, name, access = auth
    try:
        body = await _body(request)
        site_id = int(body["site_id"])
        if not _site_allowed(access, site_id):
            return _json({"error": "Нет доступа к площадке"}, 403)
        items = body.get("items")
        if not isinstance(items, list):
            items = [{
                "material_id": int(body["material_id"]),
                "quantity": body["quantity"],
            }]
        result = record_receipt_batch(
            site_id=site_id,
            supplier_id=int(body["supplier_id"]),
            items=items,
            actor_max_id=uid,
            actor_name=name,
            note=str(body.get("note") or ""),
            idempotency_key=_idempotency(request, body),
        )
    except (KeyError, TypeError, ValueError) as exc:
        return _json({"error": str(exc)}, 400)

    replay = result.pop("_idempotent_replay", False)
    if not replay:
        lines = [
            "📦 Склад Мастер · приход",
            f"Площадка: {result['site']['name']}",
            f"Поставщик: {result['supplier']['name']}",
            "Позиции:",
        ]
        lines.extend(
            f"• {item['material']['name']}: {item['quantity']} {item['material']['unit']}"
            for item in result["items"]
        )
        lines.extend([
            f"Принял: {name} (id {uid})",
            f"Примечание: {result['note']}" if result.get("note") else "",
        ])
        await _notify(
            lines,
            role="supply",
            exclude_user_id=uid,
        )
    return _json({"receipt": result}, 201)


async def handle_issue(request: web.Request) -> web.Response:
    return await _command(request, "issue", record_issue, ("site_id",),
                          "📤 Склад Мастер · выдача")


async def handle_transfer(request: web.Request) -> web.Response:
    return await _command(request, "transfer", record_transfer,
                          ("from_site_id", "to_site_id"), "🔄 Склад Мастер · перемещение")


async def handle_adjustment(request: web.Request) -> web.Response:
    auth = _auth(request, "inventory_adjustment")
    if isinstance(auth, web.Response):
        return auth
    _user, uid, name, access = auth
    try:
        body = await _body(request)
        if not _site_allowed(access, int(body["site_id"])):
            return _json({"error": "Нет доступа к площадке"}, 403)
        result = record_inventory_adjustment(
            site_id=int(body["site_id"]), material_id=int(body["material_id"]),
            quantity_delta=body["quantity_delta"], actor_max_id=uid, actor_name=name,
            note=str(body.get("note") or ""), allow_negative=bool(body.get("allow_negative")),
            idempotency_key=_idempotency(request, body),
        )
    except (KeyError, TypeError, ValueError) as exc:
        return _json({"error": str(exc)}, 400)
    replay = result.pop("_idempotent_replay", False)
    if not replay:
        await _notify(["🧮 Склад Мастер · корректировка", f"Пользователь: {name} (id {uid})"])
        await _notify_stock_alerts(result.get("stock_alerts") or [], actor_id=uid)
    return _json({"movement": result}, 201)


async def handle_request_create(request: web.Request) -> web.Response:
    auth = _auth(request, "request_create")
    if isinstance(auth, web.Response):
        return auth
    _user, uid, name, access = auth
    try:
        body = await _body(request)
        site_id = int(body["site_id"])
        if not _site_allowed(access, site_id):
            return _json({"error": "Нет доступа к площадке"}, 403)
        item = create_request(
            site_id=site_id, material_id=body.get("material_id"), quantity=body.get("quantity"),
            items=body.get("items"), urgency=str(body.get("urgency") or "plan"),
            actor_max_id=uid, actor_name=name, comment=str(body.get("comment") or ""),
            status=str(body.get("status") or "submitted"),
            idempotency_key=_idempotency(request, body),
        )
    except (KeyError, TypeError, ValueError) as exc:
        return _json({"error": str(exc)}, 400)
    replay = item.pop("_idempotent_replay", False)
    if not replay:
        lines = [
            "🟡 Склад Мастер · новая заявка",
            f"Заявка #{item['id']}",
            f"Площадка: {item['site_name']}",
            "Запрошено:",
            *_request_item_lines(item),
            f"Срочность: {'срочно' if item['urgency'] == 'urgent' else 'планово'}",
            f"Создал: {name} (id {uid})",
            f"Комментарий: {item['comment']}" if item.get("comment") else "",
        ]
        await _notify(
            lines,
            role="supply",
            exclude_user_id=uid,
        )
    return _json({"request": item}, 201)


async def handle_requests(request: web.Request) -> web.Response:
    auth = _auth(request, "view")
    if isinstance(auth, web.Response):
        return auth
    _user, _uid, _name, access = auth
    try:
        site_id = int(request.query["site_id"]) if request.query.get("site_id") else None
        if site_id and not _site_allowed(access, site_id):
            return _json({"error": "Нет доступа к площадке"}, 403)
        items = list_requests(site_id=site_id, status=request.query.get("status"),
                              limit=int(request.query.get("limit", 30)),
                              offset=int(request.query.get("offset", 0)))
        if not access["all_sites"]:
            items = [x for x in items if x["site_id"] in access["site_ids"]]
        return _json({"requests": [_decorate_request(item, access) for item in items]})
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)


async def handle_request_detail(request: web.Request) -> web.Response:
    auth = _auth(request, "view")
    if isinstance(auth, web.Response):
        return auth
    _user, _uid, _name, access = auth
    try:
        item = get_request(int(request.match_info["request_id"]))
        if not _site_allowed(access, item["site_id"]):
            return _json({"error": "Нет доступа к площадке"}, 403)
        return _json({"request": _decorate_request(item, access)})
    except ValueError as exc:
        return _json({"error": str(exc)}, 404)


async def handle_transition(request: web.Request) -> web.Response:
    auth = _auth(request, "request_create", "request_manage", "request_receive")
    if isinstance(auth, web.Response):
        return auth
    _user, uid, name, access = auth
    try:
        body = await _body(request)
        request_id = int(request.match_info["request_id"])
        current = get_request(request_id)
        if not _site_allowed(access, current["site_id"]):
            return _json({"error": "Нет доступа к площадке"}, 403)
        item = transition_request(
            request_id=request_id, new_status=str(body["status"]),
            actor_role=_role_for_transition(access, body.get("role")),
            actor_max_id=uid, actor_name=name, comment=str(body.get("comment") or ""),
            idempotency_key=_idempotency(request, body),
        )
    except (KeyError, TypeError, ValueError) as exc:
        return _json({"error": str(exc)}, 400)
    replay = item.pop("_idempotent_replay", False)
    if not replay:
        target_role = "master" if item["status"] in {
            "accepted", "in_transit", "rejected", "cancelled",
        } else "supply"
        status_label = STATUS_LABELS.get(item["status"], item["status"])
        action_label = "Принял заявку" if item["status"] == "accepted" else "Изменил статус"
        lines = [
            "🔵 Склад Мастер · статус заявки",
            f"Заявка #{item['id']}: {status_label}",
            f"Площадка: {item['site_name']}",
            "Позиции заявки:",
            *_request_item_lines(item),
            f"{action_label}: {name} (id {uid})",
            f"Комментарий: {item['supply_comment']}" if item.get("supply_comment") else "",
        ]
        await _notify(
            lines,
            role=target_role,
            exclude_user_id=uid,
        )
    return _json({"request": _decorate_request(item, access)})


async def handle_delivery_create(request: web.Request) -> web.Response:
    auth = _auth(request, "delivery_create")
    if isinstance(auth, web.Response):
        return auth
    _user, uid, name, access = auth
    try:
        body = await _body(request)
        request_id = int(request.match_info["request_id"])
        current = get_request(request_id)
        if not _site_allowed(access, current["site_id"]):
            return _json({"error": "Нет доступа к площадке"}, 403)
        item = create_delivery(
            request_id=request_id, items=body.get("items") or [],
            supplier_id=int(body["supplier_id"]) if body.get("supplier_id") else None,
            actor_max_id=uid, actor_name=name, note=str(body.get("note") or ""),
            idempotency_key=_idempotency(request, body),
        )
    except (KeyError, TypeError, ValueError) as exc:
        return _json({"error": str(exc)}, 400)
    replay = item.pop("_idempotent_replay", False)
    if not replay:
        lines = [
            "🚚 Склад Мастер · создана поставка",
            f"Заявка #{request_id}",
            f"Площадка: {current['site_name']}",
            f"Поставщик: {item.get('supplier_name') or 'не указан'}",
            "Отправлено:",
            *_delivery_item_lines(item),
            f"Оформил: {name} (id {uid})",
            f"Комментарий: {item['note']}" if item.get("note") else "",
        ]
        await _notify(
            lines,
            role="master",
            exclude_user_id=uid,
        )
    return _json({"delivery": item}, 201)


async def handle_delivery_receive(request: web.Request) -> web.Response:
    auth = _auth(request, "request_receive")
    if isinstance(auth, web.Response):
        return auth
    _user, uid, name, access = auth
    try:
        body = await _body(request)
        current = get_delivery(int(request.match_info["delivery_id"]))
        if not _site_allowed(access, current["request"]["site_id"]):
            return _json({"error": "Нет доступа к площадке"}, 403)
        item = receive_delivery(
            delivery_id=int(request.match_info["delivery_id"]), items=body.get("items"),
            actor_max_id=uid, actor_name=name, note=str(body.get("note") or ""),
            idempotency_key=_idempotency(request, body),
        )
    except (KeyError, TypeError, ValueError) as exc:
        return _json({"error": str(exc)}, 400)
    replay = item.pop("_idempotent_replay", False)
    if not replay:
        request_doc = item["request"]
        complete = request_doc["status"] in {"received", "closed"}
        lines = [
            "✅ Склад Мастер · поставка принята",
            f"Заявка #{request_doc['id']} · поставка #{item['id']}",
            f"Площадка: {request_doc['site_name']}",
            f"Поставщик: {item.get('supplier_name') or 'не указан'}",
            "Принято сейчас:",
            *_delivery_item_lines({"items": item.get("received_now") or []}),
            f"Исполнение заявки: {'полностью' if complete else 'частично'}",
        ]
        if not complete:
            remaining = [
                position
                for position in request_doc.get("items") or []
                if float(position.get("remaining") or 0) > 0
            ]
            lines.append("Осталось получить:")
            lines.extend(
                f"• {position.get('material_name') or 'Материал'}: "
                f"{_quantity_label(position.get('remaining'))} "
                f"{position.get('material_unit') or ''}".rstrip()
                for position in remaining
            )
        lines.extend([
            f"Принял: {name} (id {uid})",
            f"Комментарий: {body.get('note')}" if body.get("note") else "",
        ])
        await _notify(
            lines,
            role="supply",
            exclude_user_id=uid,
        )
    return _json({"delivery": item})


async def handle_movements(request: web.Request) -> web.Response:
    auth = _auth(request, "view")
    if isinstance(auth, web.Response):
        return auth
    _user, _uid, _name, access = auth
    try:
        value = lambda key: int(request.query[key]) if request.query.get(key) else None
        site_id = value("site_id")
        if site_id and not _site_allowed(access, site_id):
            return _json({"error": "Нет доступа к площадке"}, 403)
        result = list_movements(site_id=site_id, material_id=value("material_id"),
                                op_type=request.query.get("op_type"),
                                request_id=value("request_id"),
                                limit=int(request.query.get("limit", 50)),
                                offset=int(request.query.get("offset", 0)))
        if not access["all_sites"]:
            result["items"] = [x for x in result["items"] if x["site_id"] in access["site_ids"]]
            result["total"] = len(result["items"])
        return _json(result)
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)


async def handle_admin_material(request: web.Request) -> web.Response:
    auth = _auth(request, "settings_manage")
    if isinstance(auth, web.Response):
        return auth
    _user, uid, name, _access = auth
    try:
        return _json({
            "material": update_material(
                int(request.match_info["material_id"]),
                actor_max_id=uid,
                actor_name=name,
                **await _body(request),
            )
        })
    except (TypeError, ValueError) as exc:
        return _json({"error": str(exc)}, 400)


async def handle_admin_site(request: web.Request) -> web.Response:
    auth = _auth(request, "settings_manage")
    if isinstance(auth, web.Response):
        return auth
    try:
        return _json({"site": update_site(int(request.match_info["site_id"]),
                                          **await _body(request))})
    except (TypeError, ValueError) as exc:
        return _json({"error": str(exc)}, 400)


async def handle_admin_site_material(request: web.Request) -> web.Response:
    auth = _auth(request, "settings_manage")
    if isinstance(auth, web.Response):
        return auth
    _user, uid, name, _access = auth
    try:
        body = await _body(request)
        result = set_site_material_minimum(
            site_id=int(request.match_info["site_id"]),
            material_id=int(request.match_info["material_id"]),
            min_level=body.get("min_level", 0),
            actor_max_id=uid,
            actor_name=name,
        )
    except (TypeError, ValueError) as exc:
        return _json({"error": str(exc)}, 400)
    await _notify_stock_alerts(result.get("stock_alerts") or [], actor_id=uid)
    return _json({"setting": result})


async def handle_admin_role(request: web.Request) -> web.Response:
    auth = _auth(request, "roles_manage")
    if isinstance(auth, web.Response):
        return auth
    _user, uid, name, _access = auth
    try:
        body = await _body(request)
        result = set_role(max_id=int(body["max_id"]), role=str(body["role"]),
                          site_ids=[int(x) for x in body.get("site_ids", [])],
                          active=bool(body.get("active", True)),
                          actor_max_id=uid, actor_name=name)
        return _json({"access": result})
    except (KeyError, TypeError, ValueError) as exc:
        return _json({"error": str(exc)}, 400)


def register_sklad_master_routes(app: web.Application) -> None:
    init_sklad_master_db()
    prefix = "/api/sklad-master"
    app.router.add_get(f"{prefix}/bootstrap", handle_bootstrap)
    app.router.add_post(f"{prefix}/materials", handle_materials)
    app.router.add_post(f"{prefix}/suppliers", handle_suppliers)
    app.router.add_post(f"{prefix}/receipts", handle_receipt)
    app.router.add_post(f"{prefix}/issues", handle_issue)
    app.router.add_post(f"{prefix}/issue", handle_issue)
    app.router.add_post(f"{prefix}/transfers", handle_transfer)
    app.router.add_post(f"{prefix}/transfer", handle_transfer)
    app.router.add_post(f"{prefix}/inventory-adjustments", handle_adjustment)
    app.router.add_post(f"{prefix}/inventory-adjustment", handle_adjustment)
    app.router.add_get(f"{prefix}/movements", handle_movements)
    app.router.add_get(f"{prefix}/requests", handle_requests)
    app.router.add_post(f"{prefix}/requests", handle_request_create)
    app.router.add_get(f"{prefix}/requests/{{request_id}}", handle_request_detail)
    app.router.add_patch(f"{prefix}/requests/{{request_id}}", handle_transition)
    app.router.add_post(f"{prefix}/requests/{{request_id}}/transition", handle_transition)
    app.router.add_post(f"{prefix}/requests/{{request_id}}/deliveries", handle_delivery_create)
    app.router.add_post(f"{prefix}/deliveries/{{delivery_id}}/receive", handle_delivery_receive)
    app.router.add_patch(f"{prefix}/admin/materials/{{material_id}}", handle_admin_material)
    app.router.add_patch(f"{prefix}/admin/sites/{{site_id}}", handle_admin_site)
    app.router.add_put(
        f"{prefix}/admin/sites/{{site_id}}/materials/{{material_id}}",
        handle_admin_site_material,
    )
    app.router.add_post(f"{prefix}/admin/roles", handle_admin_role)


# Compatibility aliases used by older imports/tests.
handle_sklad_master_bootstrap = handle_bootstrap
handle_sklad_master_materials = handle_materials
handle_sklad_master_suppliers = handle_suppliers
handle_sklad_master_receipt = handle_receipt
handle_sklad_master_request = handle_request_create
