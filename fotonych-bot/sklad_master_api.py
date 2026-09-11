"""HTTP API for the ``Склад Мастер`` MAX mini application."""

from __future__ import annotations

import logging
import os
from typing import Any, Callable

from aiohttp import web

from drivers_chat import notify_admin_plain
from materials_chat import notify_master_receipt
from materials_receipt_chat import notify_materials_role_users
from max_webapp import display_name_from_user, user_id_from_user, validate_init_data
from sklad_master_store import (
    create_delivery, create_material, create_request, create_supplier, dashboard, edit_receipt,
    cancel_receipt,
    get_access, get_access_request_status, get_delivery, get_movement, get_receipt, get_request, get_request_timeline,
    get_audit_entry, init_sklad_master_db,
    list_payment_receipts, list_audit_log, list_materials,
    list_movements, list_requests, list_roles, list_sites, list_suppliers,
    find_similar_receipt_today,
    find_open_request_for_material,
    price_receipt, receive_delivery, record_inventory_adjustment, record_issue,
    record_receipt_batch, record_transfer, send_receipt_to_manager, set_role,
    set_site_material_minimum, transition_request, update_request_eta,
    update_material, update_site,
)
from sklad_master_access_chat import submit_and_notify_access_request

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
    role: str | None = None,
    exclude_user_id: int | None = None,
) -> None:
    text = "\n".join(x for x in lines if x).strip()
    if not text:
        return
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


async def _notify_manager_private(
    lines: list[str],
    *,
    exclude_user_id: int | None = None,
) -> None:
    text = "\n".join(x for x in lines if x).strip()
    if not text:
        return
    try:
        await notify_materials_role_users(
            text,
            role="manager",
            exclude_user_id=exclude_user_id,
        )
    except Exception:
        logger.exception("Склад Мастер: уведомление руководителю не отправлено")


def _money(value: Any) -> str:
    amount = round(float(value or 0), 2)
    if abs(amount - round(amount)) < 0.01:
        return f"{int(round(amount)):,}".replace(",", " ") + " ₽"
    return f"{amount:,.2f}".replace(",", " ") + " ₽"


def _receipt_item_lines(receipt: dict) -> list[str]:
    lines = []
    for item in receipt.get("items") or []:
        qty = _quantity_label(item.get("quantity"))
        unit = item.get("quantity_unit") or item.get("material_unit") or ""
        bill_qty = item.get("billing_quantity")
        bill_unit = item.get("billing_unit") or unit
        extra = ""
        if bill_qty not in (None, ""):
            try:
                same_qty = abs(float(bill_qty) - float(item.get("quantity") or 0)) < 1e-9
            except (TypeError, ValueError):
                same_qty = True
            if not same_qty or bill_unit != unit:
                extra = f" (к оплате: {_quantity_label(bill_qty)} {bill_unit})"
        lines.append(
            f"• {item.get('material_name') or 'Материал'}: {qty} {unit}{extra}".rstrip()
        )
    return lines


def _receipt_date_label(receipt: dict) -> str:
    from datetime import datetime
    stamp = receipt.get("document_date") or receipt.get("created_at")
    if not stamp:
        return ""
    return datetime.fromtimestamp(float(stamp)).strftime("%d.%m.%Y")


def _receipt_public_lines(receipt: dict, *, header: str | None = None) -> list[str]:
    date_label = _receipt_date_label(receipt)
    lines = [
        header or "📦 Склад Мастер · приход",
        f"Приход №{receipt.get('id')} · {date_label}".strip(),
        f"Площадка: {receipt.get('site_name') or receipt.get('site', {}).get('name') or '—'}",
        f"Поставщик: {receipt.get('supplier_name') or receipt.get('supplier', {}).get('name') or '—'}",
        f"Принял: {receipt.get('actor_name') or '—'}",
        "Позиции:",
    ]
    lines.extend(_receipt_item_lines(receipt))
    if receipt.get("note"):
        lines.append(f"Примечание: {receipt['note']}")
    return lines


def _receipt_payment_lines(receipt: dict, *, actor_name: str = "") -> list[str]:
    date_label = _receipt_date_label(receipt)
    lines = [
        "💰 Склад Мастер · к оплате",
        f"Приход №{receipt.get('id')} · {date_label}".strip(),
        f"Площадка: {receipt.get('site_name') or '—'}",
        f"Поставщик: {receipt.get('supplier_name') or '—'}",
        f"Принял: {receipt.get('actor_name') or '—'}",
    ]
    if actor_name:
        lines.append(f"Оформил: {actor_name}")
    lines.append("Итого к оплате:")
    for index, item in enumerate(receipt.get("items") or [], 1):
        bill_qty = _quantity_label(item.get("billing_quantity", item.get("quantity")))
        bill_unit = item.get("billing_unit") or item.get("quantity_unit") or ""
        unit_price = round(float(item.get("unit_price") or 0), 2)
        line_total = round(float(item.get("line_total") or 0), 2)
        lines.append(
            f"{index}. {item.get('material_name') or 'Материал'} — "
            f"{bill_qty} {bill_unit} × {_money(unit_price).replace(' ₽', '')} = {_money(line_total)}"
        )
    lines.append(f"Итого: {_money(receipt.get('total_amount'))}")
    return lines


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
    "in_transit": "В пути", "partially_received": "Частично на базе",
    "received": "На базе", "closed": "Закрыта", "rejected": "Отклонена",
    "cancelled": "Отменена",
}

ROLE_HINTS = {
    "master": "Приём материалов на площадках, расход и заявки на снабжение",
    "supply": "Закупки у поставщиков, заявки, цены и отправка руководителю",
    "manager": "Ведомости к оплате в личных сообщениях",
    "admin": "Контроль процесса, настройки и замена ролей",
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
    if status in {"in_transit", "partially_received"} and capabilities.get("request_manage"):
        actions.append("update_eta")
    if status == "received" and (
        capabilities.get("request_receive") or capabilities.get("roles_manage")
    ):
        actions.append("close")
    return list(dict.fromkeys(actions))


def _eta_lines(item: dict) -> list[str]:
    lines = []
    if item.get("eta_date_label"):
        lines.append(f"Ожидаем ~{item['eta_date_label']}")
    if item.get("eta_label"):
        lines.append(item["eta_label"])
    return lines


def _decorate_request(item: dict, access: dict) -> dict:
    result = dict(item)
    result["status_label"] = STATUS_LABELS.get(str(item.get("status")), str(item.get("status") or ""))
    result["allowed_actions"] = _request_actions(item, access)
    return result


async def handle_bootstrap(request: web.Request) -> web.Response:
    user, uid = _parse_user(request)
    if user is None or uid is None:
        return _json({"error": "open in MAX mini-app"}, 401)
    name = display_name_from_user(user)
    access = get_access(uid)
    if not access["roles"]:
        return _json(
            {
                "guest": True,
                "user": {"id": uid, "name": name},
                "access_request": get_access_request_status(uid),
            }
        )
    auth = user, uid, name, access
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
                allowed_actions=access["allowed_actions"],
                role_hint=ROLE_HINTS.get(access.get("role") or "", ""))
    if access["capabilities"].get("settings_manage"):
        data["admin_materials"] = list_materials(include_inactive=True)
    if access["capabilities"].get("roles_manage"):
        data["admin_roles"] = list_roles(include_inactive=True)
        data["audit_log"] = list_audit_log(limit=50)["items"]
    if access["capabilities"].get("receipt_price") or access["capabilities"].get("payment_view"):
        data["payment_receipts"] = list_payment_receipts(site_id=site_id)
    data["requests"] = [_decorate_request(item, access) for item in data.get("requests", [])]
    return _json(data)


async def handle_access_request(request: web.Request) -> web.Response:
    user, uid = _parse_user(request)
    if user is None or uid is None:
        return _json({"error": "open in MAX mini-app"}, 401)
    name = display_name_from_user(user)
    ok, message = await submit_and_notify_access_request(max_id=uid, display_name=name)
    return _json(
        {
            "ok": ok,
            "notification": message,
            "access_request": get_access_request_status(uid),
        }
    )


async def handle_materials(request: web.Request) -> web.Response:
    auth = _auth(request, "settings_manage")
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
    auth = _auth(request, "settings_manage")
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
            document_date=body.get("document_date"),
            idempotency_key=_idempotency(request, body),
        )
    except (KeyError, TypeError, ValueError) as exc:
        return _json({"error": str(exc)}, 400)

    replay = result.pop("_idempotent_replay", False)
    if not replay:
        receipt = get_receipt(int(result["id"]))
        lines = _receipt_public_lines(receipt)
        if "master" in (access.get("roles") or []):
            try:
                await notify_master_receipt("\n".join(lines))
            except Exception:
                logger.exception("Склад Мастер: приход в чат не отправлен")
        await _notify(
            lines,
            admin=False,
            role="supply",
            exclude_user_id=uid,
        )
    return _json({"receipt": get_receipt(int(result["id"]))}, 201)


async def handle_receipt_detail(request: web.Request) -> web.Response:
    auth = _auth(request, "receipt", "receipt_price", "payment_view")
    if isinstance(auth, web.Response):
        return auth
    _user, _uid, _name, access = auth
    try:
        item = get_receipt(int(request.match_info["receipt_id"]))
    except (TypeError, ValueError) as exc:
        return _json({"error": str(exc)}, 404 if "не найден" in str(exc) else 400)
    if not _site_allowed(access, item["site_id"]):
        return _json({"error": "Нет доступа к площадке"}, 403)
    return _json({"receipt": item})


async def handle_receipt_edit(request: web.Request) -> web.Response:
    auth = _auth(request, "receipt")
    if isinstance(auth, web.Response):
        return auth
    _user, uid, name, access = auth
    try:
        body = await _body(request)
        current = get_receipt(int(request.match_info["receipt_id"]))
        if not _site_allowed(access, current["site_id"]):
            return _json({"error": "Нет доступа к площадке"}, 403)
        if current.get("is_cancelled"):
            return _json({"error": "Приход отменён"}, 400)
        items = body.get("items")
        if not isinstance(items, list):
            raise ValueError("Укажите позиции прихода")
        result = edit_receipt(
            receipt_id=int(request.match_info["receipt_id"]),
            supplier_id=int(body["supplier_id"]),
            items=items,
            note=str(body.get("note") or ""),
            document_date=body.get("document_date"),
            actor_max_id=uid,
            actor_name=name,
        )
    except (KeyError, TypeError, ValueError) as exc:
        return _json({"error": str(exc)}, 400)
    await _notify(
        _receipt_public_lines(result)[:1] + [
            "✏️ Склад Мастер · приход изменён",
            *(_receipt_public_lines(result)[1:]),
            f"Изменил: {name} (id {uid})",
        ],
        admin=False,
        role="supply",
        exclude_user_id=uid,
    )
    return _json({"receipt": result})


async def handle_receipt_cancel(request: web.Request) -> web.Response:
    auth = _auth(request, "roles_manage")
    if isinstance(auth, web.Response):
        return auth
    _user, uid, name, access = auth
    try:
        body = await _body(request)
        receipt_id = int(request.match_info["receipt_id"])
        current = get_receipt(receipt_id)
        if not _site_allowed(access, current["site_id"]):
            return _json({"error": "Нет доступа к площадке"}, 403)
        result = cancel_receipt(
            receipt_id=receipt_id,
            reason=str(body.get("reason") or ""),
            actor_max_id=uid,
            actor_name=name,
            idempotency_key=_idempotency(request, body),
        )
    except (KeyError, TypeError, ValueError) as exc:
        return _json({"error": str(exc)}, 400)
    replay = result.pop("_idempotent_replay", False)
    if not replay:
        lines = [
            "🚫 Склад Мастер · приход отменён",
            f"Приход №{receipt_id}",
            f"Площадка: {current.get('site_name') or '—'}",
            f"Поставщик: {current.get('supplier_name') or '—'}",
            f"Причина: {body.get('reason') or '—'}",
            f"Отменил: {name} (id {uid})",
        ]
        await _notify(lines, role="supply", exclude_user_id=uid)
        if current.get("payment_status") == "sent":
            await _notify(lines, role="manager", exclude_user_id=uid)
    return _json({"receipt": result})


async def handle_receipt_price(request: web.Request) -> web.Response:
    auth = _auth(request, "receipt_price")
    if isinstance(auth, web.Response):
        return auth
    _user, uid, name, access = auth
    try:
        body = await _body(request)
        current = get_receipt(int(request.match_info["receipt_id"]))
        if not _site_allowed(access, current["site_id"]):
            return _json({"error": "Нет доступа к площадке"}, 403)
        result = price_receipt(
            receipt_id=int(request.match_info["receipt_id"]),
            lines=body.get("items") or [],
            actor_max_id=uid,
            actor_name=name,
        )
    except (KeyError, TypeError, ValueError) as exc:
        return _json({"error": str(exc)}, 400)
    return _json({"receipt": result})


async def handle_receipt_send_manager(request: web.Request) -> web.Response:
    auth = _auth(request, "receipt_send_manager")
    if isinstance(auth, web.Response):
        return auth
    _user, uid, name, access = auth
    try:
        current = get_receipt(int(request.match_info["receipt_id"]))
        if not _site_allowed(access, current["site_id"]):
            return _json({"error": "Нет доступа к площадке"}, 403)
        result = send_receipt_to_manager(
            receipt_id=int(request.match_info["receipt_id"]),
            actor_max_id=uid,
            actor_name=name,
        )
    except (TypeError, ValueError) as exc:
        return _json({"error": str(exc)}, 400)
    await _notify_manager_private(
        _receipt_payment_lines(result, actor_name=name),
        exclude_user_id=uid,
    )
    await _notify(
        [
            f"✅ Склад Мастер · ведомость по приходу №{result.get('id')} отправлена руководителю",
            f"Поставщик: {result.get('supplier_name') or '—'}",
            f"Оформил: {name}",
        ],
        admin=False,
        exclude_user_id=uid,
    )
    return _json({"receipt": result})


async def handle_receipt_similar(request: web.Request) -> web.Response:
    auth = _auth(request, "receipt")
    if isinstance(auth, web.Response):
        return auth
    _user, _uid, _name, access = auth
    try:
        site_id = int(request.query["site_id"])
        supplier_id = int(request.query["supplier_id"])
        if not _site_allowed(access, site_id):
            return _json({"error": "Нет доступа к площадке"}, 403)
        exclude = int(request.query["exclude_receipt_id"]) if request.query.get("exclude_receipt_id") else None
        similar = find_similar_receipt_today(
            site_id=site_id,
            supplier_id=supplier_id,
            exclude_receipt_id=exclude,
        )
    except (KeyError, TypeError, ValueError) as exc:
        return _json({"error": str(exc)}, 400)
    return _json({"similar_receipt": similar})


async def handle_audit_log(request: web.Request) -> web.Response:
    auth = _auth(request, "roles_manage")
    if isinstance(auth, web.Response):
        return auth
    try:
        result = list_audit_log(
            limit=int(request.query.get("limit", 100)),
            offset=int(request.query.get("offset", 0)),
            entity_type=request.query.get("entity_type") or None,
        )
    except (TypeError, ValueError) as exc:
        return _json({"error": str(exc)}, 400)
    return _json(result)


async def handle_audit_detail(request: web.Request) -> web.Response:
    auth = _auth(request, "roles_manage")
    if isinstance(auth, web.Response):
        return auth
    try:
        item = get_audit_entry(int(request.match_info["audit_id"]))
    except ValueError as exc:
        return _json({"error": str(exc)}, 404)
    return _json({"audit": item})


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
            confirm_duplicate=bool(body.get("confirm_duplicate")),
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


async def handle_material_open_request(request: web.Request) -> web.Response:
    auth = _auth(request, "request_create", "view")
    if isinstance(auth, web.Response):
        return auth
    _user, _uid, _name, access = auth
    try:
        site_id = int(request.query["site_id"])
        material_id = int(request.match_info["material_id"])
        if not _site_allowed(access, site_id):
            return _json({"error": "Нет доступа к площадке"}, 403)
        open_request = find_open_request_for_material(site_id=site_id, material_id=material_id)
    except (KeyError, TypeError, ValueError) as exc:
        return _json({"error": str(exc)}, 400)
    if open_request:
        open_request["status_label"] = STATUS_LABELS.get(
            str(open_request.get("status") or ""),
            str(open_request.get("status") or ""),
        )
    return _json({"open_request": open_request})


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
        item["timeline"] = get_request_timeline(item["id"])
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
            expected_delivery_days=body.get("expected_delivery_days"),
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
        if item["status"] == "accepted":
            action_label = "Принял заявку"
        elif item["status"] == "in_transit":
            action_label = "Заказал у поставщика"
        else:
            action_label = "Изменил статус"
        lines = [
            "🔵 Склад Мастер · статус заявки",
            f"Заявка #{item['id']}: {status_label}",
            f"Площадка: {item['site_name']}",
            "Позиции заявки:",
            *_request_item_lines(item),
            *_eta_lines(item),
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
            expected_delivery_days=body.get("expected_delivery_days"),
            idempotency_key=_idempotency(request, body),
        )
    except (KeyError, TypeError, ValueError) as exc:
        return _json({"error": str(exc)}, 400)
    replay = item.pop("_idempotent_replay", False)
    if not replay:
        refreshed = get_request(request_id)
        lines = [
            "🚚 Склад Мастер · заказ у поставщика",
            f"Заявка #{request_id}",
            f"Площадка: {current['site_name']}",
            f"Поставщик: {item.get('supplier_name') or 'не указан'}",
            "Отправлено:",
            *_delivery_item_lines(item),
            *_eta_lines(refreshed),
            f"Оформил: {name} (id {uid})",
            f"Комментарий: {item['note']}" if item.get("note") else "",
        ]
        await _notify(
            lines,
            role="master",
            exclude_user_id=uid,
        )
    return _json({"delivery": item}, 201)


async def handle_request_eta(request: web.Request) -> web.Response:
    auth = _auth(request, "request_manage")
    if isinstance(auth, web.Response):
        return auth
    _user, uid, name, access = auth
    try:
        body = await _body(request)
        request_id = int(request.match_info["request_id"])
        current = get_request(request_id)
        if not _site_allowed(access, current["site_id"]):
            return _json({"error": "Нет доступа к площадке"}, 403)
        item = update_request_eta(
            request_id=request_id,
            expected_delivery_days=int(body["expected_delivery_days"]),
            actor_role=_role_for_transition(access, body.get("role")),
            actor_max_id=uid,
            actor_name=name,
            idempotency_key=_idempotency(request, body),
        )
    except (KeyError, TypeError, ValueError) as exc:
        return _json({"error": str(exc)}, 400)
    replay = item.pop("_idempotent_replay", False)
    if not replay:
        lines = [
            "📅 Склад Мастер · срок поставки изменён",
            f"Заявка #{item['id']} · {item.get('site_name') or ''}",
            "Позиции заявки:",
            *_request_item_lines(item),
            *_eta_lines(item),
            f"Изменил: {name} (id {uid})",
        ]
        await _notify(lines, role="master", exclude_user_id=uid)
    return _json({"request": _decorate_request(item, access)})


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
            confirm_early=bool(body.get("confirm_early")),
            idempotency_key=_idempotency(request, body),
        )
    except (KeyError, TypeError, ValueError) as exc:
        return _json({"error": str(exc)}, 400)
    replay = item.pop("_idempotent_replay", False)
    if not replay:
        request_doc = item["request"]
        complete = request_doc["status"] in {"received", "closed"}
        lines = [
            "✅ Склад Мастер · поставка на базу",
            f"Заявка #{request_doc['id']} · заказ #{item['id']}",
            f"Площадка: {request_doc['site_name']}",
            f"Поставщик: {item.get('supplier_name') or 'не указан'}",
            "Принято:",
            *_delivery_item_lines({"items": item.get("received_now") or []}),
            f"Исполнение заявки: {'полностью' if complete else 'частично'}",
        ]
        if request_doc.get("eta_early_days"):
            lines.append(f"Досрочно на {request_doc['eta_early_days']} дн.")
        elif request_doc.get("eta_display_line") and complete:
            lines.append(request_doc["eta_display_line"])
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
            f"Оформил: {name} (id {uid})",
            f"Комментарий: {body.get('note')}" if body.get("note") else "",
        ])
        notify_role = "supply" if "master" in access.get("roles", []) else "master"
        await _notify(
            lines,
            role=notify_role,
            exclude_user_id=uid,
        )
    return _json({"delivery": item})


async def handle_movement_detail(request: web.Request) -> web.Response:
    auth = _auth(request, "view")
    if isinstance(auth, web.Response):
        return auth
    _user, _uid, _name, access = auth
    try:
        item = get_movement(int(request.match_info["movement_id"]), detailed=True)
    except (TypeError, ValueError) as exc:
        return _json({"error": str(exc)}, 404 if "не найдено" in str(exc) else 400)
    if not _site_allowed(access, item["site_id"]):
        return _json({"error": "Нет доступа к площадке"}, 403)
    return _json({"movement": item})


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
                                offset=int(request.query.get("offset", 0)),
                                detailed=request.query.get("detailed") in {"1", "true", "yes"})
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


async def handle_admin_roles_list(request: web.Request) -> web.Response:
    auth = _auth(request, "roles_manage")
    if isinstance(auth, web.Response):
        return auth
    return _json({"roles": list_roles(include_inactive=True)})


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
    app.router.add_post(f"{prefix}/access-request", handle_access_request)
    app.router.add_post(f"{prefix}/materials", handle_materials)
    app.router.add_get(
        f"{prefix}/materials/{{material_id}}/open-request",
        handle_material_open_request,
    )
    app.router.add_post(f"{prefix}/suppliers", handle_suppliers)
    app.router.add_post(f"{prefix}/receipts", handle_receipt)
    app.router.add_get(f"{prefix}/receipts/{{receipt_id}}", handle_receipt_detail)
    app.router.add_patch(f"{prefix}/receipts/{{receipt_id}}", handle_receipt_edit)
    app.router.add_post(f"{prefix}/receipts/{{receipt_id}}/cancel", handle_receipt_cancel)
    app.router.add_patch(f"{prefix}/receipts/{{receipt_id}}/pricing", handle_receipt_price)
    app.router.add_post(f"{prefix}/receipts/{{receipt_id}}/send-manager", handle_receipt_send_manager)
    app.router.add_get(f"{prefix}/receipts/similar", handle_receipt_similar)
    app.router.add_get(f"{prefix}/admin/audit", handle_audit_log)
    app.router.add_get(f"{prefix}/admin/audit/{{audit_id}}", handle_audit_detail)
    app.router.add_post(f"{prefix}/issues", handle_issue)
    app.router.add_post(f"{prefix}/issue", handle_issue)
    app.router.add_post(f"{prefix}/transfers", handle_transfer)
    app.router.add_post(f"{prefix}/transfer", handle_transfer)
    app.router.add_post(f"{prefix}/inventory-adjustments", handle_adjustment)
    app.router.add_post(f"{prefix}/inventory-adjustment", handle_adjustment)
    app.router.add_get(f"{prefix}/movements/{{movement_id}}", handle_movement_detail)
    app.router.add_get(f"{prefix}/movements", handle_movements)
    app.router.add_get(f"{prefix}/requests", handle_requests)
    app.router.add_post(f"{prefix}/requests", handle_request_create)
    app.router.add_get(f"{prefix}/requests/{{request_id}}", handle_request_detail)
    app.router.add_patch(f"{prefix}/requests/{{request_id}}", handle_transition)
    app.router.add_patch(f"{prefix}/requests/{{request_id}}/eta", handle_request_eta)
    app.router.add_post(f"{prefix}/requests/{{request_id}}/transition", handle_transition)
    app.router.add_post(f"{prefix}/requests/{{request_id}}/deliveries", handle_delivery_create)
    app.router.add_post(f"{prefix}/deliveries/{{delivery_id}}/receive", handle_delivery_receive)
    app.router.add_patch(f"{prefix}/admin/materials/{{material_id}}", handle_admin_material)
    app.router.add_patch(f"{prefix}/admin/sites/{{site_id}}", handle_admin_site)
    app.router.add_put(
        f"{prefix}/admin/sites/{{site_id}}/materials/{{material_id}}",
        handle_admin_site_material,
    )
    app.router.add_get(f"{prefix}/admin/roles", handle_admin_roles_list)
    app.router.add_post(f"{prefix}/admin/roles", handle_admin_role)


# Compatibility aliases used by older imports/tests.
handle_sklad_master_bootstrap = handle_bootstrap
handle_sklad_master_materials = handle_materials
handle_sklad_master_suppliers = handle_suppliers
handle_sklad_master_receipt = handle_receipt
handle_sklad_master_request = handle_request_create
