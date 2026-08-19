"""HTTP API площадки Таксимо."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date

from aiohttp import web

from docs_upload import list_documents, save_upload
from materials_chat import notify_event as notify_materials_chat_event
from taksimo_export import build_registry_workbook, build_yard_workbook, save_registry_copy
from taksimo_auth import can_delete, user_from_request
from taksimo_notify import (
    handle_session_notifications,
    notify_fleet_extras,
    notify_kodar_blocked,
    notify_kodar_dispatch,
    notify_kodar_received,
    schedule_wagon_full_checks,
)
from taksimo_wagon_logic import analyze_fleet_extras
from taksimo_time import complete_datetime_label
from taksimo_backup import list_backups
from taksimo_store import (
    DEFAULT_CUSTOMER,
    DEFAULT_GRID_X,
    DEFAULT_GRID_Y,
    MATERIAL_UNITS,
    MAX_SLABS_PER_CELL,
    MAX_WAGON_SLABS,
    MAX_WAGONS_PER_DEAD_END,
    PLATFORM_ZONES,
    PLATFORM_ZONES_OPERATOR,
    SESSION_COMPLETED,
    SESSION_DRAFT,
    SLAB_LETTERS,
    SUFFIXES,
    TaksimoConflictError,
    KodarBlockedError,
    add_material_receipt,
    add_template_item,
    adjust_material_stock,
    add_wagon_numbers,
    confirm_kodar_received,
    count_sessions,
    create_session,
    create_material_item,
    create_material_template,
    db_status,
    delete_template_item,
    delete_session,
    delete_slab,
    dispatch_wagon_to_kodar,
    assign_template_to_wagon,
    finalize_wagon_materials,
    get_material_item,
    get_wagon_card,
    get_wagon_materials,
    get_session,
    get_slab,
    init_taksimo_db,
    list_material_items,
    list_material_templates,
    materials_dashboard,
    list_sessions,
    list_wagon_cards,
    list_vehicles,
    list_wagon_dispatch_history,
    list_wagon_pool,
    reserve_material_for_wagon,
    reserve_scheme1_kit_for_wagon,
    format_kit_reserve_report,
    unified_search,
    update_material_item,
    update_material_template,
    update_session,
    update_slab,
    update_template_item,
    update_wagon_planned_zone,
    update_wagon_slot,
    validate_session_complete,
    wagon_plan,
    wagon_zone_counts,
    yard_map,
    yard_stats,
)


def _require_materials_admin(request: web.Request) -> str | web.Response:
    user = user_from_request(request)
    if not can_delete(user):
        return _json({"error": "Только оператор1 может редактировать расходники"}, 403)
    return user or ""


def _material_chat_location(wagon_payload: dict) -> str:
    wagon_card = (wagon_payload or {}).get("wagon") or {}
    parts = []
    location = (wagon_card.get("location_label") or "").strip()
    if location:
        parts.append(location)
    slot_label = (wagon_card.get("slot_label") or "").strip()
    if slot_label and slot_label not in parts:
        parts.append(slot_label)
    return " · ".join(parts)


def _scheme_chat_label(raw: str) -> str:
    name = (raw or "").strip()
    if not name:
        return "—"
    match = re.search(r"(\d+)", name)
    if match:
        return f"схема №{match.group(1)}"
    return name


async def _safe_notify_materials_chat(text: str) -> None:
    try:
        await notify_materials_chat_event(text)
    except Exception:
        logger.exception("Материалы чат: не удалось отправить служебное сообщение")

logger = logging.getLogger(__name__)


def _json(data: dict, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _session_action(body: dict) -> str:
    action = (body.get("action") or "complete").strip().lower()
    if action in ("draft", "complete"):
        return action
    return "complete"


def _save_session_from_body(
    body: dict,
    *,
    operator: str,
    session_id: int | None = None,
    expected_revision: int | None = None,
) -> dict:
    unload_date = (body.get("unload_date") or "").strip() or date.today().isoformat()
    vehicle_id = body.get("vehicle_id")
    if vehicle_id is not None:
        try:
            vehicle_id = int(vehicle_id)
        except (TypeError, ValueError):
            vehicle_id = None
    revision = body.get("revision")
    if revision is not None:
        try:
            revision = int(revision)
        except (TypeError, ValueError):
            revision = None
    if expected_revision is None and revision is not None:
        expected_revision = revision

    if vehicle_id is None:
        raise ValueError("Выберите машину — нажмите карточку с номером на форме")
    action = _session_action(body)
    crane_start = (body.get("crane_start") or "").strip()
    crane_end = (body.get("crane_end") or "").strip()
    fields = {
        "unload_date": unload_date,
        "trn": (body.get("trn") or "").strip(),
        "vehicle_id": vehicle_id,
        "driver": (body.get("driver") or "").strip(),
        "crane_start": crane_start,
        "crane_end": crane_end,
        "crane_minutes": body.get("crane_minutes"),
        "riggers_count": 0,
        "riggers_pay": 0,
        "taxi_pay": 0,
        "notes": (body.get("notes") or "").strip(),
        "slabs": body.get("slabs") or [],
        "operator": operator,
    }
    if action == "complete":
        validate_session_complete(crane_start, crane_end)
        fields["status"] = SESSION_COMPLETED
        fields["unload_datetime"] = complete_datetime_label()
    else:
        fields["status"] = SESSION_DRAFT

    if session_id is None:
        before = wagon_zone_counts()
        session = create_session(**fields)
        schedule_wagon_full_checks(before, wagon_zone_counts())
        return session
    existing = get_session(session_id)
    if action == "draft" and existing and existing.get("status") == SESSION_COMPLETED:
        fields.pop("status", None)
        fields.pop("unload_datetime", None)
    before = wagon_zone_counts()
    session = update_session(
        session_id,
        **fields,
        expected_revision=expected_revision,
    )
    schedule_wagon_full_checks(before, wagon_zone_counts())
    return session


def _db_status_payload() -> dict:
    st = db_status()
    backups = list_backups()
    last_backup = backups[0] if backups else None
    return {
        **st,
        "last_backup_ts": last_backup["modified"] if last_backup else None,
        "last_backup_name": last_backup["name"] if last_backup else None,
    }


async def handle_taksimo_meta(_request: web.Request) -> web.Response:
    yard = yard_map()
    return _json(
        {
            "letters": list(SLAB_LETTERS),
            "suffixes": list(SUFFIXES),
            "default_grid_x": DEFAULT_GRID_X,
            "default_grid_y": DEFAULT_GRID_Y,
            "max_slabs_per_cell": MAX_SLABS_PER_CELL,
            "grid_x": yard["grid_x"],
            "grid_y": yard["grid_y"],
            "vehicles": list_vehicles(),
            "platform_zones": list(PLATFORM_ZONES_OPERATOR),
            "customer_default": DEFAULT_CUSTOMER,
            "max_wagon_slabs": MAX_WAGON_SLABS,
            "max_wagons_per_dead_end": MAX_WAGONS_PER_DEAD_END,
            "wagon_pool": list_wagon_pool(),
            "material_units": list(MATERIAL_UNITS),
            "stats": yard_stats(),
            "db": _db_status_payload(),
        }
    )


async def handle_taksimo_stats(_request: web.Request) -> web.Response:
    return _json({**yard_stats(), "db": _db_status_payload()})


async def handle_taksimo_vehicles(_request: web.Request) -> web.Response:
    return _json({"vehicles": list_vehicles()})


async def handle_taksimo_yard(_request: web.Request) -> web.Response:
    return _json(yard_map())


async def handle_taksimo_sessions(request: web.Request) -> web.Response:
    if request.method == "GET":
        try:
            limit = int(request.rel_url.query.get("limit", "50"))
        except ValueError:
            limit = 50
        try:
            offset = int(request.rel_url.query.get("offset", "0"))
        except ValueError:
            offset = 0
        limit = max(1, min(limit, 100))
        offset = max(0, offset)
        sessions = list_sessions(limit=limit, offset=offset)
        total = count_sessions()
        return _json({
            "sessions": sessions,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(sessions) < total,
        })

    try:
        body = await request.json()
    except json.JSONDecodeError:
        return _json({"error": "invalid json"}, 400)

    operator = user_from_request(request) or ""
    action = _session_action(body)
    try:
        session = _save_session_from_body(body, operator=operator)
    except ValueError as e:
        logger.warning("Таксимо: отклонено создание сессии: %s", e)
        return _json({"error": str(e)}, 400)

    logger.info(
        "Таксимо: сессия %s (%s), плит %s, оператор=%s",
        session["id"],
        action,
        len(session["slabs"]),
        operator,
    )
    notify_action = action
    if session.get("status") == SESSION_COMPLETED and action == "draft":
        notify_action = "none"
    handle_session_notifications(session, action=notify_action, is_new=True)
    return _json({"session": session}, 201)


async def _parse_session_body(body: dict) -> dict:
    vehicle_id = body.get("vehicle_id")
    if vehicle_id is not None:
        try:
            vehicle_id = int(vehicle_id)
        except (TypeError, ValueError):
            vehicle_id = None
    return {
        "unload_date": (body.get("unload_date") or "").strip() or date.today().isoformat(),
        "trn": (body.get("trn") or "").strip(),
        "vehicle_id": vehicle_id,
        "driver": (body.get("driver") or "").strip(),
        "crane_start": (body.get("crane_start") or "").strip(),
        "crane_end": (body.get("crane_end") or "").strip(),
        "crane_minutes": body.get("crane_minutes"),
        "riggers_count": 0,
        "riggers_pay": 0,
        "taxi_pay": 0,
        "notes": (body.get("notes") or "").strip(),
        "slabs": body.get("slabs") or [],
        "revision": body.get("revision"),
    }


async def handle_taksimo_session_detail(request: web.Request) -> web.Response:
    try:
        session_id = int(request.match_info["session_id"])
    except (KeyError, ValueError):
        return _json({"error": "invalid session_id"}, 400)

    if request.method == "GET":
        session = get_session(session_id)
        if session is None:
            return _json({"error": "not found"}, 404)
        return _json({"session": session})

    if request.method == "DELETE":
        operator = user_from_request(request) or ""
        if not can_delete(operator):
            return _json({"error": "Удаление — только у оператора 1 (админ)"}, 403)
        if not delete_session(session_id):
            return _json({"error": "not found"}, 404)
        logger.info("Таксимо: удалена сессия %s, оператор=%s", session_id, operator)
        return _json({"ok": True})

    if request.method == "PUT":
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return _json({"error": "invalid json"}, 400)
        revision = body.get("revision")
        if revision is not None:
            try:
                revision = int(revision)
            except (TypeError, ValueError):
                revision = None
        operator = user_from_request(request) or ""
        action = _session_action(body)
        try:
            session = _save_session_from_body(
                body,
                operator=operator,
                session_id=session_id,
                expected_revision=revision,
            )
        except KeyError:
            return _json({"error": "not found"}, 404)
        except TaksimoConflictError as e:
            current = get_session(session_id)
            return _json(
                {"error": str(e), "revision": current["revision"] if current else None},
                409,
            )
        except ValueError as e:
            logger.warning("Таксимо: отклонено обновление сессии %s: %s", session_id, e)
            return _json({"error": str(e)}, 400)
        logger.info("Таксимо: обновлена сессия %s (%s), оператор=%s", session_id, action, operator)
        notify_action = action
        if session.get("status") == SESSION_COMPLETED and action == "draft":
            notify_action = "none"
        handle_session_notifications(session, action=notify_action, is_new=False)
        return _json({"session": session})

    return _json({"error": "method not allowed"}, 405)


async def handle_taksimo_slab(request: web.Request) -> web.Response:
    try:
        slab_id = int(request.match_info["slab_id"])
    except (KeyError, ValueError):
        return _json({"error": "invalid slab_id"}, 400)

    if request.method == "GET":
        slab = get_slab(slab_id)
        if slab is None:
            return _json({"error": "not found"}, 404)
        return _json({"slab": slab})

    if request.method == "DELETE":
        operator = user_from_request(request) or ""
        if not can_delete(operator):
            return _json({"error": "Удаление — только у оператора 1 (админ)"}, 403)
        if not delete_slab(slab_id):
            return _json({"error": "not found"}, 404)
        logger.info("Таксимо: удалена плита %s, оператор=%s", slab_id, operator)
        return _json({"ok": True})

    if request.method == "PUT":
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return _json({"error": "invalid json"}, 400)
        old_zone = None
        old_row = get_slab(slab_id)
        if old_row:
            old_zone = old_row.get("platform_zone")
        try:
            before = wagon_zone_counts()
            slab = update_slab(slab_id, data=body)
            schedule_wagon_full_checks(before, wagon_zone_counts())
        except KeyError:
            return _json({"error": "not found"}, 404)
        except ValueError as e:
            return _json({"error": str(e)}, 400)
        old_zone = slab.pop("_old_platform_zone", old_zone)
        return _json({"slab": slab})

    return _json({"error": "method not allowed"}, 405)


async def handle_taksimo_search(request: web.Request) -> web.Response:
    q = request.rel_url.query.get("q", "").strip()
    if not q:
        return _json({"type": "slab", "results": []})
    return _json(unified_search(q))


async def handle_taksimo_export_registry(request: web.Request) -> web.Response:
    date_from = request.rel_url.query.get("from", "").strip() or None
    date_to = request.rel_url.query.get("to", "").strip() or None
    single = request.rel_url.query.get("date", "").strip()
    if single:
        date_from = date_to = single
    save = request.rel_url.query.get("save", "1") != "0"
    data = build_registry_workbook(date_from=date_from, date_to=date_to)
    saved_path = None
    if save:
        saved_path = save_registry_copy(data, date_from=date_from, date_to=date_to)
        logger.info("Excel реестр: %s", saved_path)
    fname = "taksimo-reestr.xlsx"
    if date_from and date_from == date_to:
        fname = f"taksimo-reestr-{date_from}.xlsx"
    headers = {
        "Content-Disposition": f'attachment; filename="{fname}"',
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    if saved_path:
        headers["X-Export-Path"] = str(saved_path)
    return web.Response(body=data, headers=headers)


async def handle_taksimo_docs_list(_request: web.Request) -> web.Response:
    return _json({"files": list_documents()})


async def handle_taksimo_docs_upload(request: web.Request) -> web.Response:
    try:
        reader = await request.multipart()
    except Exception:
        return _json({"error": "multipart required"}, 400)
    folder = "incoming"
    file_data: bytes | None = None
    filename = "upload.bin"
    async for part in reader:
        if part.name == "folder":
            folder = (await part.text()) or "incoming"
        elif part.name == "file":
            filename = part.filename or filename
            file_data = await part.read(decode=False)
    if not file_data:
        return _json({"error": "file required"}, 400)
    try:
        saved = save_upload(folder.strip(), filename, file_data)
    except ValueError as e:
        return _json({"error": str(e)}, 400)
    logger.info("Документ загружен: %s/%s", saved["folder"], saved["name"])
    return _json({"ok": True, "file": saved}, 201)


async def handle_taksimo_wagon_plan(_request: web.Request) -> web.Response:
    return _json(wagon_plan())


async def handle_taksimo_fleet_extras(request: web.Request) -> web.Response:
    zone = (request.rel_url.query.get("zone") or "ТУРАН").strip()
    plan = wagon_plan()
    slots: list[dict] = []
    for zone_slots in (plan.get("dead_ends") or {}).values():
        slots.extend(zone_slots)
    dispatches = list_wagon_dispatch_history(limit=200)
    if zone.upper() in ("ALL", "ВСЕ", "*"):
        zones_out = {
            zone_name: analyze_fleet_extras(
                slots,
                max_slabs=MAX_WAGON_SLABS,
                zone=zone_name,
                dispatches=dispatches,
            )
            for zone_name in ("ТУРАН", "ГРУЗОВОЙ")
        }
        return _json({"zone": "ALL", "zones": zones_out})
    extras = analyze_fleet_extras(
        slots,
        max_slabs=MAX_WAGON_SLABS,
        zone=zone or None,
        dispatches=dispatches,
    )
    notify = (request.rel_url.query.get("notify") or "").strip() in ("1", "true", "yes")
    if notify:
        asyncio.create_task(notify_fleet_extras(extras, zone=zone or "ТУРАН"))
    return _json({"zone": zone, "extras": extras})


async def handle_taksimo_wagon_slot(request: web.Request) -> web.Response:
    try:
        slot_id = int(request.match_info["slot_id"])
    except (KeyError, ValueError):
        return _json({"error": "invalid slot_id"}, 400)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return _json({"error": "invalid json"}, 400)
    try:
        slot = update_wagon_slot(
            slot_id,
            wagon_number=body.get("wagon_number"),
            expected_blocks=body.get("expected_blocks"),
            scheme_code=body.get("scheme_code"),
        )
    except KeyError:
        return _json({"error": "not found"}, 404)
    except ValueError as e:
        return _json({"error": str(e)}, 400)
    return _json({"slot": slot})


async def handle_taksimo_wagon_pool(request: web.Request) -> web.Response:
    if request.method == "GET":
        return _json({"wagons": list_wagon_pool()})
    return _json(
        {"error": "Добавление вагонов — только через админ-панель"},
        403,
    )


def _require_kodar_operator(request: web.Request) -> str | None:
    user = user_from_request(request) or ""
    if not can_delete(user):
        return None
    return user


async def handle_dispatch_to_kodar(request: web.Request) -> web.Response:
    operator = _require_kodar_operator(request)
    if not operator:
        return _json({"error": "Только оператор 1"}, 403)
    try:
        slot_id = int(request.match_info["slot_id"])
    except (KeyError, ValueError):
        return _json({"error": "invalid slot_id"}, 400)
    try:
        dispatch = dispatch_wagon_to_kodar(slot_id, operator=operator)
    except KeyError:
        return _json({"error": "not found"}, 404)
    except ValueError as e:
        return _json({"error": str(e)}, 400)
    asyncio.create_task(notify_kodar_dispatch(dispatch))
    return _json({"ok": True, "dispatch": dispatch})


async def handle_kodar_received(request: web.Request) -> web.Response:
    operator = _require_kodar_operator(request)
    if not operator:
        return _json({"error": "Только оператор 1"}, 403)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        body = {}
    try:
        dispatch = confirm_kodar_received(
            str(body.get("wagon_number") or ""),
            operator=operator,
            dispatch_id=body.get("dispatch_id"),
        )
    except ValueError as e:
        return _json({"error": str(e)}, 400)
    asyncio.create_task(notify_kodar_received(dispatch))
    return _json({"ok": True, "dispatch": dispatch})


async def handle_wagon_planned_zone(request: web.Request) -> web.Response:
    wagon_number = (request.match_info.get("wagon_number") or "").strip()
    if not wagon_number:
        return _json({"error": "wagon_number required"}, 400)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return _json({"error": "invalid json"}, 400)
    try:
        wagon = update_wagon_planned_zone(
            wagon_number,
            str(body.get("planned_zone") or ""),
        )
    except ValueError as e:
        return _json({"error": str(e)}, 400)
    wagon_materials = get_wagon_materials(wagon_number) or {}
    prep = (wagon_materials.get("prep") or {})
    if prep.get("returns_materials") and wagon.get("stage") == "returning":
        await _safe_notify_materials_chat(
            "\n".join(
                [
                    "🟡 Возвратный вагон направлен в тупик",
                    "",
                    f"Вагон: {wagon_number}",
                    f"Схема: {_scheme_chat_label(prep.get('template_name') or '')}",
                    f"Куда вернуть: {wagon.get('planned_zone') or body.get('planned_zone') or '—'}",
                ]
            )
        )
    return _json({"wagon": wagon})


async def handle_taksimo_wagon_history(request: web.Request) -> web.Response:
    try:
        limit = int(request.rel_url.query.get("limit", "50"))
    except ValueError:
        limit = 50
    return _json({"dispatches": list_wagon_dispatch_history(limit=limit)})


async def handle_taksimo_wagon_catalog(request: web.Request) -> web.Response:
    try:
        limit = int(request.rel_url.query.get("limit", "80"))
    except ValueError:
        limit = 80
    query = (request.rel_url.query.get("q") or "").strip()
    return _json({"wagons": list_wagon_cards(query=query, limit=limit)})


async def handle_taksimo_wagon_card(request: web.Request) -> web.Response:
    wagon_number = (request.match_info.get("wagon_number") or "").strip()
    if not wagon_number:
        return _json({"error": "wagon_number required"}, 400)
    card = get_wagon_card(wagon_number)
    if not card:
        return _json({"error": "not found"}, 404)
    return _json({"wagon": card})


async def handle_taksimo_materials_overview(_request: web.Request) -> web.Response:
    return _json(materials_dashboard())


async def handle_taksimo_material_item(request: web.Request) -> web.Response:
    if request.method == "POST":
        operator = _require_materials_admin(request)
        if isinstance(operator, web.Response):
            return operator
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return _json({"error": "invalid json"}, 400)
        try:
            item = create_material_item(
                name=str(body.get("name") or ""),
                unit=str(body.get("unit") or "шт"),
                min_level=body.get("min_level", 0),
                norm_per_wagon=body.get("norm_per_wagon", 0),
            )
        except ValueError as e:
            return _json({"error": str(e)}, 400)
        await _safe_notify_materials_chat(
            "\n".join(
                [
                    "🟢 Новый материал",
                    "",
                    f"Материал: {item.get('name')}",
                    f"Ед. изм.: {item.get('unit')}",
                    f"Минимум: {item.get('min_level')}",
                    f"Норма на вагон: {item.get('norm_per_wagon')}",
                ]
            )
        )
        return _json({"item": item}, 201)

    try:
        material_id = int(request.match_info["material_id"])
    except (KeyError, ValueError):
        return _json({"error": "invalid material_id"}, 400)
    operator = _require_materials_admin(request)
    if isinstance(operator, web.Response):
        return operator
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return _json({"error": "invalid json"}, 400)
    try:
        item = update_material_item(material_id, body)
    except ValueError as e:
        return _json({"error": str(e)}, 400)
    await _safe_notify_materials_chat(
        "\n".join(
            [
                "🟡 Материал отредактирован",
                "",
                f"Материал: {item.get('name')}",
                f"Ед. изм.: {item.get('unit')}",
                f"Минимум: {item.get('min_level')}",
                f"Норма на вагон: {item.get('norm_per_wagon')}",
            ]
        )
    )
    return _json({"item": item})


async def handle_taksimo_material_templates(request: web.Request) -> web.Response:
    if request.method == "GET":
        return _json({"templates": list_material_templates()})
    operator = _require_materials_admin(request)
    if isinstance(operator, web.Response):
        return operator
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return _json({"error": "invalid json"}, 400)
    try:
        template = create_material_template(
            name=str(body.get("name") or ""),
            description=str(body.get("description") or ""),
            scheme_code=str(body.get("scheme_code") or ""),
            has_box=body.get("has_box"),
            returns_materials=body.get("returns_materials"),
            extra_ring_mode=str(body.get("extra_ring_mode") or ""),
            extra_units=body.get("extra_units", 0),
            k_goal=body.get("k_goal", 0),
            box_capacity_wagons=body.get("box_capacity_wagons", 0),
        )
    except ValueError as e:
        return _json({"error": str(e)}, 400)
    return _json({"template": template}, 201)


async def handle_taksimo_material_template_detail(request: web.Request) -> web.Response:
    operator = _require_materials_admin(request)
    if isinstance(operator, web.Response):
        return operator
    try:
        template_id = int(request.match_info["template_id"])
    except (KeyError, ValueError):
        return _json({"error": "invalid template_id"}, 400)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return _json({"error": "invalid json"}, 400)
    try:
        template = update_material_template(template_id, body)
    except ValueError as e:
        return _json({"error": str(e)}, 400)
    return _json({"template": template})


async def handle_taksimo_material_template_item(request: web.Request) -> web.Response:
    operator = _require_materials_admin(request)
    if isinstance(operator, web.Response):
        return operator
    try:
        template_id = int(request.match_info["template_id"])
    except (KeyError, ValueError):
        return _json({"error": "invalid template_id"}, 400)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return _json({"error": "invalid json"}, 400)
    try:
        item = add_template_item(
            template_id,
            material_id=int(body.get("material_id")),
            qty_norm=body.get("qty_norm", 0),
            work_type=str(body.get("work_type") or ""),
            feature_text=str(body.get("feature_text") or ""),
            tool_text=str(body.get("tool_text") or ""),
            norm_minutes=body.get("norm_minutes", 0),
            line_no=body.get("line_no"),
        )
    except (TypeError, ValueError) as e:
        return _json({"error": str(e)}, 400)
    return _json({"item": item}, 201)


async def handle_taksimo_material_template_item_detail(
    request: web.Request,
) -> web.Response:
    operator = _require_materials_admin(request)
    if isinstance(operator, web.Response):
        return operator
    try:
        template_item_id = int(request.match_info["template_item_id"])
    except (KeyError, ValueError):
        return _json({"error": "invalid template_item_id"}, 400)
    if request.method == "DELETE":
        if not delete_template_item(template_item_id):
            return _json({"error": "not found"}, 404)
        return _json({"ok": True})
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return _json({"error": "invalid json"}, 400)
    try:
        item = update_template_item(template_item_id, body)
    except ValueError as e:
        return _json({"error": str(e)}, 400)
    return _json({"item": item})


async def handle_taksimo_material_assign_template(request: web.Request) -> web.Response:
    operator = _require_materials_admin(request)
    if isinstance(operator, web.Response):
        return operator
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return _json({"error": "invalid json"}, 400)
    try:
        wagon = assign_template_to_wagon(
            str(body.get("wagon_number") or ""),
            template_id=int(body.get("template_id")),
            operator=operator,
            note=str(body.get("note") or ""),
        )
    except (TypeError, ValueError) as e:
        return _json({"error": str(e)}, 400)
    prep = (wagon or {}).get("prep") or {}
    location = _material_chat_location(wagon or {})
    lines = [
        "🟡 Назначена схема вагону",
        "",
        f"Вагон: {wagon.get('wagon_number')}",
        f"Схема: {_scheme_chat_label(prep.get('template_name') or '')}",
    ]
    if prep.get("k_goal"):
        lines.append(f"K по схеме: {prep.get('k_goal')}")
    if prep.get("extra_units"):
        lines.append(f"Допы A–F: {prep.get('extra_units')}")
    if prep.get("returns_materials"):
        lines.append(
            "Возврат: "
            + (
                prep.get("return_target_zone")
                or prep.get("origin_zone")
                or "ожидает тупик"
            )
        )
    if location:
        lines.append(f"Где стоит: {location}")
    if wagon.get("shortage_count"):
        lines.append(f"Дефицит позиций: {wagon.get('shortage_count')}")
    await _safe_notify_materials_chat("\n".join(lines))
    return _json({"wagon": wagon})


async def handle_taksimo_material_receipt(request: web.Request) -> web.Response:
    operator = _require_materials_admin(request)
    if isinstance(operator, web.Response):
        return operator
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return _json({"error": "invalid json"}, 400)
    try:
        material_id = int(body.get("material_id"))
        item = add_material_receipt(
            material_id,
            quantity=body.get("quantity"),
            operator=operator,
            note=str(body.get("note") or ""),
        )
    except (TypeError, ValueError) as e:
        return _json({"error": str(e)}, 400)
    await _safe_notify_materials_chat(
        "\n".join(
            [
                "🟢 Приход материала",
                "",
                f"Материал: {item.get('name')}",
                f"Приход: {body.get('quantity')} {item.get('unit')}",
                f"Факт: {item.get('on_hand')} {item.get('unit')}",
                f"Свободно: {item.get('available')} {item.get('unit')}",
            ]
        )
    )
    return _json({"item": item})


async def handle_taksimo_material_adjust(request: web.Request) -> web.Response:
    operator = _require_materials_admin(request)
    if isinstance(operator, web.Response):
        return operator
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return _json({"error": "invalid json"}, 400)
    try:
        material_id = int(body.get("material_id"))
        item = adjust_material_stock(
            material_id,
            quantity=body.get("quantity"),
            operator=operator,
            note=str(body.get("note") or ""),
        )
    except (TypeError, ValueError) as e:
        return _json({"error": str(e)}, 400)
    await _safe_notify_materials_chat(
        "\n".join(
            [
                "🟡 Корректировка материала",
                "",
                f"Материал: {item.get('name')}",
                f"Изменение: {body.get('quantity')} {item.get('unit')}",
                f"Факт: {item.get('on_hand')} {item.get('unit')}",
                f"Свободно: {item.get('available')} {item.get('unit')}",
            ]
        )
    )
    return _json({"item": item})


async def handle_taksimo_material_reserve(request: web.Request) -> web.Response:
    operator = _require_materials_admin(request)
    if isinstance(operator, web.Response):
        return operator
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return _json({"error": "invalid json"}, 400)
    try:
        material_id = int(body.get("material_id"))
        wagon = reserve_material_for_wagon(
            material_id,
            wagon_number=str(body.get("wagon_number") or ""),
            quantity=body.get("quantity"),
            operator=operator,
            note=str(body.get("note") or ""),
        )
    except (TypeError, ValueError) as e:
        return _json({"error": str(e)}, 400)
    material = get_material_item(material_id)
    location = _material_chat_location(wagon or {})
    reserved_item = next(
        (
            item
            for item in ((wagon or {}).get("items") or [])
            if int(item.get("id") or 0) == material_id
        ),
        None,
    )
    lines = [
        "🟡 Резерв под вагон",
        "",
        f"Вагон: {wagon.get('wagon_number')}",
        f"Материал: {(material or {}).get('name') or material_id}",
        f"Резерв: {body.get('quantity')} {(material or {}).get('unit') or ''}".rstrip(),
    ]
    if location:
        lines.append(f"Где стоит: {location}")
    if material:
        lines.append(f"Свободно после резерва: {material.get('available')} {material.get('unit')}")
    if reserved_item and float(reserved_item.get("shortage_qty") or 0) > 0:
        lines.append(
            f"Остался дефицит: {reserved_item.get('shortage_qty')} {reserved_item.get('unit')}"
        )
    await _safe_notify_materials_chat("\n".join(lines))
    return _json({"wagon": wagon})


async def handle_taksimo_material_reserve_kit(request: web.Request) -> web.Response:
    operator = _require_materials_admin(request)
    if isinstance(operator, web.Response):
        return operator
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return _json({"error": "invalid json"}, 400)
    wagon_number = str(body.get("wagon_number") or "").strip()
    if not wagon_number:
        return _json({"error": "wagon_number required"}, 400)
    try:
        wagon = reserve_scheme1_kit_for_wagon(
            wagon_number,
            operator=operator,
            note=str(body.get("note") or ""),
        )
    except ValueError as e:
        return _json({"error": str(e)}, 400)
    kit_reserve = (wagon or {}).get("kit_reserve") or {}
    location = _material_chat_location(wagon or {})
    lines = [
        "🟡 Резерв комплекта схемы 1",
        "",
        f"Вагон: {wagon_number}",
        f"Дефицит позиций: {wagon.get('shortage_count', 0)}",
    ]
    if kit_reserve.get("reserved"):
        lines.append(f"Зарезервировано позиций: {len(kit_reserve.get('reserved') or [])}")
    if kit_reserve.get("skipped"):
        lines.append(f"Без остатка на складе: {len(kit_reserve.get('skipped') or [])}")
    if location:
        lines.append(f"Где стоит: {location}")
    report = format_kit_reserve_report(kit_reserve)
    if report:
        lines.extend([""] + report[:12])
    await _safe_notify_materials_chat("\n".join(lines))
    return _json({"wagon": wagon, "kit_reserve": kit_reserve})


async def handle_taksimo_material_wagon(request: web.Request) -> web.Response:
    wagon_number = (request.match_info.get("wagon_number") or "").strip()
    if not wagon_number:
        return _json({"error": "wagon_number required"}, 400)
    if request.method == "GET":
        wagon = get_wagon_materials(wagon_number)
        if not wagon:
            return _json({"error": "not found"}, 404)
        return _json({"wagon": wagon})

    try:
        body = await request.json()
    except json.JSONDecodeError:
        return _json({"error": "invalid json"}, 400)
    operator = _require_materials_admin(request)
    if isinstance(operator, web.Response):
        return operator
    try:
        wagon = finalize_wagon_materials(
            wagon_number,
            items=body.get("items") or [],
            operator=operator,
            note=str(body.get("note") or ""),
        )
    except ValueError as e:
        return _json({"error": str(e)}, 400)
    location = _material_chat_location(wagon or {})
    lines = [
        "🟢 Материалы по вагону закрыты",
        "",
        f"Вагон: {wagon.get('wagon_number')}",
    ]
    if location:
        lines.append(f"Где стоит: {location}")
    lines.append(f"Дефицит позиций: {wagon.get('shortage_count', 0)}")
    await _safe_notify_materials_chat("\n".join(lines))
    return _json({"wagon": wagon})


async def handle_taksimo_export_yard(_request: web.Request) -> web.Response:
    data = build_yard_workbook()
    headers = {
        "Content-Disposition": 'attachment; filename="taksimo-ploshadka.xlsx"',
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    return web.Response(body=data, headers=headers)


def register_taksimo_routes(app: web.Application) -> None:
    init_taksimo_db()
    app.router.add_get("/api/taksimo/meta", handle_taksimo_meta)
    app.router.add_get("/api/taksimo/vehicles", handle_taksimo_vehicles)
    app.router.add_get("/api/taksimo/yard", handle_taksimo_yard)
    app.router.add_get("/api/taksimo/sessions", handle_taksimo_sessions)
    app.router.add_post("/api/taksimo/sessions", handle_taksimo_sessions)
    app.router.add_get("/api/taksimo/sessions/{session_id}", handle_taksimo_session_detail)
    app.router.add_put("/api/taksimo/sessions/{session_id}", handle_taksimo_session_detail)
    app.router.add_delete("/api/taksimo/sessions/{session_id}", handle_taksimo_session_detail)
    app.router.add_get("/api/taksimo/slabs/{slab_id}", handle_taksimo_slab)
    app.router.add_put("/api/taksimo/slabs/{slab_id}", handle_taksimo_slab)
    app.router.add_delete("/api/taksimo/slabs/{slab_id}", handle_taksimo_slab)
    app.router.add_get("/api/taksimo/search", handle_taksimo_search)
    app.router.add_get("/api/taksimo/wagons/plan", handle_taksimo_wagon_plan)
    app.router.add_get("/api/taksimo/wagons/fleet-extras", handle_taksimo_fleet_extras)
    app.router.add_put("/api/taksimo/wagons/slots/{slot_id}", handle_taksimo_wagon_slot)
    app.router.add_post(
        "/api/taksimo/wagons/slots/{slot_id}/dispatch-kodar",
        handle_dispatch_to_kodar,
    )
    app.router.add_post("/api/taksimo/wagons/kodar-received", handle_kodar_received)
    app.router.add_get("/api/taksimo/wagons/history", handle_taksimo_wagon_history)
    app.router.add_get("/api/taksimo/wagons/catalog", handle_taksimo_wagon_catalog)
    app.router.add_get("/api/taksimo/wagons/card/{wagon_number}", handle_taksimo_wagon_card)
    app.router.add_get("/api/taksimo/materials/overview", handle_taksimo_materials_overview)
    app.router.add_post("/api/taksimo/materials/items", handle_taksimo_material_item)
    app.router.add_put("/api/taksimo/materials/items/{material_id}", handle_taksimo_material_item)
    app.router.add_get("/api/taksimo/materials/templates", handle_taksimo_material_templates)
    app.router.add_post("/api/taksimo/materials/templates", handle_taksimo_material_templates)
    app.router.add_put(
        "/api/taksimo/materials/templates/{template_id}",
        handle_taksimo_material_template_detail,
    )
    app.router.add_post("/api/taksimo/materials/templates/{template_id}/items", handle_taksimo_material_template_item)
    app.router.add_put(
        "/api/taksimo/materials/templates/items/{template_item_id}",
        handle_taksimo_material_template_item_detail,
    )
    app.router.add_delete(
        "/api/taksimo/materials/templates/items/{template_item_id}",
        handle_taksimo_material_template_item_detail,
    )
    app.router.add_post("/api/taksimo/materials/preps/assign", handle_taksimo_material_assign_template)
    app.router.add_post("/api/taksimo/materials/receipt", handle_taksimo_material_receipt)
    app.router.add_post("/api/taksimo/materials/adjust", handle_taksimo_material_adjust)
    app.router.add_post("/api/taksimo/materials/reserve", handle_taksimo_material_reserve)
    app.router.add_post("/api/taksimo/materials/reserve-kit", handle_taksimo_material_reserve_kit)
    app.router.add_get("/api/taksimo/materials/wagons/{wagon_number}", handle_taksimo_material_wagon)
    app.router.add_post("/api/taksimo/materials/wagons/{wagon_number}/finalize", handle_taksimo_material_wagon)
    app.router.add_put(
        "/api/taksimo/wagons/pool/{wagon_number}/planned-zone",
        handle_wagon_planned_zone,
    )
    app.router.add_get("/api/taksimo/wagons/pool", handle_taksimo_wagon_pool)
    app.router.add_post("/api/taksimo/wagons/pool", handle_taksimo_wagon_pool)
    app.router.add_get("/api/taksimo/stats", handle_taksimo_stats)
    app.router.add_get("/api/taksimo/export/registry.xlsx", handle_taksimo_export_registry)
    app.router.add_get("/api/taksimo/export/yard.xlsx", handle_taksimo_export_yard)
    app.router.add_get("/api/taksimo/docs", handle_taksimo_docs_list)
    app.router.add_post("/api/taksimo/docs/upload", handle_taksimo_docs_upload)
