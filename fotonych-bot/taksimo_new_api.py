"""HTTP API нового изолированного контура PWA Таксимо."""

from __future__ import annotations

import hmac
import os
from urllib.parse import quote

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


async def handle_search(request: web.Request) -> web.Response:
    try:
        return _json({"results": store.search(request.query.get("q"), limit=request.query.get("limit", 50))})
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)


async def handle_events(request: web.Request) -> web.Response:
    return _json({"events": store.list_events(limit=request.query.get("limit", 100))})


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
    app.router.add_post("/api/taksimo-new/wagons/{wagon_number}/loaded", _with_database_errors(handle_wagon_loaded))
    app.router.add_post("/api/taksimo-new/wagons/{wagon_number}/dispatch", _with_database_errors(handle_wagon_dispatch))
    app.router.add_post("/api/taksimo-new/wagons/{wagon_number}/arrived-kodar", _with_database_errors(handle_wagon_arrived_kodar))
    app.router.add_post("/api/taksimo-new/wagons/{wagon_number}/unloaded-bts-east", _with_database_errors(handle_wagon_unloaded_bts_east))
    app.router.add_post("/api/taksimo-new/wagons/{wagon_number}/returned-empty", _with_database_errors(handle_wagon_returned_empty))
    app.router.add_get("/api/taksimo-new/search", _with_database_errors(handle_search))
    app.router.add_get("/api/taksimo-new/events", _with_database_errors(handle_events))
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
