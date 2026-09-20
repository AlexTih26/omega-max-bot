"""HTTP API отдельного бухгалтерского реестра РУМЕКС."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib.parse import quote

from aiohttp import web

import rumex_registry_backup
from rumex_registry_auth import is_registry_admin, user_from_request
from rumex_registry_documents import (
    archive_test_ttn_workbooks,
    archived_test_ttn_path,
    build_test_ttn_workbook,
    test_ttn_filename,
)
from rumex_registry_export import build_test_registry_workbook
from rumex_registry_notifications import notify_new_test_shipment
from rumex_test_dispatcher_auth import user_from_request as test_dispatcher_user_from_request
from rumex_registry_store import (
    DB_PATH,
    confirm_document_vehicle_binding,
    confirm_test_documents_handed_to_driver,
    claim_test_shipment,
    create_test_shipment,
    get_accountant_availability,
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
    mark_test_ttn_downloaded,
    mark_test_er_sent_to_kontur,
    mark_er_confirmed,
    mark_er_sent,
    release_due_test_shipments,
    resubmit_test_shipment,
    return_test_shipment_for_correction,
    review_test_shipment,
    save_carrier,
    set_accountant_availability,
)

logger = logging.getLogger(__name__)

_CLIENT_ERROR_SECRET_RE = re.compile(
    r"(?i)\b(cookie|token|authorization|bearer|password|passwd|pin)\b\s*[:=]\s*[^\s,;]+"
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


def _client_error_text(value: object, *, limit: int) -> str:
    """Оставить в журнале только короткий однострочный технический текст."""
    text = " ".join(str(value or "").split())
    return _CLIENT_ERROR_SECRET_RE.sub(r"\1=[скрыто]", text)[:limit]


async def handle_client_error(request: web.Request) -> web.Response:
    """Принять обезличенную ошибку браузерного интерфейса РУМЕКС.

    Маршрут доступен до входа, чтобы фиксировать ошибки страниц авторизации.
    Клиент намеренно не отправляет cookies, токены, данные форм или stack trace.
    """
    try:
        body = await request.json()
    except Exception:
        return _json({"error": "Некорректный отчёт об ошибке"}, 400)
    if not isinstance(body, dict):
        return _json({"error": "Некорректный отчёт об ошибке"}, 400)

    kind = _client_error_text(body.get("kind"), limit=40)
    message = _client_error_text(body.get("message"), limit=500)
    source = _client_error_text(body.get("source"), limit=300)
    try:
        line = max(0, int(body.get("line") or 0))
        column = max(0, int(body.get("column") or 0))
    except (TypeError, ValueError):
        return _json({"error": "Некорректная позиция ошибки"}, 400)
    if kind not in {"error", "unhandledrejection", "console-error"} or not message:
        return _json({"error": "Некорректный отчёт об ошибке"}, 400)

    logger.error(
        "РУМЕКС клиентская ошибка: kind=%s source=%s line=%s column=%s message=%s",
        kind,
        source or "—",
        line,
        column,
        message,
    )
    return _json({"ok": True}, 202)


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


def _test_dispatcher_identity(request: web.Request) -> tuple[dict | None, web.Response | None]:
    """Проверить отдельную парольную сессию диспетчера тестового кабинета."""
    password_user = test_dispatcher_user_from_request(request)
    if password_user:
        return {
            "max_user_id": 0,
            "name": password_user,
            "identity_kind": "password",
            "identity_id": password_user,
        }, None
    return None, _json({"error": "Требуется вход по паролю"}, 401)


def _test_viewer_identity(request: web.Request) -> tuple[dict | None, web.Response | None]:
    """Разрешить просмотр бухгалтеру либо диспетчеру с парольной сессией."""
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
    archive = next(
        (item for item in shipment.get("ttn_archives", []) if item.get("copy_number") == copy_number),
        None,
    )
    if archive is not None:
        path = archived_test_ttn_path(str(archive.get("filename") or ""))
        if path is None:
            logger.error("Не найден архив ТТН: shipment_id=%s copy=%s", shipment.get("id"), copy_number)
            return _json({"error": "Архив выданной ТТН недоступен"}, 409)
        workbook = path.read_bytes()
    else:
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


def _registry_archive_status() -> dict[str, float | None]:
    """Вернуть время последнего сохранения реестра и успешной резервной копии."""
    try:
        registry_saved_at = max(
            path.stat().st_mtime for path in (DB_PATH, DB_PATH.with_name(DB_PATH.name + "-wal"))
            if path.is_file()
        )
    except OSError:
        registry_saved_at = None
    backup_paths = list((DB_PATH.parent / "backups").glob("rumex-registry-*.db"))
    try:
        backup_saved_at = max((path.stat().st_mtime for path in backup_paths), default=None)
    except OSError:
        backup_saved_at = None
    return {"registry_saved_at": registry_saved_at, "backup_saved_at": backup_saved_at}


def _backup_after_registry_write(reason: str) -> None:
    """Сохранить копию после подтверждённой записи, не отменяя саму запись.

    Изменение уже атомарно зафиксировано в SQLite к моменту этого вызова.
    Ошибка хранилища резервных копий не должна выдавать пользователю ложную
    ошибку о сохранении, но обязательно остаётся в журнале для реакции.
    """
    try:
        rumex_registry_backup.backup_rumex_registry_db(reason=reason)
    except Exception:
        logger.exception("РУМЕКС реестр: не удалось создать бэкап после %s", reason)


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
            "archive_status": _registry_archive_status(),
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
    _backup_after_registry_write("carrier_saved")
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
    _backup_after_registry_write("document_fleet_imported")
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
    _backup_after_registry_write("document_vehicle_bound")
    return _json({"ok": True, "binding": binding}, 201)


async def handle_test_registry(_request: web.Request) -> web.Response:
    _viewer, denied = _test_viewer_identity(_request)
    if denied is not None:
        return denied
    released = release_due_test_shipments()
    if released:
        _backup_after_registry_write("overdue_test_shipments_released")
    return _json(
        {
            "site_label": "Завод Румекс · тестовая погрузка",
            "shipments": list_test_shipments(),
            "block_types": list_block_types(active_only=True),
            "archive_status": _registry_archive_status(),
        }
    )


async def handle_test_registry_export(request: web.Request) -> web.Response:
    _viewer, denied = _test_viewer_identity(request)
    if denied is not None:
        return denied
    date_from = str(request.query.get("date_from") or "")
    date_to = str(request.query.get("date_to") or "")
    search = str(request.query.get("search") or "").strip()
    if (date_from and not date_from.isascii()) or (date_to and not date_to.isascii()) or len(search) > 100:
        return _json({"error": "Некорректные фильтры экспорта"}, 400)
    try:
        workbook = build_test_registry_workbook(
            list_test_shipments(), date_from=date_from, date_to=date_to, search=search
        )
    except (TypeError, ValueError):
        return _json({"error": "Некорректные фильтры экспорта"}, 400)
    return web.Response(
        body=workbook,
        headers={"Content-Disposition": "attachment; filename*=UTF-8''rumex-registry.xlsx"},
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


async def handle_test_shipment(request: web.Request) -> web.Response:
    _viewer, denied = _test_viewer_identity(request)
    if denied is not None:
        return denied
    shipment_id = _test_shipment_id(request)
    if shipment_id is None:
        return _json({"error": "Некорректный номер тестовой погрузки"}, 400)
    released = release_due_test_shipments()
    if released:
        _backup_after_registry_write("overdue_test_shipments_released")
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
    _backup_after_registry_write("test_shipment_created")
    request.app.loop.create_task(notify_new_test_shipment(shipment))
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
    _backup_after_registry_write("test_shipment_resubmitted")
    return _json({"ok": True, "shipment": shipment})


async def handle_test_shipment_handed_to_driver(request: web.Request) -> web.Response:
    shipment_id = _test_shipment_id(request)
    if shipment_id is None:
        return _json({"error": "Некорректный номер тестовой погрузки"}, 400)
    dispatcher, denied = _test_dispatcher_identity(request)
    if denied is not None:
        return denied
    try:
        current_shipment = get_test_shipment(shipment_id)
        if current_shipment is None:
            return _json({"error": "Тестовая погрузка не найдена"}, 404)
        archived = [] if current_shipment.get("status") == "documents_handed_to_driver" else archive_test_ttn_workbooks(current_shipment)
        shipment = confirm_test_documents_handed_to_driver(
            shipment_id,
            dispatcher_max_user_id=int(dispatcher["max_user_id"]),
            dispatcher_name=str(dispatcher["name"]),
            dispatcher_identity_kind=str(dispatcher["identity_kind"]),
            dispatcher_identity_id=str(dispatcher["identity_id"]),
            ttn_archives=archived,
        )
    except ValueError as exc:
        return _json({"error": str(exc)}, 409)
    _backup_after_registry_write("test_documents_handed_to_driver")
    return _json({"ok": True, "shipment": shipment})


async def handle_test_ttn_download(request: web.Request) -> web.Response:
    shipment_id = _test_shipment_id(request)
    if shipment_id is None:
        return _json({"error": "Некорректный номер тестовой погрузки"}, 400)
    viewer, denied = _test_viewer_identity(request)
    if denied is not None:
        return denied
    shipment = get_test_shipment(shipment_id)
    if shipment is None:
        return _json({"error": "Тестовая погрузка не найдена"}, 404)
    copy_number = _test_ttn_copy_number(request)
    if copy_number is None:
        return _json({"error": "Укажите экземпляр ТТН: copy=1, 2, 3 или 4"}, 400)
    if viewer and viewer.get("role") == "dispatcher":
        try:
            shipment = mark_test_ttn_downloaded(
                shipment_id,
                dispatcher_max_user_id=int(viewer["max_user_id"]),
                dispatcher_name=str(viewer["name"]),
                dispatcher_identity_kind=str(viewer["identity_kind"]),
                dispatcher_identity_id=str(viewer["identity_id"]),
                copy_number=copy_number,
            )
        except ValueError as exc:
            return _json({"error": str(exc)}, 409)
        _backup_after_registry_write("test_ttn_downloaded")
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
    _backup_after_registry_write(f"test_shipment_{action}")
    return _json({"ok": True, "message": message, "shipment": shipment})


async def handle_test_shipment_claim(request: web.Request) -> web.Response:
    shipment_id = _test_shipment_id(request)
    if shipment_id is None:
        return _json({"error": "Некорректный номер тестовой погрузки"}, 400)
    accountant = user_from_request(request)
    if not accountant:
        return _json({"error": "Требуется вход"}, 401)
    try:
        release_due_test_shipments()
        shipment = claim_test_shipment(shipment_id, accountant_name=accountant)
    except ValueError as exc:
        return _json({"error": str(exc)}, 409)
    _backup_after_registry_write("test_shipment_claimed")
    return _json({"ok": True, "message": "Погрузка взята в работу.", "shipment": shipment})


async def handle_accountant_availability(request: web.Request) -> web.Response:
    accountant = user_from_request(request)
    if not accountant:
        return _json({"error": "Требуется вход"}, 401)
    if request.method == "GET":
        return _json({"availability": get_accountant_availability(accountant)})
    try:
        body = await request.json()
        availability = set_accountant_availability(accountant, body.get("availability"))
    except (ValueError, AttributeError) as exc:
        return _json({"error": str(exc) or "Некорректный запрос"}, 400)
    _backup_after_registry_write("accountant_availability_changed")
    return _json({"ok": True, "availability": availability})


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
    _backup_after_registry_write(f"shipment_er_{action}")
    return _json({"ok": True, "message": message, "shipment": shipment})


async def handle_er_sent(request: web.Request) -> web.Response:
    return await _apply_er_action(request, "sent")


async def handle_er_confirmed(request: web.Request) -> web.Response:
    return await _apply_er_action(request, "confirmed")


def register_rumex_registry_routes(app: web.Application) -> None:
    init_rumex_registry_db()
    app.router.add_post("/api/rumex-registry/client-errors", handle_client_error)
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
    app.router.add_get("/api/rumex-registry/test/registry/export", handle_test_registry_export)
    app.router.add_get("/api/rumex-registry/accountant/availability", handle_accountant_availability)
    app.router.add_put("/api/rumex-registry/accountant/availability", handle_accountant_availability)
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
        "/api/rumex-registry/test/shipments/{shipment_id}/claim",
        handle_test_shipment_claim,
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
