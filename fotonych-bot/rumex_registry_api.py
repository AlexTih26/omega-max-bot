"""HTTP API отдельного бухгалтерского реестра РУМЕКС."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

from aiohttp import web

from max_webapp import display_name_from_user, init_data_from_request, user_id_from_user, validate_init_data
from rumex_registry_auth import is_registry_admin, user_from_request
from rumex_registry_documents import build_test_ttn_workbook, test_ttn_filename
from rumex_test_dispatcher_auth import user_from_request as test_dispatcher_user_from_request
from rumex_registry_store import (
    confirm_document_vehicle_binding,
    confirm_test_documents_handed_to_driver,
    create_test_shipment,
    get_document_fleet_vehicle,
    get_shipment,
    get_test_shipment,
    import_document_fleet_snapshot,
    init_rumex_registry_db,
    list_block_types,
    list_carriers,
    list_document_fleet_vehicles,
    list_document_vehicle_binding_history,
    list_shipments,
    list_test_block_history,
    list_test_shipments,
    mark_test_er_sent_to_kontur,
    mark_er_confirmed,
    mark_er_sent,
    resubmit_test_shipment,
    return_test_shipment_for_correction,
    review_test_shipment,
    save_carrier,
)

REGISTRY_SAMPLES_DIR = Path(__file__).resolve().parent.parent / "docs" / "registry"
REGISTRY_SAMPLES = {
    "tn-pdf": {
        "title": "ТТН · образец 8102 (PDF)",
        "document_kind": "TN",
        "filename": "8102 образец.pdf",
        "content_type": "application/pdf",
    },
    "tn-xls": {
        "title": "ТТН · образец 8102 (Excel)",
        "document_kind": "TN",
        "filename": "8102 образец ТТН.xls",
        "content_type": "application/vnd.ms-excel",
    },
    "er": {
        "title": "ЭР · образец",
        "document_kind": "ER",
        "filename": "Образец ЭР.pdf",
        "content_type": "application/pdf",
    },
}


def _json(data: dict, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _shipment_id(request: web.Request) -> int | None:
    try:
        value = int(str(request.match_info.get("shipment_id") or ""))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _carrier_id(request: web.Request) -> int | None:
    try:
        value = int(str(request.match_info.get("carrier_id") or ""))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _test_shipment_id(request: web.Request) -> int | None:
    try:
        value = int(str(request.match_info.get("shipment_id") or ""))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _test_dispatcher_ids() -> set[int]:
    """Отдельный whitelist, чтобы тестовый кабинет не унаследовал боевые права."""
    result: set[int] = set()
    for value in (os.getenv("RUMEX_TEST_DISPATCHER_MAX_IDS") or "").split(","):
        try:
            user_id = int(value.strip())
        except (TypeError, ValueError):
            continue
        if user_id > 0:
            result.add(user_id)
    return result


def _test_dispatcher_identity(request: web.Request) -> tuple[dict | None, web.Response | None]:
    """Проверить парольную сессию либо подпись MAX тестового диспетчера."""
    password_user = test_dispatcher_user_from_request(request)
    if password_user:
        return {
            "max_user_id": 0,
            "name": password_user,
            "identity_kind": "password",
            "identity_id": password_user,
        }, None
    configured_ids = _test_dispatcher_ids()
    if not configured_ids:
        return None, _json({"error": "Тестовый доступ диспетчера не настроен"}, 503)
    init_data = init_data_from_request(request)
    parsed = validate_init_data(init_data, (os.getenv("MAX_BOT_TOKEN") or "").strip())
    if parsed is None or not isinstance(parsed.get("user"), dict):
        return None, _json({"error": "Откройте кабинет из MAX или войдите по паролю"}, 401)
    user = parsed["user"]
    user_id = user_id_from_user(user)
    if user_id is None or user_id not in configured_ids:
        return None, _json({"error": "Нет доступа к тестовому кабинету диспетчера"}, 403)
    return {
        "max_user_id": user_id,
        "name": display_name_from_user(user),
        "identity_kind": "max",
        "identity_id": str(user_id),
    }, None


def _test_viewer_identity(request: web.Request) -> tuple[dict | None, web.Response | None]:
    """Разрешить просмотр бухгалтеру либо тестовому диспетчеру MAX/по паролю."""
    accountant = user_from_request(request)
    if accountant:
        return {"role": "accountant", "name": accountant}, None
    dispatcher, denied = _test_dispatcher_identity(request)
    if denied is not None:
        return None, denied
    return {"role": "dispatcher", **(dispatcher or {})}, None


def _test_documents_are_open(shipment: dict) -> bool:
    return shipment.get("status") in {"documents_ready", "documents_handed_to_driver"}


def _test_ttn_copy_number(request: web.Request) -> int | None:
    try:
        copy_number = int(str(request.query.get("copy") or ""))
    except (TypeError, ValueError):
        return None
    return copy_number if copy_number in {1, 2, 3, 4} else None


def _test_ttn_response(shipment: dict, *, copy_number: int) -> web.Response:
    if not _test_documents_are_open(shipment):
        return _json({"error": "Документы ещё не открыты бухгалтером"}, 409)
    try:
        workbook = build_test_ttn_workbook(shipment, copy_number=copy_number)
    except ValueError as exc:
        return _json({"error": str(exc)}, 409)
    filename = test_ttn_filename(shipment, copy_number=copy_number)
    return web.Response(
        body=workbook,
        headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename)},
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def _sample_path(sample: dict) -> Path:
    return REGISTRY_SAMPLES_DIR / str(sample["filename"])


def _samples_payload() -> list[dict]:
    samples: list[dict] = []
    for sample_id, sample in REGISTRY_SAMPLES.items():
        path = _sample_path(sample)
        available = path.is_file()
        samples.append(
            {
                "id": sample_id,
                "title": sample["title"],
                "document_kind": sample["document_kind"],
                "available": available,
                "view_url": f"/api/rumex-registry/samples/{sample_id}" if available else "",
                "download_url": f"/api/rumex-registry/samples/{sample_id}/download" if available else "",
            }
        )
    return samples


def _require_registry_admin(request: web.Request) -> tuple[str | None, web.Response | None]:
    accountant = user_from_request(request)
    if not accountant:
        return None, _json({"error": "Требуется вход"}, 401)
    if not is_registry_admin(accountant):
        return None, _json({"error": "Редактировать справочник может только Бухгалтер 1"}, 403)
    return accountant, None


async def handle_registry(_request: web.Request) -> web.Response:
    return _json(
        {
            "site_label": "РУМЕКС · бухгалтерский реестр",
            "shipments": list_shipments(),
            "block_types": list_block_types(active_only=True),
        }
    )


async def handle_shipment(request: web.Request) -> web.Response:
    shipment_id = _shipment_id(request)
    if shipment_id is None:
        return _json({"error": "Некорректный номер отгрузки"}, 400)
    shipment = get_shipment(shipment_id)
    if shipment is None:
        return _json({"error": "Отгрузка не найдена"}, 404)
    return _json({"shipment": shipment})


async def handle_carriers(_request: web.Request) -> web.Response:
    return _json({"carriers": list_carriers()})


async def _save_carrier(request: web.Request, carrier_id: int | None) -> web.Response:
    accountant, denied = _require_registry_admin(request)
    if denied is not None:
        return denied
    try:
        body = await request.json()
    except Exception:
        return _json({"error": "Некорректный запрос"}, 400)
    if not isinstance(body, dict):
        return _json({"error": "Некорректный запрос"}, 400)
    active = body.get("active", True)
    if not isinstance(active, bool):
        return _json({"error": "Поле активности должно быть логическим значением"}, 400)
    try:
        carrier = save_carrier(
            carrier_id=carrier_id,
            name=body.get("name"),
            inn=body.get("inn"),
            kpp=body.get("kpp"),
            legal_address=body.get("legal_address"),
            confirmation_source=body.get("confirmation_source"),
            confirmation_reference=body.get("confirmation_reference"),
            ogrn=body.get("ogrn"),
            contact_name=body.get("contact_name"),
            contact_phone=body.get("contact_phone"),
            note=body.get("note"),
            active=active,
            actor_name=accountant or "",
        )
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)
    return _json({"ok": True, "carrier": carrier}, 201 if carrier_id is None else 200)


async def handle_carrier_create(request: web.Request) -> web.Response:
    return await _save_carrier(request, None)


async def handle_carrier_update(request: web.Request) -> web.Response:
    carrier_id = _carrier_id(request)
    if carrier_id is None:
        return _json({"error": "Некорректный номер перевозчика"}, 400)
    return await _save_carrier(request, carrier_id)


async def handle_document_fleet(_request: web.Request) -> web.Response:
    return _json({"vehicles": list_document_fleet_vehicles()})


async def handle_document_fleet_import(request: web.Request) -> web.Response:
    accountant, denied = _require_registry_admin(request)
    if denied is not None:
        return denied
    try:
        vehicles = import_document_fleet_snapshot(accountant_name=accountant or "")
    except ValueError as exc:
        return _json({"error": str(exc)}, 409)
    return _json({"ok": True, "vehicles": vehicles})


async def handle_document_vehicle(request: web.Request) -> web.Response:
    tail = str(request.match_info.get("plate_tail") or "").strip()
    try:
        vehicle = get_document_fleet_vehicle(tail)
        history = list_document_vehicle_binding_history(tail)
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)
    if vehicle is None:
        return _json({"error": "Машина не найдена"}, 404)
    return _json({"vehicle": vehicle, "binding_history": history})


async def handle_document_vehicle_binding(request: web.Request) -> web.Response:
    accountant, denied = _require_registry_admin(request)
    if denied is not None:
        return denied
    try:
        body = await request.json()
    except Exception:
        return _json({"error": "Некорректный запрос"}, 400)
    if not isinstance(body, dict):
        return _json({"error": "Некорректный запрос"}, 400)
    try:
        binding = confirm_document_vehicle_binding(
            plate_tail=request.match_info.get("plate_tail"),
            carrier_id=body.get("carrier_id"),
            driver_full_name=body.get("driver_full_name"),
            driver_license_number=body.get("driver_license_number"),
            accountant_name=accountant or "",
            note=body.get("note"),
        )
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)
    return _json({"ok": True, "binding": binding}, 201)


async def handle_test_registry(_request: web.Request) -> web.Response:
    _viewer, denied = _test_viewer_identity(_request)
    if denied is not None:
        return denied
    return _json(
        {
            "site_label": "Завод Румекс · тестовая погрузка",
            "shipments": list_test_shipments(),
            "block_types": list_block_types(active_only=True),
        }
    )


async def handle_test_shipment(request: web.Request) -> web.Response:
    _viewer, denied = _test_viewer_identity(request)
    if denied is not None:
        return denied
    shipment_id = _test_shipment_id(request)
    if shipment_id is None:
        return _json({"error": "Некорректный номер тестовой погрузки"}, 400)
    shipment = get_test_shipment(shipment_id)
    if shipment is None:
        return _json({"error": "Тестовая погрузка не найдена"}, 404)
    return _json({"shipment": shipment})


async def handle_test_block_history(request: web.Request) -> web.Response:
    _viewer, denied = _test_viewer_identity(request)
    if denied is not None:
        return denied
    try:
        items = list_test_block_history(
            request.match_info.get("block_type_code"), request.match_info.get("block_number")
        )
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)
    return _json({"items": items})


async def handle_test_vehicle_lookup(request: web.Request) -> web.Response:
    dispatcher, denied = _test_dispatcher_identity(request)
    if denied is not None:
        return denied
    del dispatcher
    try:
        vehicle = get_document_fleet_vehicle(request.match_info.get("plate_tail"))
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)
    if vehicle is None or not vehicle.get("source_present") or not vehicle.get("source_active"):
        return _json({"found": False, "reason": "Машина не найдена среди активных машин парка"})
    binding = vehicle.get("document_binding")
    if binding is None:
        return _json({"found": False, "reason": "Нет подтверждённой бухгалтером документной карточки"})
    if not str(binding.get("driver_license_number") or "").strip():
        return _json(
            {
                "found": False,
                "reason": (
                    "В подтверждённой карточке машины нет номера водительского удостоверения. "
                    "Попросите бухгалтера подтвердить новую карточку машины."
                ),
            }
        )
    return _json({"found": True, "vehicle": vehicle, "document_binding": binding})


async def handle_test_shipment_create(request: web.Request) -> web.Response:
    dispatcher, denied = _test_dispatcher_identity(request)
    if denied is not None:
        return denied
    try:
        body = await request.json()
    except Exception:
        return _json({"error": "Некорректный запрос"}, 400)
    if not isinstance(body, dict):
        return _json({"error": "Некорректный запрос"}, 400)
    try:
        shipment = create_test_shipment(
            plate_tail=body.get("plate_tail"),
            block_count=body.get("block_count"),
            items=body.get("items") if isinstance(body.get("items"), list) else [],
            dispatcher_max_user_id=int(dispatcher["max_user_id"]),
            dispatcher_name=str(dispatcher["name"]),
            loaded_at=body.get("loaded_at"),
            dispatcher_identity_kind=str(dispatcher["identity_kind"]),
            dispatcher_identity_id=str(dispatcher["identity_id"]),
        )
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)
    return _json({"ok": True, "shipment": shipment}, 201)


async def handle_test_shipment_resubmit(request: web.Request) -> web.Response:
    shipment_id = _test_shipment_id(request)
    if shipment_id is None:
        return _json({"error": "Некорректный номер тестовой погрузки"}, 400)
    dispatcher, denied = _test_dispatcher_identity(request)
    if denied is not None:
        return denied
    try:
        body = await request.json()
    except Exception:
        return _json({"error": "Некорректный запрос"}, 400)
    if not isinstance(body, dict):
        return _json({"error": "Некорректный запрос"}, 400)
    try:
        shipment = resubmit_test_shipment(
            shipment_id,
            block_count=body.get("block_count"),
            items=body.get("items") if isinstance(body.get("items"), list) else [],
            loaded_at=body.get("loaded_at"),
            dispatcher_max_user_id=int(dispatcher["max_user_id"]),
            dispatcher_name=str(dispatcher["name"]),
            dispatcher_identity_kind=str(dispatcher["identity_kind"]),
            dispatcher_identity_id=str(dispatcher["identity_id"]),
        )
    except ValueError as exc:
        return _json({"error": str(exc)}, 409)
    return _json({"ok": True, "shipment": shipment})


async def handle_test_shipment_handed_to_driver(request: web.Request) -> web.Response:
    shipment_id = _test_shipment_id(request)
    if shipment_id is None:
        return _json({"error": "Некорректный номер тестовой погрузки"}, 400)
    dispatcher, denied = _test_dispatcher_identity(request)
    if denied is not None:
        return denied
    try:
        shipment = confirm_test_documents_handed_to_driver(
            shipment_id,
            dispatcher_max_user_id=int(dispatcher["max_user_id"]),
            dispatcher_name=str(dispatcher["name"]),
            dispatcher_identity_kind=str(dispatcher["identity_kind"]),
            dispatcher_identity_id=str(dispatcher["identity_id"]),
        )
    except ValueError as exc:
        return _json({"error": str(exc)}, 409)
    return _json({"ok": True, "shipment": shipment})


async def handle_test_ttn_download(request: web.Request) -> web.Response:
    shipment_id = _test_shipment_id(request)
    if shipment_id is None:
        return _json({"error": "Некорректный номер тестовой погрузки"}, 400)
    dispatcher, denied = _test_dispatcher_identity(request)
    if denied is not None:
        return denied
    del dispatcher
    shipment = get_test_shipment(shipment_id)
    if shipment is None:
        return _json({"error": "Тестовая погрузка не найдена"}, 404)
    copy_number = _test_ttn_copy_number(request)
    if copy_number is None:
        return _json({"error": "Укажите экземпляр ТТН: copy=1, 2, 3 или 4"}, 400)
    return _test_ttn_response(shipment, copy_number=copy_number)


async def _test_accountant_action(request: web.Request, action: str) -> web.Response:
    shipment_id = _test_shipment_id(request)
    if shipment_id is None:
        return _json({"error": "Некорректный номер тестовой погрузки"}, 400)
    accountant = user_from_request(request)
    if not accountant:
        return _json({"error": "Требуется вход"}, 401)
    try:
        body = await request.json()
    except Exception:
        return _json({"error": "Некорректный запрос"}, 400)
    if not isinstance(body, dict):
        return _json({"error": "Некорректный запрос"}, 400)
    try:
        if action == "return":
            shipment = return_test_shipment_for_correction(
                shipment_id, accountant_name=accountant, reason=body.get("reason")
            )
            message = "Погрузка возвращена диспетчеру для исправления."
        elif action == "review":
            er_required = body.get("er_required")
            shipment = review_test_shipment(
                shipment_id, accountant_name=accountant, er_required=er_required
            )
            message = "Погрузка проверена. " + (
                "Отметьте отправку расписки в Контур." if er_required else "ТТН открыта диспетчеру."
            )
        else:
            shipment = mark_test_er_sent_to_kontur(
                shipment_id,
                accountant_name=accountant,
                external_reference=body.get("external_reference"),
            )
            message = "Расписка отмечена как отправленная в ЭДО Контур. Документы открыты диспетчеру."
    except ValueError as exc:
        return _json({"error": str(exc)}, 409)
    return _json({"ok": True, "message": message, "shipment": shipment})


async def handle_test_shipment_return(request: web.Request) -> web.Response:
    return await _test_accountant_action(request, "return")


async def handle_test_shipment_review(request: web.Request) -> web.Response:
    return await _test_accountant_action(request, "review")


async def handle_test_shipment_er_sent(request: web.Request) -> web.Response:
    return await _test_accountant_action(request, "er_sent")


async def handle_samples(_request: web.Request) -> web.Response:
    return _json({"samples": _samples_payload()})


async def handle_sample(request: web.Request) -> web.StreamResponse:
    sample_id = str(request.match_info.get("sample_id") or "")
    sample = REGISTRY_SAMPLES.get(sample_id)
    if sample is None:
        return _json({"error": "Образец не найден"}, 404)
    path = _sample_path(sample)
    if not path.is_file():
        return _json({"error": "Образец ещё не загружен"}, 404)
    response = web.FileResponse(path)
    response.content_type = str(sample["content_type"])
    if request.path.endswith("/download"):
        response.headers["Content-Disposition"] = (
            "attachment; filename*=UTF-8''" + quote(str(sample["filename"]))
        )
    return response


async def _apply_er_action(request: web.Request, action: str) -> web.Response:
    shipment_id = _shipment_id(request)
    if shipment_id is None:
        return _json({"error": "Некорректный номер отгрузки"}, 400)
    accountant = user_from_request(request)
    if not accountant:  # Защита на случай изменения порядка middleware.
        return _json({"error": "Требуется вход"}, 401)
    try:
        body = await request.json()
    except Exception:
        return _json({"error": "Некорректный запрос"}, 400)
    reference = str(body.get("external_reference") or "").strip()
    try:
        if action == "sent":
            shipment = mark_er_sent(
                shipment_id,
                accountant_name=accountant,
                external_reference=reference,
            )
            message = "ЭР отмечена как отправленная в Контур."
        else:
            shipment = mark_er_confirmed(
                shipment_id,
                accountant_name=accountant,
                external_reference=reference,
            )
            message = "ЭР подтверждена. ТТН доступна диспетчеру."
    except ValueError as exc:
        return _json({"error": str(exc)}, 409)
    return _json({"ok": True, "message": message, "shipment": shipment})


async def handle_er_sent(request: web.Request) -> web.Response:
    return await _apply_er_action(request, "sent")


async def handle_er_confirmed(request: web.Request) -> web.Response:
    return await _apply_er_action(request, "confirmed")


def register_rumex_registry_routes(app: web.Application) -> None:
    init_rumex_registry_db()
    app.router.add_get("/api/rumex-registry/registry", handle_registry)
    app.router.add_get("/api/rumex-registry/shipments/{shipment_id}", handle_shipment)
    app.router.add_post("/api/rumex-registry/shipments/{shipment_id}/er-sent", handle_er_sent)
    app.router.add_post("/api/rumex-registry/shipments/{shipment_id}/er-confirmed", handle_er_confirmed)
    app.router.add_get("/api/rumex-registry/carriers", handle_carriers)
    app.router.add_post("/api/rumex-registry/carriers", handle_carrier_create)
    app.router.add_put("/api/rumex-registry/carriers/{carrier_id}", handle_carrier_update)
    app.router.add_get("/api/rumex-registry/document-fleet", handle_document_fleet)
    app.router.add_post("/api/rumex-registry/document-fleet/import", handle_document_fleet_import)
    app.router.add_get("/api/rumex-registry/document-fleet/{plate_tail}", handle_document_vehicle)
    app.router.add_post(
        "/api/rumex-registry/document-fleet/{plate_tail}/binding",
        handle_document_vehicle_binding,
    )
    app.router.add_get("/api/rumex-registry/test/registry", handle_test_registry)
    app.router.add_get("/api/rumex-registry/test/shipments/{shipment_id}", handle_test_shipment)
    app.router.add_get(
        "/api/rumex-registry/test/blocks/{block_type_code}/{block_number}/history",
        handle_test_block_history,
    )
    app.router.add_get(
        "/api/rumex-registry/test/vehicles/{plate_tail}", handle_test_vehicle_lookup
    )
    app.router.add_post("/api/rumex-registry/test/shipments", handle_test_shipment_create)
    app.router.add_post(
        "/api/rumex-registry/test/shipments/{shipment_id}/resubmit",
        handle_test_shipment_resubmit,
    )
    app.router.add_post(
        "/api/rumex-registry/test/shipments/{shipment_id}/handed-to-driver",
        handle_test_shipment_handed_to_driver,
    )
    app.router.add_get(
        "/api/rumex-registry/test/shipments/{shipment_id}/documents/tn",
        handle_test_ttn_download,
    )
    app.router.add_post(
        "/api/rumex-registry/test/shipments/{shipment_id}/return",
        handle_test_shipment_return,
    )
    app.router.add_post(
        "/api/rumex-registry/test/shipments/{shipment_id}/review",
        handle_test_shipment_review,
    )
    app.router.add_post(
        "/api/rumex-registry/test/shipments/{shipment_id}/er-sent",
        handle_test_shipment_er_sent,
    )
    app.router.add_get("/api/rumex-registry/samples", handle_samples)
    app.router.add_get("/api/rumex-registry/samples/{sample_id}", handle_sample)
    app.router.add_get("/api/rumex-registry/samples/{sample_id}/download", handle_sample)
