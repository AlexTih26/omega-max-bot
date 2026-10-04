"""HTTP API нового изолированного контура PWA Таксимо."""

from __future__ import annotations

from typing import Any

import hmac
import os
from datetime import datetime, timezone
from io import BytesIO
from urllib.parse import quote
from zoneinfo import ZoneInfo

from aiohttp import web

from taksimo_new_auth import operator_from_request
from taksimo_new_db import TaksimoNewDatabaseError
import taksimo_new_store as store
import taksimo_legacy_reader as legacy


def _json(data: dict, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _operator(request: web.Request) -> tuple[dict | None, web.Response | None]:
    operator = operator_from_request(request)
    if operator is None:
        return None, _json({"error": "Требуется вход"}, 401)
    return operator, None


def _service_authorized(request: web.Request) -> bool:
    configured = _integration_token()
    authorization = (request.headers.get("Authorization") or "").strip()
    prefix = "Bearer "
    return bool(configured and authorization.startswith(prefix) and hmac.compare_digest(authorization[len(prefix):], configured))


def _integration_token() -> str:
    return (os.getenv("TAKSIMO_NEW_INTEGRATION_TOKEN") or "").strip()


def _service_unavailable_response() -> web.Response | None:
    if not _integration_token():
        return _json({"error": "Служебный мост РУМЕКС/диспетчера ещё не настроен"}, 503)
    return None


async def _body(request: web.Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        raise ValueError("Некорректный JSON") from None
    if not isinstance(body, dict):
        raise ValueError("Некорректный JSON")
    return body


async def handle_dashboard(_request: web.Request) -> web.Response:
    return _json(store.dashboard())


async def handle_intakes(request: web.Request) -> web.Response:
    if request.method == "GET":
        return _json({"intakes": store.list_intakes(date_value=request.query.get("date"), limit=request.query.get("limit", 100))})
    operator, denied = _operator(request)
    if denied:
        return denied
    try:
        return _json({"intake": store.create_manual_intake(await _body(request), operator=operator)}, 201)
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)


async def handle_intake(request: web.Request) -> web.Response:
    intake = store.get_intake(request.match_info["public_id"])
    if intake is None:
        return _json({"error": "Приёмка не найдена"}, 404)
    return _json({"intake": intake})


async def handle_intake_claim(request: web.Request) -> web.Response:
    operator, denied = _operator(request)
    if denied:
        return denied
    try:
        return _json({"intake": store.claim_intake(request.match_info["public_id"], operator=operator)})
    except KeyError:
        return _json({"error": "Приёмка не найдена"}, 404)
    except store.TaksimoNewConflictError as exc:
        return _json({"error": str(exc)}, 409)


async def handle_intake_confirm(request: web.Request) -> web.Response:
    operator, denied = _operator(request)
    if denied:
        return denied
    try:
        body = await _body(request)
        lines = body.get("lines")
        if not isinstance(lines, list):
            raise ValueError("Передайте список блоков")
        return _json({"intake": store.confirm_intake(request.match_info["public_id"], lines=lines, operator=operator)})
    except KeyError:
        return _json({"error": "Приёмка не найдена"}, 404)
    except store.TaksimoNewConflictError as exc:
        return _json({"error": str(exc)}, 409)
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)


async def handle_intake_draft(request: web.Request) -> web.Response:
    operator, denied = _operator(request)
    if denied:
        return denied
    try:
        body = await _body(request)
        lines = body.get("lines")
        if not isinstance(lines, list):
            raise ValueError("Передайте список блоков")
        return _json({"intake": store.save_intake_draft(request.match_info["public_id"], lines=lines, operator=operator)})
    except KeyError:
        return _json({"error": "Приёмка не найдена"}, 404)
    except store.TaksimoNewConflictError as exc:
        return _json({"error": str(exc)}, 409)
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)


async def _handle_intake_fact(request: web.Request, recorder) -> web.Response:
    operator, denied = _operator(request)
    if denied:
        return denied
    try:
        return _json({"intake": recorder(request.match_info["public_id"], operator=operator)})
    except KeyError:
        return _json({"error": "Приёмка не найдена"}, 404)
    except store.TaksimoNewConflictError as exc:
        return _json({"error": str(exc)}, 409)
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)


async def handle_intake_arrival(request: web.Request) -> web.Response:
    return await _handle_intake_fact(request, store.record_intake_arrival)


async def handle_intake_crane_started(request: web.Request) -> web.Response:
    return await _handle_intake_fact(request, store.record_intake_crane_started)


async def handle_intake_crane_ended(request: web.Request) -> web.Response:
    return await _handle_intake_fact(request, store.record_intake_crane_ended)


async def handle_intake_cancel(request: web.Request) -> web.Response:
    operator, denied = _operator(request)
    if denied:
        return denied
    try:
        body = await _body(request)
        return _json({"intake": store.cancel_intake(request.match_info["public_id"], reason=body.get("reason"), operator=operator)})
    except PermissionError as exc:
        return _json({"error": str(exc)}, 403)
    except KeyError:
        return _json({"error": "Приёмка не найдена"}, 404)
    except store.TaksimoNewConflictError as exc:
        return _json({"error": str(exc)}, 409)
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)


async def handle_corrections(request: web.Request) -> web.Response:
    operator, denied = _operator(request)
    if denied:
        return denied
    try:
        body = await _body(request)
        correction = store.create_correction(
            subject_type=str(body.get("subject_type") or ""), subject_public_id=str(body.get("subject_id") or ""),
            reason=body.get("reason"), details=body.get("details"), operator=operator,
        )
        return _json({"correction": correction}, 201)
    except PermissionError as exc:
        return _json({"error": str(exc)}, 403)
    except KeyError:
        return _json({"error": "Подтверждённая операция не найдена"}, 404)
    except store.TaksimoNewConflictError as exc:
        return _json({"error": str(exc)}, 409)
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)


async def handle_operation_cancel(request: web.Request) -> web.Response:
    operator, denied = _operator(request)
    if denied:
        return denied
    try:
        body = await _body(request)
        cancellation = store.cancel_operation(
            subject_type=request.match_info["subject_type"], subject_public_id=request.match_info["subject_id"],
            reason=body.get("reason"), operator=operator,
        )
        return _json({"cancellation": cancellation}, 201)
    except PermissionError as exc:
        return _json({"error": str(exc)}, 403)
    except KeyError:
        return _json({"error": "Подтверждённая операция не найдена"}, 404)
    except store.TaksimoNewConflictError as exc:
        return _json({"error": str(exc)}, 409)
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)


async def handle_yard(_request: web.Request) -> web.Response:
    return _json(store.yard_map())


async def handle_wagons(_request: web.Request) -> web.Response:
    return _json({"wagons": store.list_wagons()})


async def handle_wagon_history(request: web.Request) -> web.Response:
    operator, denied = _operator(request)
    if denied:
        return denied
    number = request.match_info["wagon_number"]
    history = store.wagon_history(number)
    legacy_trips = [
        trip for trip in legacy.list_legacy_wagon_history()
        if str(trip.get("wagon_number") or "") == str(number)
    ]
    blocks_by_dispatch: dict[int, list[dict[str, Any]]] = {}
    for slab in legacy.list_legacy_dispatch_slabs():
        if str(slab.get("wagon_number") or "") != str(number):
            continue
        dispatch_id = slab.get("wagon_dispatch_id")
        if not isinstance(dispatch_id, int):
            continue
        blocks_by_dispatch.setdefault(dispatch_id, []).append(
            {"letter": slab.get("letter") or "", "number": slab.get("number") or "",
             "weight": slab.get("weight") or "", "loading_date": slab.get("loading_date") or ""}
        )
    for trip in legacy_trips:
        trip["blocks"] = blocks_by_dispatch.get(int(trip["id"]), [])
        for field in ("dispatched_at", "received_at"):
            value = trip.get(field)
            if isinstance(value, (int, float)):
                trip[field] = datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    history["trips"] = legacy_trips
    history["circles"] = len(legacy_trips)
    return _json({"history": history})


async def handle_wagon_load(request: web.Request) -> web.Response:
    operator, denied = _operator(request)
    if denied:
        return denied
    try:
        body = await _body(request)
        return _json({"load": store.load_block_to_wagon(block_id=body.get("block_id"), wagon_number=body.get("wagon_number"), operator=operator)}, 201)
    except KeyError:
        return _json({"error": "Блок не найден"}, 404)
    except store.TaksimoNewConflictError as exc:
        return _json({"error": str(exc)}, 409)
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)


async def handle_wagon_dispatch(request: web.Request) -> web.Response:
    operator, denied = _operator(request)
    if denied:
        return denied
    try:
        return _json({"wagon": store.dispatch_wagon(request.match_info["wagon_number"], operator=operator)})
    except PermissionError as exc:
        return _json({"error": str(exc)}, 403)
    except KeyError:
        return _json({"error": "Вагон не найден"}, 404)
    except store.TaksimoNewConflictError as exc:
        return _json({"error": str(exc)}, 409)
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)


async def _handle_wagon_transition(request: web.Request, transition) -> web.Response:
    operator, denied = _operator(request)
    if denied:
        return denied
    try:
        return _json({"wagon": transition(request.match_info["wagon_number"], operator=operator)})
    except PermissionError as exc:
        return _json({"error": str(exc)}, 403)
    except KeyError:
        return _json({"error": "Вагон не найден"}, 404)
    except store.TaksimoNewConflictError as exc:
        return _json({"error": str(exc)}, 409)
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)


async def handle_wagon_loaded(request: web.Request) -> web.Response:
    return await _handle_wagon_transition(request, store.mark_wagon_loaded)


async def handle_wagon_arrived_kodar(request: web.Request) -> web.Response:
    return await _handle_wagon_transition(request, store.mark_wagon_arrived_kodar)


async def handle_wagon_unloaded_bts_east(request: web.Request) -> web.Response:
    return await _handle_wagon_transition(request, store.mark_wagon_unloaded_bts_east)


async def handle_wagon_returned_empty(request: web.Request) -> web.Response:
    return await _handle_wagon_transition(request, store.mark_wagon_returned_empty)


_SEARCH_KINDS = {"block", "wagon", "intake", "slab", "vehicle"}


def _legacy_search_results(text: str, kind: str) -> list[dict[str, Any]]:
    """Read-only поиск по справочникам старой Таксимо (плиты, машины, вагоны)."""
    upper = text.upper()
    results: list[dict[str, Any]] = []
    if not kind or kind == "slab":
        for slab in legacy.list_legacy_slabs():
            needle = (str(slab.get("letter") or "") + str(slab.get("number") or "")).upper()
            if upper in needle:
                results.append({
                    "kind": "slab",
                    "label": f'{slab.get("letter") or ""} {slab.get("number") or ""}'.strip(),
                    "letter": slab.get("letter"), "number": slab.get("number"),
                    "platform_zone": slab.get("platform_zone"), "wagon_number": slab.get("wagon_number"),
                    "on_yard": slab.get("on_yard"), "legacy": True,
                })
    if not kind or kind == "vehicle":
        for vehicle in legacy.list_legacy_vehicles():
            needle = " ".join(str(vehicle.get(f) or "") for f in ("plate", "brand", "driver")).upper()
            if upper in needle:
                results.append({
                    "kind": "vehicle",
                    "label": str(vehicle.get("plate") or "—"),
                    "plate": vehicle.get("plate"), "brand": vehicle.get("brand"), "driver": vehicle.get("driver"),
                    "active": vehicle.get("active"), "legacy": True,
                })
    if not kind or kind == "wagon":
        for wagon in legacy.list_legacy_wagons():
            if upper in str(wagon.get("number") or "").upper():
                results.append({
                    "kind": "wagon",
                    "label": "Вагон " + str(wagon.get("number") or ""),
                    "number": wagon.get("number"), "stage": wagon.get("stage"),
                    "planned_zone": wagon.get("planned_zone"), "legacy": True,
                })
    return results


async def handle_search(request: web.Request) -> web.Response:
    operator, denied = _operator(request)
    if denied:
        return denied
    query = (request.query.get("q") or "").strip()
    if not query:
        return _json({"results": []})
    kind = (request.query.get("kind") or "").strip().lower()
    if kind and kind not in _SEARCH_KINDS:
        return _json({"error": "Некорректная категория поиска"}, 400)
    try:
        results = store.search(query, limit=request.query.get("limit", 50))
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)
    results.extend(_legacy_search_results(query, kind))
    if kind:
        results = [item for item in results if item.get("kind") == kind]
    return _json({"results": results[:50]})


async def handle_events(request: web.Request) -> web.Response:
    return _json({"events": store.list_events(limit=request.query.get("limit", 100))})


_EVENT_LABELS_RU = {
    "intake_expected_imported": "Получена ожидаемая приёмка из моста РУМЕКС",
    "intake_draft_created": "Создана ручная ожидаемая приёмка",
    "intake_locked": "Приёмка взята в работу",
    "intake_arrived": "Машина прибыла на площадку",
    "intake_crane_started": "Кран начал работу",
    "intake_crane_ended": "Кран завершил работу",
    "intake_confirmed": "Приёмка подтверждена",
    "intake_discrepancy": "Приёмка подтверждена с расхождением",
    "intake_draft_saved": "Сохранён черновик приёмки",
    "intake_cancelled": "Отменена подтверждённая приёмка",
    "block_loaded_to_wagon": "Блок загружен в вагон",
    "wagon_loaded": "Загрузка вагона зафиксирована",
    "wagon_dispatched": "Вагон отправлен",
    "wagon_in_transit": "Вагон в пути",
    "wagon_arrived_kodar": "Вагон прибыл в Кодар",
    "wagon_unloaded_bts_east": "Вагон выгружен у БТС Восток",
    "wagon_returned_empty": "Вагон вернулся порожним",
    "operation_corrected": "Создана корректировка",
    "operation_cancelled": "Отменена подтверждённая операция",
    "daily_report": "Ежедневный отчёт",
}


def _journal_datetime(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return str(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo("Asia/Irkutsk")).strftime("%d.%m.%Y %H:%M")


def _journal_details(payload: Any) -> str:
    if not isinstance(payload, dict):
        return str(payload) if payload else ""
    keys = ("wagon_number", "block", "block_id", "external_reference", "ttn_number",
            "vehicle_plate", "blocks_count", "expected_blocks_count", "reason", "status")
    parts = []
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            parts.append(f"{key}={payload[key]}")
    return "; ".join(parts)


async def handle_events_export(request: web.Request) -> web.Response:
    operator, denied = _operator(request)
    if denied:
        return denied
    try:
        from openpyxl import Workbook
    except Exception:
        return _json({"error": "Экспорт в Excel не настроен на сервере"}, 503)
    events = store.list_events(limit=1000)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Журнал"
    sheet.append(["Время (Иркутск)", "Событие", "Исполнитель", "Объект", "Детали"])
    for event in events:
        sheet.append([
            _journal_datetime(event.get("occurred_at")),
            _EVENT_LABELS_RU.get(event.get("event_type"), event.get("event_type")),
            event.get("actor_name") or "",
            event.get("subject_public_id") or "",
            _journal_details(event.get("payload")),
        ])
    buffer = BytesIO()
    workbook.save(buffer)
    return web.Response(
        body=buffer.getvalue(),
        headers={"Content-Disposition": "attachment; filename*=UTF-8''taksimo-journal.xlsx"},
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


async def handle_report(request: web.Request) -> web.Response:
    try:
        return _json(store.report_summary(date_value=request.query.get("date")))
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)


# --- Read-only справочники и картина площадки из старой Таксимо ---
# Подтягиваются напрямую из старой SQLite (mode=ro) на каждый запрос, пока
# операторы ещё работают в старой версии. Старая база не изменяется.

async def handle_legacy_vehicles(_request: web.Request) -> web.Response:
    return _json({"vehicles": legacy.list_legacy_vehicles()})


async def handle_legacy_wagons(_request: web.Request) -> web.Response:
    return _json({"wagons": legacy.list_legacy_wagons()})


async def handle_legacy_slots(_request: web.Request) -> web.Response:
    return _json({"slots": legacy.list_legacy_wagon_slots()})


async def handle_legacy_slabs(_request: web.Request) -> web.Response:
    return _json({"slabs": legacy.list_legacy_slabs()})


async def handle_legacy_sessions(_request: web.Request) -> web.Response:
    return _json({"sessions": legacy.list_legacy_sessions()})


async def handle_legacy_wagon_history(_request: web.Request) -> web.Response:
    return _json({"history": legacy.list_legacy_wagon_history()})


_LEGACY_ZONE_TO_DEAD_END = {
    "ГРУЗОВОЙ": "gruzovoy_1",
    "ТУРАН": "gruzovoy_2",
}


async def handle_dead_ends(_request: web.Request) -> web.Response:
    """Карта тупиков новой площадки для выбора вагона тапом.

    Пока операторы ещё работают в старой Таксимо, вагоны в слотах читаются из
    старого ``wagon_slots`` (зона ГРУЗОВОЙ -> Грузовой 1, ТУРАН -> Грузовой 2
    (Туран)); новые вагоны контура без слота попадают в ``free_wagons``.
    """
    positions_by_code: dict[str, dict[int, str]] = {}
    for slot in legacy.list_legacy_wagon_slots():
        code = _LEGACY_ZONE_TO_DEAD_END.get((slot.get("zone") or "").strip().upper())
        if code is None or not isinstance(slot.get("slot_index"), int):
            continue
        positions_by_code.setdefault(code, {})[slot["slot_index"]] = (slot.get("wagon_number") or "")
    free_wagons = [wagon["wagon_number"] for wagon in store.list_wagons()]
    dead_ends = store.wagon_dead_ends()
    for dead_end in dead_ends:
        slot_map = positions_by_code.get(dead_end["code"], {})
        slot_count = int(dead_end["slots"])
        dead_end["positions"] = [
            {"slot_index": index, "wagon_number": slot_map.get(index, "")}
            for index in range(1, slot_count + 1)
        ]
    return _json({"dead_ends": dead_ends, "free_wagons": free_wagons})


async def handle_attachment_placeholder(_request: web.Request) -> web.Response:
    return _json(
        {"error": "Архив фото ещё не настроен: нужен отдельный Object Storage и закрытая выдача файлов."},
        503,
    )


async def handle_integration_documents(request: web.Request) -> web.Response:
    unavailable = _service_unavailable_response()
    if unavailable:
        return unavailable
    if not _service_authorized(request):
        return _json({"error": "Интеграция не авторизована"}, 401)
    key = (request.headers.get("Idempotency-Key") or "").strip()
    try:
        document = store.import_expected_document(await _body(request), idempotency_key=key)
        return _json({"document": document}, 201)
    except store.TaksimoNewConflictError as exc:
        return _json({"error": str(exc)}, 409)
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)


async def handle_intake_documents(request: web.Request) -> web.Response:
    operator, denied = _operator(request)
    if denied:
        return denied
    return _json({"documents": store.list_intake_documents(request.match_info["public_id"])})


async def handle_intake_document_download(request: web.Request) -> web.Response:
    operator, denied = _operator(request)
    if denied:
        return denied
    try:
        copy_number = int(request.match_info["copy_number"])
    except (TypeError, ValueError):
        return _json({"error": "Некорректный номер копии"}, 400)
    document = store.get_intake_document(request.match_info["public_id"], copy_number)
    if document is None:
        return _json({"error": "Документ не найден"}, 404)
    return web.Response(
        body=document["content"],
        headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(str(document["filename"]))},
        content_type=str(document["content_type"]),
    )
    return _json(
        {"error": "Архив фото ещё не настроен: нужен отдельный Object Storage и закрытая выдача файлов."},
        503,
    )


async def handle_integration_expected(request: web.Request) -> web.Response:
    unavailable = _service_unavailable_response()
    if unavailable:
        return unavailable
    if not _service_authorized(request):
        return _json({"error": "Интеграция не авторизована"}, 401)
    key = (request.headers.get("Idempotency-Key") or "").strip()
    try:
        intake = store.import_expected_intake(await _body(request), idempotency_key=key)
        return _json({"intake": intake}, 201)
    except store.TaksimoNewConflictError as exc:
        return _json({"error": str(exc)}, 409)
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)


async def handle_integration_outbox(request: web.Request) -> web.Response:
    unavailable = _service_unavailable_response()
    if unavailable:
        return unavailable
    if not _service_authorized(request):
        return _json({"error": "Интеграция не авторизована"}, 401)
    return _json({"events": store.list_outbox(limit=request.query.get("limit", 100))})


async def handle_integration_ack(request: web.Request) -> web.Response:
    unavailable = _service_unavailable_response()
    if unavailable:
        return unavailable
    if not _service_authorized(request):
        return _json({"error": "Интеграция не авторизована"}, 401)
    try:
        body = await _body(request)
        if not store.acknowledge_outbox(request.match_info["public_id"], receipt_reference=body.get("receipt_reference")):
            return _json({"error": "Событие не найдено или уже подтверждено"}, 404)
        return _json({"ok": True})
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)


async def handle_integration_failure(request: web.Request) -> web.Response:
    unavailable = _service_unavailable_response()
    if unavailable:
        return unavailable
    if not _service_authorized(request):
        return _json({"error": "Интеграция не авторизована"}, 401)
    try:
        body = await _body(request)
        if not store.record_outbox_delivery_failure(request.match_info["public_id"], result_note=body.get("result_note")):
            return _json({"error": "Событие не найдено или уже подтверждено"}, 404)
        return _json({"ok": True})
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)


def _with_database_errors(handler):
    async def wrapped(request: web.Request) -> web.Response:
        try:
            return await handler(request)
        except TaksimoNewDatabaseError:
            return _json({"error": "MySQL нового контура недоступен"}, 503)
    return wrapped


def register_taksimo_new_routes(app: web.Application) -> None:
    app.router.add_get("/api/taksimo-new/dashboard", _with_database_errors(handle_dashboard))
    app.router.add_get("/api/taksimo-new/intakes", _with_database_errors(handle_intakes))
    app.router.add_post("/api/taksimo-new/intakes", _with_database_errors(handle_intakes))
    app.router.add_get("/api/taksimo-new/intakes/{public_id}", _with_database_errors(handle_intake))
    app.router.add_post("/api/taksimo-new/intakes/{public_id}/claim", _with_database_errors(handle_intake_claim))
    app.router.add_post("/api/taksimo-new/intakes/{public_id}/arrival", _with_database_errors(handle_intake_arrival))
    app.router.add_post("/api/taksimo-new/intakes/{public_id}/crane-started", _with_database_errors(handle_intake_crane_started))
    app.router.add_post("/api/taksimo-new/intakes/{public_id}/crane-ended", _with_database_errors(handle_intake_crane_ended))
    app.router.add_post("/api/taksimo-new/intakes/{public_id}/confirm", _with_database_errors(handle_intake_confirm))
    app.router.add_post("/api/taksimo-new/intakes/{public_id}/draft", _with_database_errors(handle_intake_draft))
    app.router.add_post("/api/taksimo-new/intakes/{public_id}/cancel", _with_database_errors(handle_intake_cancel))
    app.router.add_post("/api/taksimo-new/corrections", _with_database_errors(handle_corrections))
    app.router.add_post("/api/taksimo-new/operations/{subject_type}/{subject_id}/cancel", _with_database_errors(handle_operation_cancel))
    app.router.add_get("/api/taksimo-new/yard", _with_database_errors(handle_yard))
    app.router.add_get("/api/taksimo-new/wagons", _with_database_errors(handle_wagons))
    app.router.add_post("/api/taksimo-new/wagons/load", _with_database_errors(handle_wagon_load))
    app.router.add_get("/api/taksimo-new/wagons/{wagon_number}/history", _with_database_errors(handle_wagon_history))
    app.router.add_post("/api/taksimo-new/wagons/{wagon_number}/loaded", _with_database_errors(handle_wagon_loaded))
    app.router.add_post("/api/taksimo-new/wagons/{wagon_number}/dispatch", _with_database_errors(handle_wagon_dispatch))
    app.router.add_post("/api/taksimo-new/wagons/{wagon_number}/arrived-kodar", _with_database_errors(handle_wagon_arrived_kodar))
    app.router.add_post("/api/taksimo-new/wagons/{wagon_number}/unloaded-bts-east", _with_database_errors(handle_wagon_unloaded_bts_east))
    app.router.add_post("/api/taksimo-new/wagons/{wagon_number}/returned-empty", _with_database_errors(handle_wagon_returned_empty))
    app.router.add_get("/api/taksimo-new/search", _with_database_errors(handle_search))
    app.router.add_get("/api/taksimo-new/events", _with_database_errors(handle_events))
    app.router.add_get("/api/taksimo-new/events/export", _with_database_errors(handle_events_export))
    app.router.add_get("/api/taksimo-new/reports/summary", _with_database_errors(handle_report))
    app.router.add_get("/api/taksimo-new/catalog/vehicles", handle_legacy_vehicles)
    app.router.add_get("/api/taksimo-new/catalog/wagons", handle_legacy_wagons)
    app.router.add_get("/api/taksimo-new/catalog/slots", handle_legacy_slots)
    app.router.add_get("/api/taksimo-new/catalog/slabs", handle_legacy_slabs)
    app.router.add_get("/api/taksimo-new/catalog/sessions", handle_legacy_sessions)
    app.router.add_get("/api/taksimo-new/catalog/wagon-history", handle_legacy_wagon_history)
    app.router.add_get("/api/taksimo-new/catalog/dead-ends", _with_database_errors(handle_dead_ends))
    app.router.add_post("/api/taksimo-new/attachments", _with_database_errors(handle_attachment_placeholder))
    app.router.add_get("/api/taksimo-new/intakes/{public_id}/documents", _with_database_errors(handle_intake_documents))
    app.router.add_get("/api/taksimo-new/intakes/{public_id}/documents/{copy_number}", _with_database_errors(handle_intake_document_download))
    app.router.add_post("/api/taksimo-new/integration/rumex/expected-documents", _with_database_errors(handle_integration_documents))
    app.router.add_post("/api/taksimo-new/integration/rumex/expected-intakes", _with_database_errors(handle_integration_expected))
    app.router.add_get("/api/taksimo-new/integration/outbox", _with_database_errors(handle_integration_outbox))
    app.router.add_post("/api/taksimo-new/integration/outbox/{public_id}/ack", _with_database_errors(handle_integration_ack))
    app.router.add_post("/api/taksimo-new/integration/outbox/{public_id}/failure", _with_database_errors(handle_integration_failure))
