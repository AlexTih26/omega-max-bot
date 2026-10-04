"""Доменная модель новой, изолированной PWA Таксимо.

Все сохранённые факты относятся только к MySQL-схеме ``taksimo_new``. Здесь
нет импортов старой SQLite-Таксимо или SQLite-реестра РУМЕКС.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta, timezone
import json
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from taksimo_new_db import iso_utc, transaction, utc_now


OPERATOR_ROLES = {"operator1", "operator2", "operator3"}
MAX_LOCK_MINUTES = 20
DEFAULT_TIMEZONE = "Asia/Irkutsk"

# Тупики новой площадки — своя конфигурация, НЕ копия старой (там было два:
# ГРУЗОВОЙ и ТУРАН по 10). Слоты обязательны: это позиция вагона.
WAGON_DEAD_ENDS = (
    {"code": "gruzovoy_1", "name": "Грузовой 1", "slots": 10},
    {"code": "gruzovoy_2", "name": "Грузовой 2 (Туран)", "slots": 10},
    {"code": "gruzovoy_3", "name": "Грузовой 3", "slots": 10},
)


def wagon_dead_ends() -> list[dict[str, Any]]:
    """Конфигурация тупиков/слотов новой площадки."""
    return [dict(item) for item in WAGON_DEAD_ENDS]


class TaksimoNewConflictError(ValueError):
    """Конфликт состояния: старые данные нельзя молча заменить."""


def _public_id() -> str:
    return str(uuid4())


def _required_text(value: Any, label: str, *, max_length: int) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"Укажите {label}")
    if len(result) > max_length:
        raise ValueError(f"{label.capitalize()} слишком длинный")
    return result


def _optional_text(value: Any, label: str, *, max_length: int) -> str:
    result = str(value or "").strip()
    if len(result) > max_length:
        raise ValueError(f"{label.capitalize()} слишком длинный")
    return result


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _as_int(value: Any, label: str, *, minimum: int = 0, maximum: int = 2_147_483_647) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"Некорректно указано: {label}") from None
    if result < minimum or result > maximum:
        raise ValueError(f"Некорректно указано: {label}")
    return result


def _datetime_from_external(value: Any, label: str) -> datetime | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError(f"Некорректно указано: {label}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"Некорректно указано: {label}") from None
    if parsed.tzinfo is None:
        raise ValueError(f"Укажите {label} с часовым поясом")
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _row_datetime(row: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in row.items():
        result[key] = iso_utc(value) if isinstance(value, datetime) else value
    return result


def _operator_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "role": str(row["role_code"]),
        "name": str(row["display_name"]),
    }


def _operator_actor(operator: Mapping[str, Any]) -> tuple[str, str, str]:
    return "operator", str(int(operator["id"])), str(operator["name"])


def _append_event(
    cursor: Any,
    *,
    event_type: str,
    subject_type: str,
    subject_public_id: str | None,
    actor_kind: str,
    actor_id: str,
    actor_name: str,
    payload: Mapping[str, Any],
    occurred_at: datetime,
) -> str:
    public_id = _public_id()
    cursor.execute(
        """INSERT INTO tn_events
           (public_id, event_type, subject_type, subject_public_id, actor_kind, actor_id,
            actor_name, payload_json, occurred_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, CAST(%s AS JSON), %s)""",
        (
            public_id, event_type, subject_type, subject_public_id, actor_kind, actor_id,
            actor_name, _json(payload), occurred_at,
        ),
    )
    return public_id


def _append_outbox(
    cursor: Any,
    *,
    event_type: str,
    idempotency_key: str,
    payload: Mapping[str, Any],
    created_at: datetime,
) -> None:
    cursor.execute(
        """INSERT INTO tn_integration_outbox
           (public_id, event_type, idempotency_key, payload_json, created_at)
           VALUES (%s, %s, %s, CAST(%s AS JSON), %s)""",
        (_public_id(), event_type, idempotency_key, _json(payload), created_at),
    )


def _append_notification(
    cursor: Any,
    *,
    event_type: str,
    idempotency_key: str,
    payload: Mapping[str, Any],
    created_at: datetime,
) -> None:
    cursor.execute(
        """INSERT INTO tn_notification_outbox
           (public_id, event_type, idempotency_key, payload_json, created_at)
           VALUES (%s, %s, %s, CAST(%s AS JSON), %s)""",
        (_public_id(), event_type, idempotency_key, _json(payload), created_at),
    )


def _rumex_context(row: Mapping[str, Any]) -> dict[str, Any]:
    shipment_id = row.get("rumex_shipment_id")
    if shipment_id is None:
        return {}
    return {
        "rumex_shipment_id": int(shipment_id),
        "rumex_document_version": int(row["rumex_document_version"]),
        "physical_owner": str(row["physical_owner"]),
    }


def _intake_line_payload(line: Mapping[str, Any], *, require_location: bool) -> dict[str, Any]:
    block_type = _required_text(line.get("block_type", line.get("type")), "тип блока", max_length=30).upper()
    block_number = _required_text(line.get("block_number", line.get("number")), "номер блока", max_length=80).upper()
    product_name = _optional_text(line.get("product_name"), "наименование", max_length=200)
    weight = line.get("weight_kg")
    weight_kg = None if weight in (None, "") else _as_int(weight, "вес", minimum=1)
    receipt_state = str(line.get("receipt_state") or "received").strip().lower()
    if receipt_state not in {"received", "missing", "damaged"}:
        raise ValueError("Некорректный статус блока")
    condition_code = str(line.get("condition_code") or ("damage" if receipt_state == "damaged" else "ok")).strip().lower()
    if condition_code not in {"ok", "damage"}:
        raise ValueError("Некорректное состояние блока")
    note = _optional_text(line.get("discrepancy_note"), "примечание", max_length=500)
    if (receipt_state in {"missing", "damaged"} or condition_code == "damage") and not note:
        raise ValueError("Для недостачи или повреждения укажите примечание")
    yard_x = line.get("yard_x")
    yard_y = line.get("yard_y")
    wagon_number = _optional_text(line.get("wagon_number"), "номер вагона", max_length=40).upper() or None
    if receipt_state == "missing":
        if yard_x not in (None, "") or yard_y not in (None, "") or wagon_number:
            raise ValueError("Для недостающего блока нельзя указывать место размещения")
        parsed_x = parsed_y = None
    elif require_location:
        if wagon_number:
            if yard_x not in (None, "") or yard_y not in (None, ""):
                raise ValueError("Укажите либо вагон, либо место на площадке")
            parsed_x = parsed_y = None
        else:
            parsed_x = _as_int(yard_x, "координата X", minimum=1, maximum=13)
            parsed_y = _as_int(yard_y, "координата Y", minimum=1, maximum=25)
    else:
        if wagon_number:
            parsed_x = parsed_y = None
        else:
            parsed_x = None if yard_x in (None, "") else _as_int(yard_x, "координата X", minimum=1, maximum=13)
            parsed_y = None if yard_y in (None, "") else _as_int(yard_y, "координата Y", minimum=1, maximum=25)
    return {
        "block_type": block_type,
        "block_number": block_number,
        "product_name": product_name,
        "weight_kg": weight_kg,
        "receipt_state": receipt_state,
        "condition_code": condition_code,
        "discrepancy_note": note,
        "yard_x": parsed_x,
        "yard_y": parsed_y,
        "wagon_number": wagon_number,
    }


def _expected_line_payload(line: Mapping[str, Any]) -> dict[str, Any]:
    payload = _intake_line_payload({**line, "receipt_state": "received", "condition_code": "ok"}, require_location=False)
    return {key: payload[key] for key in ("block_type", "block_number", "product_name", "weight_kg")}


def operator_accounts_ready() -> bool:
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT role_code FROM tn_operator_accounts WHERE active = 1")
            roles = {str(row["role_code"]) for row in cursor.fetchall()}
    return roles == OPERATOR_ROLES


def set_operator_account(role: str, display_name: str, *, pin_hash: str | None, active: bool) -> None:
    if role not in OPERATOR_ROLES:
        raise ValueError("Неизвестная роль оператора")
    name = _required_text(display_name, "имя оператора", max_length=120)
    now = utc_now()
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id, pin_hash FROM tn_operator_accounts WHERE role_code = %s FOR UPDATE", (role,))
            existing = cursor.fetchone()
            if existing:
                value = pin_hash if pin_hash is not None else str(existing["pin_hash"])
                cursor.execute(
                    """UPDATE tn_operator_accounts
                       SET display_name = %s, pin_hash = %s, active = %s, updated_at = %s
                       WHERE id = %s""",
                    (name, value, int(active), now, int(existing["id"])),
                )
                if not active:
                    cursor.execute(
                        "UPDATE tn_operator_sessions SET revoked_at = %s WHERE operator_id = %s AND revoked_at IS NULL",
                        (now, int(existing["id"])),
                    )
            elif pin_hash is None:
                raise ValueError("Для новой учётной записи требуется PIN")
            else:
                cursor.execute(
                    """INSERT INTO tn_operator_accounts (role_code, display_name, pin_hash, active, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (role, name, pin_hash, int(active), now, now),
                )


def find_operator_for_pin(pin: str, verifier: Callable[[str, str], bool]) -> dict[str, Any] | None:
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, role_code, display_name, pin_hash FROM tn_operator_accounts WHERE active = 1 ORDER BY id"
            )
            for row in cursor.fetchall():
                if verifier(pin, str(row["pin_hash"])):
                    return _operator_from_row(row)
    return None


def failed_login_count(remote_address: str, *, since_seconds: int) -> int:
    since = utc_now() - timedelta(seconds=since_seconds)
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT COUNT(*) AS count FROM tn_operator_login_attempts
                   WHERE remote_address = %s AND success = 0 AND attempted_at >= %s""",
                (remote_address, since),
            )
            return int(cursor.fetchone()["count"])


def record_login_attempt(
    *, success: bool, remote_address: str, user_agent: str, operator_id: int | None = None
) -> None:
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO tn_operator_login_attempts
                   (operator_id, success, remote_address, user_agent, attempted_at)
                   VALUES (%s, %s, %s, %s, %s)""",
                (operator_id, int(success), remote_address[:128], user_agent[:500], utc_now()),
            )


def create_session(
    *, token_hash: str, operator_id: int, expires_at: float, remote_address: str, user_agent: str
) -> None:
    now = utc_now()
    expiry = datetime.fromtimestamp(expires_at, tz=timezone.utc).replace(tzinfo=None)
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO tn_operator_sessions
                   (token_hash, operator_id, created_at, expires_at, last_seen_at, remote_address, user_agent)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (token_hash, operator_id, now, expiry, now, remote_address[:128], user_agent[:500]),
            )


def session_operator(token_hash: str, *, renewal_seconds: int) -> dict[str, Any] | None:
    now = utc_now()
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT a.id, a.role_code, a.display_name
                   FROM tn_operator_sessions AS s
                   JOIN tn_operator_accounts AS a ON a.id = s.operator_id
                   WHERE s.token_hash = %s AND s.revoked_at IS NULL AND s.expires_at > %s AND a.active = 1
                   FOR UPDATE""",
                (token_hash, now),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            cursor.execute(
                "UPDATE tn_operator_sessions SET last_seen_at = %s, expires_at = %s WHERE token_hash = %s",
                (now, now + timedelta(seconds=renewal_seconds), token_hash),
            )
            return _operator_from_row(row)


def revoke_session(token_hash: str) -> None:
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE tn_operator_sessions SET revoked_at = %s WHERE token_hash = %s AND revoked_at IS NULL", (utc_now(), token_hash))


def _fetch_intake(cursor: Any, intake_id: int, *, for_update: bool = False) -> dict[str, Any] | None:
    cursor.execute(
        """SELECT i.*, creator.display_name AS created_by_name, confirmer.display_name AS confirmed_by_name,
                  canc.cancelled_at, canc.reason AS cancellation_reason
           FROM tn_intakes AS i
           LEFT JOIN tn_operator_accounts AS creator ON creator.id = i.created_by_operator_id
           LEFT JOIN tn_operator_accounts AS confirmer ON confirmer.id = i.confirmed_by_operator_id
           LEFT JOIN tn_intake_cancellations AS canc ON canc.intake_id = i.id
           WHERE i.id = %s""" + (" FOR UPDATE" if for_update else ""),
        (intake_id,),
    )
    return cursor.fetchone()


def _intake_payload(cursor: Any, row: Mapping[str, Any], *, include_lines: bool = True) -> dict[str, Any]:
    result = _row_datetime(dict(row))
    intake_id = int(row["id"])
    result["id"] = intake_id
    result["cancelled"] = row.get("cancelled_at") is not None
    if include_lines:
        cursor.execute("SELECT * FROM tn_expected_intake_lines WHERE intake_id = %s ORDER BY sort_order", (intake_id,))
        result["expected_lines"] = [_row_datetime(dict(item)) for item in cursor.fetchall()]
        cursor.execute("SELECT * FROM tn_intake_lines WHERE intake_id = %s ORDER BY sort_order", (intake_id,))
        result["lines"] = [_row_datetime(dict(item)) for item in cursor.fetchall()]
    return result


def import_expected_intake(payload: Mapping[str, Any], *, idempotency_key: str) -> dict[str, Any]:
    """Принять новый ожидаемый рейс только из доверенного одностороннего моста."""
    key = _required_text(idempotency_key, "ключ идемпотентности", max_length=160)
    contract_version = payload.get("contract_version")
    if contract_version not in (1, 2):
        raise ValueError("Неподдерживаемая версия контракта РУМЕКС")
    reference = _required_text(payload.get("external_reference"), "внешний номер рейса", max_length=160)
    rumex_shipment_id = _as_int(payload.get("rumex_shipment_id"), "идентификатор отгрузки РУМЕКС", minimum=1)
    document_version = _as_int(payload.get("document_version"), "версия документа РУМЕКС", minimum=1)
    if payload.get("physical_owner") != "taksimo_new":
        raise ValueError("Физическим владельцем рейса должна быть новая Таксимо")
    ttn_number = _optional_text(payload.get("ttn_number"), "номер ТТН", max_length=120)
    vehicle_plate = _required_text(payload.get("vehicle_plate"), "номер машины", max_length=40).upper()
    driver_name = _required_text(payload.get("driver_name"), "имя водителя", max_length=200)
    confirmation = payload.get("confirmation")
    er_confirmed_at = None
    if isinstance(confirmation, Mapping):
        er_confirmed_at = _datetime_from_external(confirmation.get("er_confirmed_at"), "время подтверждения ЭР")
        _optional_text(confirmation.get("er_external_reference"), "внешнюю ссылку подтверждённого ЭР", max_length=200)
    if contract_version == 1 and er_confirmed_at is None:
        raise ValueError("Передайте время подтверждения ЭР")
    if contract_version == 1 and not ttn_number:
        raise ValueError("Передайте номер ТТН")
    expected_raw = payload.get("expected_blocks")
    if not isinstance(expected_raw, list) or not expected_raw:
        raise ValueError("Передайте ожидаемые блоки рейса")
    expected = [_expected_line_payload(item) for item in expected_raw if isinstance(item, Mapping)]
    if len(expected) != len(expected_raw):
        raise ValueError("Некорректная строка ожидаемого груза")
    identities = {(item["block_type"], item["block_number"]) for item in expected}
    if len(identities) != len(expected):
        raise ValueError("В ожидаемом грузе повторяется блок")
    now = utc_now()
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT i.* FROM tn_integration_inbox AS inbox
                   JOIN tn_intakes AS i ON i.id = inbox.intake_id
                   WHERE inbox.source_system = 'rumex' AND inbox.idempotency_key = %s""",
                (key,),
            )
            repeated = cursor.fetchone()
            if repeated is not None:
                return _intake_payload(cursor, repeated)
            cursor.execute("SELECT id FROM tn_intakes WHERE source_system = 'rumex' AND source_reference = %s", (reference,))
            if cursor.fetchone() is not None:
                raise TaksimoNewConflictError("Рейс РУМЕКС с таким номером уже принят под другим ключом")
            cursor.execute(
                """SELECT id FROM tn_intakes
                   WHERE source_system = 'rumex' AND rumex_shipment_id = %s AND rumex_document_version = %s""",
                (rumex_shipment_id, document_version),
            )
            if cursor.fetchone() is not None:
                raise TaksimoNewConflictError("Эта версия документа РУМЕКС уже принята под другим ключом")
            public_id = _public_id()
            cursor.execute(
                """INSERT INTO tn_intakes
                   (public_id, source_system, source_reference, rumex_shipment_id, rumex_document_version,
                    physical_owner, ttn_number, vehicle_plate, driver_name, planned_arrival_at,
                    expected_blocks_count, status, created_at, updated_at)
                   VALUES (%s, 'rumex', %s, %s, %s, 'taksimo_new', %s, %s, %s, %s, %s, 'expected', %s, %s)""",
                (
                    public_id, reference, rumex_shipment_id, document_version,
                    ttn_number, vehicle_plate, driver_name,
                    _datetime_from_external(payload.get("planned_arrival_at"), "плановое время прибытия"),
                    len(expected), now, now,
                ),
            )
            intake_id = int(cursor.lastrowid)
            cursor.executemany(
                """INSERT INTO tn_expected_intake_lines
                   (intake_id, sort_order, block_type, block_number, product_name, weight_kg, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                [
                    (intake_id, number, item["block_type"], item["block_number"], item["product_name"], item["weight_kg"], now)
                    for number, item in enumerate(expected, start=1)
                ],
            )
            cursor.execute(
                """INSERT INTO tn_integration_inbox (idempotency_key, source_system, received_at, payload_json, intake_id)
                   VALUES (%s, 'rumex', %s, CAST(%s AS JSON), %s)""",
                (key, now, _json(payload), intake_id),
            )
            _append_event(
                cursor, event_type="intake_expected_imported", subject_type="intake", subject_public_id=public_id,
                actor_kind="integration", actor_id="rumex", actor_name="РУМЕКС",
                payload={
                    "external_reference": reference,
                    "rumex_shipment_id": rumex_shipment_id,
                    "rumex_document_version": document_version,
                    "physical_owner": "taksimo_new",
                    "er_confirmed_at": iso_utc(er_confirmed_at),
                    "expected_blocks_count": len(expected),
                }, occurred_at=now,
            )
            intake = _fetch_intake(cursor, intake_id)
            if intake is None:
                raise RuntimeError("Не удалось прочитать импортированный рейс")
            return _intake_payload(cursor, intake)


def import_expected_document(payload: Mapping[str, Any], *, idempotency_key: str) -> dict[str, Any]:
    """Приложить документ (копию ТТН) к рейсу РУМЕКС из доверенного моста."""
    key = _required_text(idempotency_key, "ключ идемпотентности", max_length=160)
    rumex_shipment_id = _as_int(payload.get("rumex_shipment_id"), "идентификатор отгрузки РУМЕКС", minimum=1)
    document_kind = _required_text(payload.get("document_kind"), "вид документа", max_length=16).upper()
    if document_kind not in ("TN", "ER"):
        raise ValueError("Неподдерживаемый вид документа")
    copy_number = _as_int(payload.get("copy_number"), "номер копии", minimum=1, maximum=4)
    filename = _required_text(payload.get("filename"), "имя файла", max_length=255)
    content_type = _required_text(payload.get("content_type"), "тип содержимого", max_length=128)
    content_b64 = _required_text(payload.get("content_base64"), "содержимое документа", max_length=20_000_000)
    try:
        content = base64.b64decode(content_b64, validate=True)
    except Exception as exc:
        raise ValueError("Некорректное base64-содержимое документа") from exc
    if not content:
        raise ValueError("Пустое содержимое документа")
    now = utc_now()
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, public_id FROM tn_intakes WHERE source_system = 'rumex' AND rumex_shipment_id = %s LIMIT 1",
                (rumex_shipment_id,),
            )
            intake = cursor.fetchone()
            if intake is None:
                raise TaksimoNewConflictError("Рейс РУМЕКС для документа ещё не принят")
            intake_id = int(intake["id"])
            public_id = str(intake["public_id"])
            cursor.execute("SELECT id FROM tn_intake_documents WHERE idempotency_key = %s", (key,))
            if cursor.fetchone() is not None:
                return {"public_id": public_id, "document_kind": document_kind, "copy_number": copy_number, "imported": True}
            cursor.execute(
                "SELECT id FROM tn_intake_documents WHERE intake_id = %s AND document_kind = %s AND copy_number = %s",
                (intake_id, document_kind, copy_number),
            )
            if cursor.fetchone() is not None:
                raise TaksimoNewConflictError("Копия документа уже приложена к рейсу")
            cursor.execute(
                """INSERT INTO tn_intake_documents
                   (intake_id, document_kind, copy_number, filename, content_type, content, idempotency_key, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (intake_id, document_kind, copy_number, filename, content_type, content, key, now),
            )
            return {"public_id": public_id, "document_kind": document_kind, "copy_number": copy_number, "filename": filename, "imported": True}


def list_intake_documents(public_id: str) -> list[dict[str, Any]]:
    """Метаданные вложений приёмки (без содержимого файлов)."""
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM tn_intakes WHERE public_id = %s", (public_id,))
            intake = cursor.fetchone()
            if intake is None:
                return []
            cursor.execute(
                """SELECT id, document_kind, copy_number, filename, content_type, created_at
                   FROM tn_intake_documents WHERE intake_id = %s ORDER BY document_kind, copy_number""",
                (intake["id"],),
            )
            return [_row_datetime(dict(row)) for row in cursor.fetchall()]


def get_intake_document(public_id: str, copy_number: int) -> dict[str, Any] | None:
    """Содержимое копии ТТН для скачивания оператором."""
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM tn_intakes WHERE public_id = %s", (public_id,))
            intake = cursor.fetchone()
            if intake is None:
                return None
            cursor.execute(
                """SELECT d.filename, d.content_type, d.content, i.source_reference
                   FROM tn_intake_documents AS d
                   JOIN tn_intakes AS i ON i.id = d.intake_id
                   WHERE d.intake_id = %s AND d.document_kind = 'TN' AND d.copy_number = %s LIMIT 1""",
                (intake["id"], copy_number),
            )
            row = cursor.fetchone()
            return dict(row) if row is not None else None


def create_manual_intake(payload: Mapping[str, Any], *, operator: Mapping[str, Any]) -> dict[str, Any]:
    count = _as_int(payload.get("expected_blocks_count"), "ожидаемое число блоков", minimum=1, maximum=100)
    now = utc_now()
    actor_kind, actor_id, actor_name = _operator_actor(operator)
    with transaction() as connection:
        with connection.cursor() as cursor:
            public_id = _public_id()
            cursor.execute(
                """INSERT INTO tn_intakes
                   (public_id, ttn_number, vehicle_plate, driver_name, planned_arrival_at, expected_blocks_count,
                    status, created_by_operator_id, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, 'draft', %s, %s, %s)""",
                (
                    public_id,
                    _optional_text(payload.get("ttn_number"), "номер ТТН", max_length=120),
                    _optional_text(payload.get("vehicle_plate"), "номер машины", max_length=40).upper(),
                    _optional_text(payload.get("driver_name"), "имя водителя", max_length=200),
                    _datetime_from_external(payload.get("planned_arrival_at"), "плановое время прибытия"),
                    count, int(operator["id"]), now, now,
                ),
            )
            intake_id = int(cursor.lastrowid)
            _append_event(
                cursor, event_type="intake_draft_created", subject_type="intake", subject_public_id=public_id,
                actor_kind=actor_kind, actor_id=actor_id, actor_name=actor_name,
                payload={"expected_blocks_count": count}, occurred_at=now,
            )
            row = _fetch_intake(cursor, intake_id)
            if row is None:
                raise RuntimeError("Не удалось прочитать черновик приёмки")
            return _intake_payload(cursor, row)


def claim_intake(public_id: str, *, operator: Mapping[str, Any]) -> dict[str, Any]:
    now = utc_now()
    expires = now + timedelta(minutes=MAX_LOCK_MINUTES)
    actor_kind, actor_id, actor_name = _operator_actor(operator)
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM tn_intakes WHERE public_id = %s FOR UPDATE", (public_id,))
            found = cursor.fetchone()
            if found is None:
                raise KeyError(public_id)
            intake_id = int(found["id"])
            row = _fetch_intake(cursor, intake_id, for_update=True)
            if row is None:
                raise KeyError(public_id)
            if row["cancelled_at"] is not None or str(row["status"]) in {"confirmed", "discrepancy"}:
                raise TaksimoNewConflictError("Подтверждённую или отменённую приёмку нельзя взять в работу")
            locked_by = row.get("locked_by_operator_id")
            locked_until = row.get("locked_until")
            if locked_by and int(locked_by) != int(operator["id"]) and locked_until and locked_until >= now:
                raise TaksimoNewConflictError("Эту приёмку уже редактирует другой оператор")
            cursor.execute(
                """UPDATE tn_intakes SET status = 'in_progress', locked_by_operator_id = %s,
                   locked_until = %s, updated_at = %s WHERE id = %s""",
                (int(operator["id"]), expires, now, intake_id),
            )
            _append_event(
                cursor, event_type="intake_locked", subject_type="intake", subject_public_id=public_id,
                actor_kind=actor_kind, actor_id=actor_id, actor_name=actor_name,
                payload={"lock_minutes": MAX_LOCK_MINUTES}, occurred_at=now,
            )
            refreshed = _fetch_intake(cursor, intake_id)
            if refreshed is None:
                raise RuntimeError("Не удалось прочитать блокировку приёмки")
            return _intake_payload(cursor, refreshed)


def confirm_intake(public_id: str, *, lines: Iterable[Mapping[str, Any]], operator: Mapping[str, Any]) -> dict[str, Any]:
    return _save_intake(public_id, lines=lines, operator=operator, finalize=True)


def save_intake_draft(public_id: str, *, lines: Iterable[Mapping[str, Any]], operator: Mapping[str, Any]) -> dict[str, Any]:
    """Сохранить раскладку блоков как черновик, не подтверждая приёмку."""
    return _save_intake(public_id, lines=lines, operator=operator, finalize=False)


def _save_intake(
    public_id: str,
    *,
    lines: Iterable[Mapping[str, Any]],
    operator: Mapping[str, Any],
    finalize: bool,
) -> dict[str, Any]:
    normalized = [_intake_line_payload(item, require_location=True) for item in lines]
    if not normalized:
        raise ValueError("Добавьте хотя бы один блок")
    identities = {(item["block_type"], item["block_number"]) for item in normalized}
    if len(identities) != len(normalized):
        raise ValueError("В приёмке повторяется один и тот же блок")
    now = utc_now()
    actor_kind, actor_id, actor_name = _operator_actor(operator)
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM tn_intakes WHERE public_id = %s FOR UPDATE", (public_id,))
            found = cursor.fetchone()
            if found is None:
                raise KeyError(public_id)
            intake_id = int(found["id"])
            row = _fetch_intake(cursor, intake_id, for_update=True)
            if row is None:
                raise KeyError(public_id)
            if str(row["status"]) not in {"expected", "draft", "in_progress"} or row["cancelled_at"] is not None:
                raise TaksimoNewConflictError("Приёмка уже подтверждена или отменена")
            locked_by = row.get("locked_by_operator_id")
            locked_until = row.get("locked_until")
            if locked_by and int(locked_by) != int(operator["id"]) and locked_until and locked_until >= now:
                raise TaksimoNewConflictError("Приёмку редактирует другой оператор")
            if str(row["source_system"]) == "rumex":
                required_facts = {
                    "arrived_at": "прибытие машины",
                    "crane_started_at": "начало работы крана",
                    "crane_ended_at": "окончание работы крана",
                }
                missing_facts = [label for field, label in required_facts.items() if row.get(field) is None]
                if missing_facts:
                    raise TaksimoNewConflictError(
                        "Перед приёмкой рейса РУМЕКС зафиксируйте: " + ", ".join(missing_facts)
                    )
            if str(row["source_system"]) != "rumex" and finalize and len(normalized) != int(row["expected_blocks_count"]):
                raise ValueError("Число фактических блоков должно совпадать с ожидаемым")
            expected_identities: set[tuple[str, str]] = set()
            if str(row["source_system"]) == "rumex":
                cursor.execute(
                    "SELECT block_type, block_number FROM tn_expected_intake_lines WHERE intake_id = %s",
                    (intake_id,),
                )
                expected_identities = {
                    (str(item["block_type"]), str(item["block_number"]))
                    for item in cursor.fetchall()
                }
                if finalize:
                    if not expected_identities:
                        raise TaksimoNewConflictError("Для рейса РУМЕКС не сохранен ожидаемый состав")
                    absent = expected_identities - identities
                    if absent:
                        labels = ", ".join(f"{kind} {number}" for kind, number in sorted(absent))
                        raise ValueError("Отметьте каждый ожидаемый блок, включая недостачу: " + labels)
                    for item in normalized:
                        identity = (item["block_type"], item["block_number"])
                        if identity not in expected_identities and not item["discrepancy_note"]:
                            raise ValueError("Для незаявленного блока укажите причину расхождения")
            for item in normalized:
                if item["receipt_state"] == "missing":
                    continue
                cursor.execute(
                    "SELECT id FROM tn_blocks WHERE block_type = %s AND block_number = %s FOR UPDATE",
                    (item["block_type"], item["block_number"]),
                )
                if cursor.fetchone() is not None:
                    raise TaksimoNewConflictError(f"Блок {item['block_type']} {item['block_number']} уже находится в новом контуре")
            slots_by_cell: dict[tuple[int, int], set[int]] = {}
            for item in normalized:
                if item["receipt_state"] == "missing":
                    item["yard_slot"] = None
                    continue
                if item["wagon_number"]:
                    item["yard_slot"] = None
                    continue
                cell = (int(item["yard_x"]), int(item["yard_y"]))
                if cell not in slots_by_cell:
                    cursor.execute(
                        """SELECT yard_slot FROM tn_blocks
                           WHERE current_location_kind = 'yard' AND yard_x = %s AND yard_y = %s FOR UPDATE""",
                        cell,
                    )
                    slots_by_cell[cell] = {
                        int(saved["yard_slot"])
                        for saved in cursor.fetchall()
                        if saved["yard_slot"] is not None
                    }
                free_slot = next((slot for slot in range(1, 5) if slot not in slots_by_cell[cell]), None)
                if free_slot is None:
                    raise TaksimoNewConflictError(
                        f"Ячейка X={cell[0]}, Y={cell[1]} заполнена: допускается не более 4 блоков"
                    )
                slots_by_cell[cell].add(free_slot)
                item["yard_slot"] = free_slot
            # Перезаписать предыдущую версию этой приёмки (черновик или повтор сохранения):
            # старые строки и блоки удаляются заново, чтобы ничего не задвоилось.
            cursor.execute(
                """DELETE wl FROM tn_wagon_loads AS wl
                   JOIN tn_blocks AS b ON b.id = wl.block_id
                   WHERE b.received_intake_id = %s""",
                (intake_id,),
            )
            cursor.execute("DELETE FROM tn_blocks WHERE received_intake_id = %s", (intake_id,))
            cursor.execute("DELETE FROM tn_intake_lines WHERE intake_id = %s", (intake_id,))
            cursor.executemany(
                """INSERT INTO tn_intake_lines
                   (intake_id, sort_order, block_type, block_number, product_name, weight_kg, receipt_state,
                    condition_code, discrepancy_note, yard_x, yard_y, confirmed_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                [
                    (
                        intake_id, order, item["block_type"], item["block_number"], item["product_name"], item["weight_kg"],
                        item["receipt_state"], item["condition_code"], item["discrepancy_note"], item["yard_x"], item["yard_y"], now,
                    )
                    for order, item in enumerate(normalized, start=1)
                ],
            )
            cursor.execute("SELECT id, block_type, block_number FROM tn_intake_lines WHERE intake_id = %s", (intake_id,))
            saved_lines = {(str(item["block_type"]), str(item["block_number"])): int(item["id"]) for item in cursor.fetchall()}
            wagon_ids: dict[str, int] = {}
            for item in normalized:
                if item["receipt_state"] == "missing":
                    continue
                line_id = saved_lines[(item["block_type"], item["block_number"])]
                if item["wagon_number"]:
                    wagon_number = item["wagon_number"]
                    if wagon_number not in wagon_ids:
                        cursor.execute(
                            "SELECT id, status FROM tn_wagons WHERE wagon_number = %s FOR UPDATE", (wagon_number,)
                        )
                        wagon_row = cursor.fetchone()
                        if wagon_row is None:
                            cursor.execute(
                                "INSERT INTO tn_wagons (wagon_number, status, created_at) VALUES (%s, 'forming', %s)",
                                (wagon_number, now),
                            )
                            wagon_ids[wagon_number] = int(cursor.lastrowid)
                        elif str(wagon_row["status"]) in {"forming", "returned_empty"}:
                            if str(wagon_row["status"]) == "returned_empty":
                                cursor.execute(
                                    """UPDATE tn_wagons SET status = 'forming', loaded_at = NULL,
                                       loaded_by_operator_id = NULL, dispatched_at = NULL,
                                       dispatched_by_operator_id = NULL, arrived_kodar_at = NULL,
                                       arrived_kodar_by_operator_id = NULL, unloaded_bts_east_at = NULL,
                                       unloaded_bts_east_by_operator_id = NULL, returned_empty_at = NULL,
                                       returned_empty_by_operator_id = NULL WHERE id = %s""",
                                    (int(wagon_row["id"]),),
                                )
                            wagon_ids[wagon_number] = int(wagon_row["id"])
                        else:
                            raise TaksimoNewConflictError(f"Вагон {wagon_number} нельзя догружать на текущем этапе")
                    cursor.execute(
                        """INSERT INTO tn_blocks
                           (block_type, block_number, product_name, weight_kg, condition_code, current_location_kind,
                            yard_x, yard_y, yard_slot, wagon_number, received_intake_id, received_line_id, created_at, updated_at)
                           VALUES (%s, %s, %s, %s, %s, 'wagon', NULL, NULL, NULL, %s, %s, %s, %s, %s)""",
                        (
                            item["block_type"], item["block_number"], item["product_name"], item["weight_kg"], item["condition_code"],
                            wagon_number, intake_id, line_id, now, now,
                        ),
                    )
                    block_id = int(cursor.lastrowid)
                    cursor.execute(
                        """INSERT INTO tn_wagon_loads (wagon_id, block_id, loaded_by_operator_id, loaded_at)
                           VALUES (%s, %s, %s, %s)""",
                        (wagon_ids[wagon_number], block_id, int(operator["id"]), now),
                    )
                else:
                    cursor.execute(
                        """INSERT INTO tn_blocks
                           (block_type, block_number, product_name, weight_kg, condition_code, current_location_kind,
                            yard_x, yard_y, yard_slot, received_intake_id, received_line_id, created_at, updated_at)
                           VALUES (%s, %s, %s, %s, %s, 'yard', %s, %s, %s, %s, %s, %s, %s)""",
                        (
                            item["block_type"], item["block_number"], item["product_name"], item["weight_kg"], item["condition_code"],
                            item["yard_x"], item["yard_y"], item["yard_slot"], intake_id, line_id, now, now,
                        ),
                    )
            if finalize:
                discrepancy = any(
                    item["receipt_state"] != "received"
                    or item["condition_code"] != "ok"
                    or (expected_identities and (item["block_type"], item["block_number"]) not in expected_identities)
                    for item in normalized
                )
                status = "discrepancy" if discrepancy else "confirmed"
                cursor.execute(
                    """UPDATE tn_intakes SET status = %s, confirmed_by_operator_id = %s, confirmed_at = %s,
                       locked_by_operator_id = NULL, locked_until = NULL, updated_at = %s WHERE id = %s""",
                    (status, int(operator["id"]), now, now, intake_id),
                )
                outgoing_payload = {
                    "event": "intake_confirmed" if not discrepancy else "intake_discrepancy",
                    "intake_id": public_id,
                    "source_system": str(row["source_system"]),
                    "external_reference": str(row["source_reference"] or ""),
                    "ttn_number": str(row["ttn_number"]),
                    "vehicle_plate": str(row["vehicle_plate"]),
                    "status": status,
                    "confirmed_at": iso_utc(now),
                    "operator": {"role": str(operator["role"]), "name": str(operator["name"])},
                    "blocks": normalized,
                    "expected_blocks": len(expected_identities) if expected_identities else int(row["expected_blocks_count"]),
                    **_rumex_context(row),
                }
                _append_event(
                    cursor, event_type="intake_confirmed" if not discrepancy else "intake_discrepancy",
                    subject_type="intake", subject_public_id=public_id,
                    actor_kind=actor_kind, actor_id=actor_id, actor_name=actor_name,
                    payload=outgoing_payload, occurred_at=now,
                )
                _append_notification(
                    cursor,
                    event_type="intake_confirmed" if not discrepancy else "intake_discrepancy",
                    idempotency_key=f"intake:{public_id}:notification:{status}",
                    payload=outgoing_payload,
                    created_at=now,
                )
                if _rumex_context(row):
                    _append_outbox(
                        cursor, event_type="taksimo.intake.confirmed" if not discrepancy else "taksimo.intake.discrepancy",
                        idempotency_key=f"intake:{public_id}:{status}", payload=outgoing_payload, created_at=now,
                    )
            else:
                _append_event(
                    cursor, event_type="intake_draft_saved", subject_type="intake", subject_public_id=public_id,
                    actor_kind=actor_kind, actor_id=actor_id, actor_name=actor_name,
                    payload={"draft_blocks_count": len(normalized)}, occurred_at=now,
                )
            updated = _fetch_intake(cursor, intake_id)
            if updated is None:
                raise RuntimeError("Не удалось прочитать приёмку после сохранения")
            return _intake_payload(cursor, updated)


def _record_intake_fact(
    public_id: str,
    *,
    fact: str,
    operator: Mapping[str, Any],
) -> dict[str, Any]:
    definitions = {
        "arrival": {
            "column": "arrived_at",
            "event_type": "intake_arrived",
            "outbox_type": "taksimo.intake.arrived",
            "label": "прибытие машины",
            "allowed_statuses": {"expected", "in_progress"},
            "next_status": "in_progress",
        },
        "crane_started": {
            "column": "crane_started_at",
            "event_type": "intake_crane_started",
            "outbox_type": "taksimo.intake.crane_started",
            "label": "начало работы крана",
            "allowed_statuses": {"in_progress"},
            "required_column": "arrived_at",
        },
        "crane_ended": {
            "column": "crane_ended_at",
            "event_type": "intake_crane_ended",
            "outbox_type": "taksimo.intake.crane_ended",
            "label": "окончание работы крана",
            "allowed_statuses": {"in_progress"},
            "required_column": "crane_started_at",
        },
    }
    definition = definitions[fact]
    now = utc_now()
    actor_kind, actor_id, actor_name = _operator_actor(operator)
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM tn_intakes WHERE public_id = %s FOR UPDATE", (public_id,))
            found = cursor.fetchone()
            if found is None:
                raise KeyError(public_id)
            intake_id = int(found["id"])
            row = _fetch_intake(cursor, intake_id, for_update=True)
            if row is None:
                raise KeyError(public_id)
            if row["cancelled_at"] is not None or str(row["status"]) not in definition["allowed_statuses"]:
                raise TaksimoNewConflictError(f"Нельзя зафиксировать {definition['label']} в текущем статусе приёмки")
            if row.get(definition["column"]) is not None:
                raise TaksimoNewConflictError(f"{definition['label'].capitalize()} уже зафиксировано")
            required_column = definition.get("required_column")
            if required_column and row.get(required_column) is None:
                raise TaksimoNewConflictError(f"Сначала зафиксируйте {definitions['arrival' if required_column == 'arrived_at' else 'crane_started']['label']}")
            status = definition.get("next_status")
            if status:
                cursor.execute(
                    f"UPDATE tn_intakes SET {definition['column']} = %s, status = %s, updated_at = %s WHERE id = %s",
                    (now, status, now, intake_id),
                )
            else:
                cursor.execute(
                    f"UPDATE tn_intakes SET {definition['column']} = %s, updated_at = %s WHERE id = %s",
                    (now, now, intake_id),
                )
            payload = {
                "event": definition["event_type"],
                "intake_id": public_id,
                "external_reference": str(row["source_reference"] or ""),
                "ttn_number": str(row["ttn_number"]),
                "vehicle_plate": str(row["vehicle_plate"]),
                "operator": {"role": str(operator["role"]), "name": str(operator["name"])},
                "occurred_at": iso_utc(now),
                **_rumex_context(row),
            }
            _append_event(
                cursor, event_type=definition["event_type"], subject_type="intake", subject_public_id=public_id,
                actor_kind=actor_kind, actor_id=actor_id, actor_name=actor_name, payload=payload, occurred_at=now,
            )
            _append_notification(
                cursor,
                event_type=definition["event_type"],
                idempotency_key=f"intake:{public_id}:notification:{fact}",
                payload=payload,
                created_at=now,
            )
            if _rumex_context(row):
                _append_outbox(
                    cursor, event_type=definition["outbox_type"],
                    idempotency_key=f"intake:{public_id}:{fact}", payload=payload, created_at=now,
                )
            updated = _fetch_intake(cursor, intake_id)
            if updated is None:
                raise RuntimeError("Не удалось прочитать приёмку после фиксации факта")
            return _intake_payload(cursor, updated)


def record_intake_arrival(public_id: str, *, operator: Mapping[str, Any]) -> dict[str, Any]:
    """Неизменно зафиксировать фактическое прибытие автомобиля на площадку."""
    return _record_intake_fact(public_id, fact="arrival", operator=operator)


def record_intake_crane_started(public_id: str, *, operator: Mapping[str, Any]) -> dict[str, Any]:
    """Неизменно зафиксировать начало работы крана по приёмке."""
    return _record_intake_fact(public_id, fact="crane_started", operator=operator)


def record_intake_crane_ended(public_id: str, *, operator: Mapping[str, Any]) -> dict[str, Any]:
    """Неизменно зафиксировать окончание работы крана по приёмке."""
    return _record_intake_fact(public_id, fact="crane_ended", operator=operator)


def list_intakes(*, date_value: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    parsed_limit = max(1, min(int(limit), 200))
    conditions = []
    params: list[Any] = []
    if date_value:
        zone = ZoneInfo(DEFAULT_TIMEZONE)
        try:
            day = datetime.fromisoformat(date_value).date()
        except ValueError:
            raise ValueError("Некорректная дата") from None
        start = datetime.combine(day, datetime.min.time(), tzinfo=zone).astimezone(timezone.utc).replace(tzinfo=None)
        finish = start + timedelta(days=1)
        conditions.append("i.created_at >= %s AND i.created_at < %s")
        params.extend([start, finish])
    query = """SELECT i.*, canc.cancelled_at, canc.reason AS cancellation_reason
               FROM tn_intakes AS i
               LEFT JOIN tn_intake_cancellations AS canc ON canc.intake_id = i.id"""
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY COALESCE(i.planned_arrival_at, i.created_at) DESC, i.id DESC LIMIT %s"
    params.append(parsed_limit)
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            return [_intake_payload(cursor, row) for row in cursor.fetchall()]


def get_intake(public_id: str) -> dict[str, Any] | None:
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM tn_intakes WHERE public_id = %s", (public_id,))
            found = cursor.fetchone()
            if found is None:
                return None
            row = _fetch_intake(cursor, int(found["id"]))
            return _intake_payload(cursor, row) if row is not None else None


def cancel_intake(public_id: str, *, reason: Any, operator: Mapping[str, Any]) -> dict[str, Any]:
    cancel_operation(subject_type="intake", subject_public_id=public_id, reason=reason, operator=operator)
    intake = get_intake(public_id)
    if intake is None:
        raise KeyError(public_id)
    return intake


def cancel_operation(
    *, subject_type: str, subject_public_id: str, reason: Any, operator: Mapping[str, Any]
) -> dict[str, Any]:
    if str(operator["role"]) != "operator1":
        raise PermissionError("Отмена подтверждённых операций доступна только Оператору 1")
    if subject_type not in {"intake", "wagon_load", "wagon_dispatch"}:
        raise ValueError("Некорректный вид операции")
    text = _required_text(reason, "причину отмены", max_length=500)
    now = utc_now()
    actor_kind, actor_id, actor_name = _operator_actor(operator)
    cancellation_public_id = _public_id()
    with transaction() as connection:
        with connection.cursor() as cursor:
            if subject_type == "intake":
                cursor.execute("SELECT id FROM tn_intakes WHERE public_id = %s FOR UPDATE", (subject_public_id,))
                found = cursor.fetchone()
                if found is None:
                    raise KeyError(subject_public_id)
                intake_id = int(found["id"])
                row = _fetch_intake(cursor, intake_id, for_update=True)
                if row is None:
                    raise KeyError(subject_public_id)
                if str(row["status"]) not in {"confirmed", "discrepancy"}:
                    raise TaksimoNewConflictError("Отменить можно только подтверждённую приёмку")
                if row["cancelled_at"] is not None:
                    raise TaksimoNewConflictError("Приёмка уже отменена")
                cursor.execute(
                    """INSERT INTO tn_intake_cancellations (intake_id, reason, cancelled_by_operator_id, cancelled_at)
                       VALUES (%s, %s, %s, %s)""",
                    (intake_id, text, int(operator["id"]), now),
                )
            elif subject_type == "wagon_load":
                load_id = _as_int(subject_public_id, "загрузка вагона", minimum=1)
                subject_public_id = str(load_id)
                cursor.execute("SELECT id FROM tn_wagon_loads WHERE id = %s FOR UPDATE", (load_id,))
                if cursor.fetchone() is None:
                    raise KeyError(subject_public_id)
            else:
                subject_public_id = _required_text(subject_public_id, "номер вагона", max_length=40).upper()
                cursor.execute(
                    "SELECT id FROM tn_wagons WHERE wagon_number = %s AND dispatched_at IS NOT NULL FOR UPDATE",
                    (subject_public_id,),
                )
                if cursor.fetchone() is None:
                    raise KeyError(subject_public_id)
            cursor.execute(
                """SELECT public_id FROM tn_operation_cancellations
                   WHERE subject_type = %s AND subject_public_id = %s FOR UPDATE""",
                (subject_type, subject_public_id),
            )
            if cursor.fetchone() is not None:
                raise TaksimoNewConflictError("Операция уже отменена")
            cursor.execute(
                """INSERT INTO tn_operation_cancellations
                   (public_id, subject_type, subject_public_id, reason, cancelled_by_operator_id, cancelled_at)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (cancellation_public_id, subject_type, subject_public_id, text, int(operator["id"]), now),
            )
            _append_event(
                cursor, event_type="intake_cancelled" if subject_type == "intake" else "operation_cancelled",
                subject_type=subject_type, subject_public_id=subject_public_id,
                actor_kind=actor_kind, actor_id=actor_id, actor_name=actor_name,
                payload={"cancellation_id": cancellation_public_id, "reason": text}, occurred_at=now,
            )
            _append_outbox(
                cursor, event_type=f"taksimo.{subject_type}.cancelled", idempotency_key=f"cancellation:{cancellation_public_id}",
                payload={
                    "cancellation_id": cancellation_public_id, "subject_type": subject_type,
                    "subject_id": subject_public_id, "reason": text, "cancelled_at": iso_utc(now),
                }, created_at=now,
            )
    return {
        "id": cancellation_public_id, "subject_type": subject_type, "subject_id": subject_public_id,
        "reason": text, "cancelled_at": iso_utc(now),
    }


def create_correction(
    *, subject_type: str, subject_public_id: str, reason: Any, details: Mapping[str, Any] | None, operator: Mapping[str, Any]
) -> dict[str, Any]:
    if str(operator["role"]) != "operator1":
        raise PermissionError("Корректировки подтверждённых операций доступны только Оператору 1")
    if subject_type not in {"intake", "wagon_load", "wagon_dispatch"}:
        raise ValueError("Некорректный вид операции")
    text = _required_text(reason, "причину корректировки", max_length=500)
    if details is not None and not isinstance(details, Mapping):
        raise ValueError("Некорректные данные корректировки")
    now = utc_now()
    public_id = _public_id()
    actor_kind, actor_id, actor_name = _operator_actor(operator)
    payload = {"reason": text, "details": dict(details or {})}
    with transaction() as connection:
        with connection.cursor() as cursor:
            if subject_type == "intake":
                cursor.execute("SELECT id, status FROM tn_intakes WHERE public_id = %s FOR UPDATE", (subject_public_id,))
                subject = cursor.fetchone()
                if subject is None or str(subject["status"]) not in {"confirmed", "discrepancy"}:
                    raise TaksimoNewConflictError("Корректировка возможна только для подтверждённой приёмки")
            elif subject_type == "wagon_load":
                load_id = _as_int(subject_public_id, "загрузка вагона", minimum=1)
                subject_public_id = str(load_id)
                cursor.execute("SELECT id FROM tn_wagon_loads WHERE id = %s FOR UPDATE", (load_id,))
                if cursor.fetchone() is None:
                    raise KeyError(subject_public_id)
            else:
                subject_public_id = _required_text(subject_public_id, "номер вагона", max_length=40).upper()
                cursor.execute(
                    "SELECT id FROM tn_wagons WHERE wagon_number = %s AND dispatched_at IS NOT NULL FOR UPDATE",
                    (subject_public_id,),
                )
                if cursor.fetchone() is None:
                    raise KeyError(subject_public_id)
            cursor.execute(
                """INSERT INTO tn_operation_corrections
                   (public_id, subject_type, subject_public_id, reason, correction_json, created_by_operator_id, created_at)
                   VALUES (%s, %s, %s, %s, CAST(%s AS JSON), %s, %s)""",
                (public_id, subject_type, subject_public_id, text, _json(payload), int(operator["id"]), now),
            )
            _append_event(
                cursor, event_type="operation_corrected", subject_type=subject_type,
                subject_public_id=subject_public_id, actor_kind=actor_kind, actor_id=actor_id,
                actor_name=actor_name, payload={"correction_id": public_id, **payload}, occurred_at=now,
            )
            _append_outbox(
                cursor, event_type=f"taksimo.{subject_type}.corrected", idempotency_key=f"correction:{public_id}",
                payload={
                    "correction_id": public_id, "subject_type": subject_type, "subject_id": subject_public_id,
                    **payload, "corrected_at": iso_utc(now),
                }, created_at=now,
            )
    return {"id": public_id, "subject_type": subject_type, "subject_id": subject_public_id, **payload, "created_at": iso_utc(now)}


def yard_map() -> dict[str, Any]:
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT b.id, b.block_type, b.block_number, b.product_name, b.weight_kg, b.condition_code,
                          b.yard_x, b.yard_y, b.yard_slot, b.updated_at,
                          i.status AS intake_status, i.public_id AS intake_public_id
                   FROM tn_blocks AS b
                   LEFT JOIN tn_intakes AS i ON i.id = b.received_intake_id
                   WHERE b.current_location_kind = 'yard'
                   ORDER BY b.yard_y, b.yard_x, b.block_type, b.block_number"""
            )
            blocks = []
            for row in cursor.fetchall():
                item = _row_datetime(dict(row))
                item["draft"] = row["intake_status"] not in ("confirmed", "discrepancy") if row["intake_status"] is not None else False
                blocks.append(item)
    return {"grid": {"x": 13, "y": 25, "max_blocks_per_cell": 4}, "blocks": blocks}


def list_wagons() -> list[dict[str, Any]]:
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT w.id, w.wagon_number, w.status, w.created_at, w.loaded_at, w.dispatched_at,
                          w.arrived_kodar_at, w.unloaded_bts_east_at,
                          COUNT(loads.id) AS blocks_count
                   FROM tn_wagons AS w LEFT JOIN tn_wagon_loads AS loads ON loads.wagon_id = w.id
                   GROUP BY w.id ORDER BY w.created_at DESC, w.id DESC"""
            )
            return [_row_datetime(dict(row)) for row in cursor.fetchall()]


def wagon_history(wagon_number: Any) -> dict[str, Any]:
    """История одного вагона: текущее состояние, блоки в вагоне и этапы цикла."""
    number = _required_text(wagon_number, "номер вагона", max_length=40).upper()
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM tn_wagons WHERE wagon_number = %s", (number,))
            wagon_row = cursor.fetchone()
            wagon = _row_datetime(dict(wagon_row)) if wagon_row is not None else None
            cursor.execute(
                """SELECT loads.id AS load_id, loads.loaded_at,
                          b.block_type, b.block_number, b.product_name, b.weight_kg, b.condition_code
                   FROM tn_wagon_loads AS loads
                   JOIN tn_blocks AS b ON b.id = loads.block_id
                   JOIN tn_wagons AS w ON w.id = loads.wagon_id
                   WHERE w.wagon_number = %s
                   ORDER BY loads.loaded_at, loads.id""",
                (number,),
            )
            loads = [_row_datetime(dict(row)) for row in cursor.fetchall()]
            cursor.execute(
                """SELECT e.event_type, e.occurred_at, e.payload_json, e.actor_name
                   FROM tn_events AS e
                   WHERE e.subject_public_id = %s AND e.subject_type IN ('wagon', 'wagon_dispatch')
                   ORDER BY e.occurred_at, e.id""",
                (number,),
            )
            events = [_row_datetime(dict(row)) for row in cursor.fetchall()]
    return {"wagon": wagon, "loads": loads, "events": events}


def _wagon_rumex_contexts(cursor: Any, wagon_id: int) -> list[dict[str, Any]]:
    cursor.execute(
        """SELECT DISTINCT i.rumex_shipment_id, i.rumex_document_version
           FROM tn_wagon_loads AS loads
           JOIN tn_blocks AS block ON block.id = loads.block_id
           JOIN tn_intakes AS i ON i.id = block.received_intake_id
           WHERE loads.wagon_id = %s
             AND i.source_system = 'rumex'
             AND i.physical_owner = 'taksimo_new'
             AND i.rumex_shipment_id IS NOT NULL
             AND i.rumex_document_version IS NOT NULL
           ORDER BY i.rumex_shipment_id, i.rumex_document_version""",
        (wagon_id,),
    )
    return [
        {
            "rumex_shipment_id": int(row["rumex_shipment_id"]),
            "rumex_document_version": int(row["rumex_document_version"]),
            "physical_owner": "taksimo_new",
        }
        for row in cursor.fetchall()
    ]


def _append_wagon_physical_facts(
    cursor: Any,
    *,
    wagon_id: int,
    wagon_number: str,
    stage: str,
    event_type: str,
    payload: Mapping[str, Any],
    created_at: datetime,
) -> None:
    for context in _wagon_rumex_contexts(cursor, wagon_id):
        _append_outbox(
            cursor,
            event_type=event_type,
            idempotency_key=(
                f"wagon:{wagon_number}:{stage}:rumex:{context['rumex_shipment_id']}:"
                f"v{context['rumex_document_version']}"
            ),
            payload={**payload, **context},
            created_at=created_at,
        )


def load_block_to_wagon(*, block_id: Any, wagon_number: Any, operator: Mapping[str, Any]) -> dict[str, Any]:
    parsed_block_id = _as_int(block_id, "блок", minimum=1)
    number = _required_text(wagon_number, "номер вагона", max_length=40).upper()
    now = utc_now()
    actor_kind, actor_id, actor_name = _operator_actor(operator)
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT block.*, i.rumex_shipment_id, i.rumex_document_version, i.physical_owner
                   FROM tn_blocks AS block
                   JOIN tn_intakes AS i ON i.id = block.received_intake_id
                   WHERE block.id = %s FOR UPDATE""",
                (parsed_block_id,),
            )
            block = cursor.fetchone()
            if block is None:
                raise KeyError(parsed_block_id)
            if str(block["current_location_kind"]) != "yard":
                raise TaksimoNewConflictError("Блок уже не находится на площадке")
            cursor.execute("SELECT * FROM tn_wagons WHERE wagon_number = %s FOR UPDATE", (number,))
            wagon = cursor.fetchone()
            if wagon is None:
                cursor.execute(
                    "INSERT INTO tn_wagons (wagon_number, status, created_at) VALUES (%s, 'forming', %s)",
                    (number, now),
                )
                wagon_id = int(cursor.lastrowid)
            else:
                if str(wagon["status"]) == "returned_empty":
                    # Порожний вагон снова у площадки: начинаем новый проход.
                    # Поля предыдущего прохода сбрасываются, история сохраняется в tn_events.
                    cursor.execute(
                        """UPDATE tn_wagons SET status = 'forming', loaded_at = NULL,
                           loaded_by_operator_id = NULL, dispatched_at = NULL,
                           dispatched_by_operator_id = NULL, arrived_kodar_at = NULL,
                           arrived_kodar_by_operator_id = NULL, unloaded_bts_east_at = NULL,
                           unloaded_bts_east_by_operator_id = NULL, returned_empty_at = NULL,
                           returned_empty_by_operator_id = NULL WHERE id = %s""",
                        (int(wagon["id"]),),
                    )
                    wagon_id = int(wagon["id"])
                elif str(wagon["status"]) == "forming":
                    wagon_id = int(wagon["id"])
                else:
                    raise TaksimoNewConflictError("Вагон после фиксации загрузки нельзя догружать")
            cursor.execute(
                """INSERT INTO tn_wagon_loads (wagon_id, block_id, loaded_by_operator_id, loaded_at)
                   VALUES (%s, %s, %s, %s)""",
                (wagon_id, parsed_block_id, int(operator["id"]), now),
            )
            load_id = int(cursor.lastrowid)
            cursor.execute(
                """UPDATE tn_blocks SET current_location_kind = 'wagon', yard_x = NULL, yard_y = NULL, yard_slot = NULL,
                   wagon_number = %s, updated_at = %s WHERE id = %s""",
                (number, now, parsed_block_id),
            )
            _append_event(
                cursor, event_type="block_loaded_to_wagon", subject_type="wagon_load", subject_public_id=str(load_id),
                actor_kind=actor_kind, actor_id=actor_id, actor_name=actor_name,
                payload={"wagon_number": number, "block_id": parsed_block_id, "block": f"{block['block_type']} {block['block_number']}"}, occurred_at=now,
            )
            context = _rumex_context(block)
            if context:
                _append_outbox(
                    cursor, event_type="taksimo.wagon_load.confirmed", idempotency_key=f"wagon-load:{load_id}",
                    payload={
                        "load_id": load_id, "wagon_number": number, "block_id": parsed_block_id,
                        "block": f"{block['block_type']} {block['block_number']}", "loaded_at": iso_utc(now),
                        **context,
                    }, created_at=now,
                )
            return {"id": load_id, "wagon_number": number, "block_id": parsed_block_id, "loaded_at": iso_utc(now)}


def mark_wagon_loaded(wagon_number: Any, *, operator: Mapping[str, Any]) -> dict[str, Any]:
    if str(operator["role"]) != "operator1":
        raise PermissionError("Зафиксировать загрузку вагона может только Оператор 1")
    number = _required_text(wagon_number, "номер вагона", max_length=40).upper()
    now = utc_now()
    actor_kind, actor_id, actor_name = _operator_actor(operator)
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM tn_wagons WHERE wagon_number = %s FOR UPDATE", (number,))
            wagon = cursor.fetchone()
            if wagon is None:
                raise KeyError(number)
            if str(wagon["status"]) != "forming":
                raise TaksimoNewConflictError("Загрузка вагона уже зафиксирована")
            cursor.execute("SELECT COUNT(*) AS count FROM tn_wagon_loads WHERE wagon_id = %s", (int(wagon["id"]),))
            count = int(cursor.fetchone()["count"])
            if not count:
                raise ValueError("Нельзя зафиксировать загрузку пустого вагона")
            cursor.execute(
                """UPDATE tn_wagons SET status = 'loaded', loaded_at = %s, loaded_by_operator_id = %s
                   WHERE id = %s""",
                (now, int(operator["id"]), int(wagon["id"])),
            )
            payload = {"wagon_number": number, "blocks_count": count, "loaded_at": iso_utc(now)}
            _append_event(
                cursor, event_type="wagon_loaded", subject_type="wagon", subject_public_id=number,
                actor_kind=actor_kind, actor_id=actor_id, actor_name=actor_name, payload=payload, occurred_at=now,
            )
            _append_wagon_physical_facts(
                cursor, wagon_id=int(wagon["id"]), wagon_number=number, stage="loaded",
                event_type="taksimo.wagon.loaded", payload=payload, created_at=now,
            )
            _append_notification(
                cursor, event_type="wagon_loaded", idempotency_key=f"wagon:{number}:loaded",
                payload=payload, created_at=now,
            )
    return {"wagon_number": number, "status": "loaded", "blocks_count": count, "loaded_at": iso_utc(now)}


def dispatch_wagon(wagon_number: Any, *, operator: Mapping[str, Any]) -> dict[str, Any]:
    if str(operator["role"]) != "operator1":
        raise PermissionError("Отправить вагон может только Оператор 1")
    number = _required_text(wagon_number, "номер вагона", max_length=40).upper()
    now = utc_now()
    actor_kind, actor_id, actor_name = _operator_actor(operator)
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM tn_wagons WHERE wagon_number = %s FOR UPDATE", (number,))
            wagon = cursor.fetchone()
            if wagon is None:
                raise KeyError(number)
            if str(wagon["status"]) != "loaded":
                raise TaksimoNewConflictError("Перед отправлением зафиксируйте загрузку вагона")
            cursor.execute("SELECT COUNT(*) AS count FROM tn_wagon_loads WHERE wagon_id = %s", (int(wagon["id"]),))
            count = int(cursor.fetchone()["count"])
            cursor.execute(
                """UPDATE tn_wagons SET status = 'in_transit', dispatched_at = %s, dispatched_by_operator_id = %s
                   WHERE id = %s""",
                (now, int(operator["id"]), int(wagon["id"])),
            )
            payload = {"wagon_number": number, "blocks_count": count, "dispatched_at": iso_utc(now)}
            _append_event(
                cursor, event_type="wagon_dispatched", subject_type="wagon_dispatch", subject_public_id=number,
                actor_kind=actor_kind, actor_id=actor_id, actor_name=actor_name, payload=payload, occurred_at=now,
            )
            _append_wagon_physical_facts(
                cursor, wagon_id=int(wagon["id"]), wagon_number=number, stage="in_transit",
                event_type="taksimo.wagon.in_transit", payload=payload, created_at=now,
            )
            _append_notification(
                cursor, event_type="wagon_in_transit", idempotency_key=f"wagon:{number}:in_transit",
                payload=payload, created_at=now,
            )
    return {"wagon_number": number, "status": "in_transit", "blocks_count": count, "dispatched_at": iso_utc(now)}


def _advance_wagon_lifecycle(
    wagon_number: Any,
    *,
    expected_status: str,
    next_status: str,
    timestamp_column: str,
    operator_column: str,
    event_type: str,
    outbox_type: str,
    notification_type: str,
    operator: Mapping[str, Any],
) -> dict[str, Any]:
    if str(operator["role"]) != "operator1":
        raise PermissionError("Подтвердить этап вагона может только Оператор 1")
    number = _required_text(wagon_number, "номер вагона", max_length=40).upper()
    now = utc_now()
    actor_kind, actor_id, actor_name = _operator_actor(operator)
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM tn_wagons WHERE wagon_number = %s FOR UPDATE", (number,))
            wagon = cursor.fetchone()
            if wagon is None:
                raise KeyError(number)
            if str(wagon["status"]) != expected_status:
                raise TaksimoNewConflictError("Нельзя подтвердить этот этап в текущем статусе вагона")
            cursor.execute("SELECT COUNT(*) AS count FROM tn_wagon_loads WHERE wagon_id = %s", (int(wagon["id"]),))
            count = int(cursor.fetchone()["count"])
            cursor.execute(
                f"UPDATE tn_wagons SET status = %s, {timestamp_column} = %s, {operator_column} = %s WHERE id = %s",
                (next_status, now, int(operator["id"]), int(wagon["id"])),
            )
            payload = {
                "wagon_number": number,
                "blocks_count": count,
                "status": next_status,
                "occurred_at": iso_utc(now),
            }
            _append_event(
                cursor, event_type=event_type, subject_type="wagon", subject_public_id=number,
                actor_kind=actor_kind, actor_id=actor_id, actor_name=actor_name, payload=payload, occurred_at=now,
            )
            _append_wagon_physical_facts(
                cursor, wagon_id=int(wagon["id"]), wagon_number=number, stage=next_status,
                event_type=outbox_type, payload=payload, created_at=now,
            )
            _append_notification(
                cursor, event_type=notification_type, idempotency_key=f"wagon:{number}:{next_status}",
                payload=payload, created_at=now,
            )
    return payload


def mark_wagon_arrived_kodar(wagon_number: Any, *, operator: Mapping[str, Any]) -> dict[str, Any]:
    """Зафиксировать прибытие вагона в Кодар без изменения исходных фактов."""
    return _advance_wagon_lifecycle(
        wagon_number, expected_status="in_transit", next_status="at_kodar",
        timestamp_column="arrived_kodar_at", operator_column="arrived_kodar_by_operator_id",
        event_type="wagon_arrived_kodar", outbox_type="taksimo.wagon.at_kodar",
        notification_type="wagon_at_kodar", operator=operator,
    )


def mark_wagon_unloaded_bts_east(wagon_number: Any, *, operator: Mapping[str, Any]) -> dict[str, Any]:
    """Зафиксировать выгрузку вагона у БТС Восток без перезаписи пути."""
    return _advance_wagon_lifecycle(
        wagon_number, expected_status="at_kodar", next_status="unloaded_bts_east",
        timestamp_column="unloaded_bts_east_at", operator_column="unloaded_bts_east_by_operator_id",
        event_type="wagon_unloaded_bts_east", outbox_type="taksimo.wagon.unloaded_bts_east",
        notification_type="wagon_unloaded_bts_east", operator=operator,
    )


def mark_wagon_returned_empty(wagon_number: Any, *, operator: Mapping[str, Any]) -> dict[str, Any]:
    """Зафиксировать возврат порожнего вагона на площадку, замыкая цикл."""
    return _advance_wagon_lifecycle(
        wagon_number, expected_status="unloaded_bts_east", next_status="returned_empty",
        timestamp_column="returned_empty_at", operator_column="returned_empty_by_operator_id",
        event_type="wagon_returned_empty", outbox_type="taksimo.wagon.returned_empty",
        notification_type="wagon_returned_empty", operator=operator,
    )


def search(query: Any, *, limit: int = 50) -> list[dict[str, Any]]:
    text = _required_text(query, "поисковый запрос", max_length=80).upper()
    parsed_limit = max(1, min(int(limit), 100))
    pattern = "%" + text + "%"
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT b.id, b.block_type, b.block_number, b.product_name, b.weight_kg, b.condition_code,
                          b.current_location_kind, b.yard_x, b.yard_y, b.wagon_number, b.updated_at,
                          i.public_id AS intake_public_id, i.ttn_number, i.vehicle_plate
                   FROM tn_blocks AS b JOIN tn_intakes AS i ON i.id = b.received_intake_id
                   WHERE b.block_type LIKE %s OR b.block_number LIKE %s OR b.wagon_number LIKE %s OR i.ttn_number LIKE %s
                   ORDER BY b.updated_at DESC LIMIT %s""",
                (pattern, pattern, pattern, pattern, parsed_limit),
            )
            return [_row_datetime(dict(row)) for row in cursor.fetchall()]


def list_events(*, limit: int = 100) -> list[dict[str, Any]]:
    parsed_limit = max(1, min(int(limit), 200))
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM tn_events ORDER BY occurred_at DESC, id DESC LIMIT %s", (parsed_limit,))
            events = []
            for row in cursor.fetchall():
                item = _row_datetime(dict(row))
                payload = item.pop("payload_json", "{}")
                item["payload"] = json.loads(payload) if isinstance(payload, str) else payload
                events.append(item)
            return events


def dashboard() -> dict[str, Any]:
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT status, COUNT(*) AS count FROM tn_intakes GROUP BY status")
            intake_statuses = {str(row["status"]): int(row["count"]) for row in cursor.fetchall()}
            cursor.execute("SELECT COUNT(*) AS count FROM tn_blocks WHERE current_location_kind = 'yard'")
            yard_blocks = int(cursor.fetchone()["count"])
            cursor.execute("SELECT status, COUNT(*) AS count FROM tn_wagons GROUP BY status")
            wagon_statuses = {str(row["status"]): int(row["count"]) for row in cursor.fetchall()}
            cursor.execute(
                """SELECT COUNT(*) AS count FROM tn_integration_outbox
                   WHERE delivered_at IS NULL
                     AND JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.physical_owner')) = 'taksimo_new'
                     AND JSON_EXTRACT(payload_json, '$.rumex_shipment_id') IS NOT NULL"""
            )
            pending_integrations = int(cursor.fetchone()["count"])
            cursor.execute(
                """SELECT i.*, canc.cancelled_at, canc.reason AS cancellation_reason
                   FROM tn_intakes AS i LEFT JOIN tn_intake_cancellations AS canc ON canc.intake_id = i.id
                   WHERE i.status IN ('expected', 'in_progress') AND canc.cancelled_at IS NULL
                   ORDER BY COALESCE(i.planned_arrival_at, i.created_at), i.id LIMIT 10"""
            )
            expected = [_intake_payload(cursor, row) for row in cursor.fetchall()]
    return {
        "timezone": DEFAULT_TIMEZONE,
        "intakes": intake_statuses,
        "yard_blocks": yard_blocks,
        "wagons": wagon_statuses,
        "pending_integrations": pending_integrations,
        "expected_intakes": expected,
    }


def report_summary(*, date_value: str | None = None) -> dict[str, Any]:
    intakes = list_intakes(date_value=date_value, limit=200)
    confirmed = [item for item in intakes if item["status"] in {"confirmed", "discrepancy"} and not item["cancelled"]]
    received = sum(1 for intake in confirmed for line in intake["lines"] if line["receipt_state"] != "missing")
    missing = sum(1 for intake in confirmed for line in intake["lines"] if line["receipt_state"] == "missing")
    damaged = sum(1 for intake in confirmed for line in intake["lines"] if line["condition_code"] == "damage")
    return {
        "date": date_value or "all",
        "confirmed_intakes": len(confirmed),
        "received_blocks": received,
        "missing_blocks": missing,
        "damaged_blocks": damaged,
        "intakes": intakes,
    }


def _report_interval(date_value: Any) -> tuple[str, datetime, datetime]:
    try:
        day = datetime.fromisoformat(_required_text(date_value, "дату отчёта", max_length=32)).date()
    except ValueError:
        raise ValueError("Некорректная дата") from None
    zone = ZoneInfo(DEFAULT_TIMEZONE)
    start = datetime.combine(day, datetime.min.time(), tzinfo=zone).astimezone(timezone.utc).replace(tzinfo=None)
    return day.isoformat(), start, start + timedelta(days=1)


def _daily_report_payload(
    cursor: Any,
    *,
    date_value: str,
    start: datetime,
    finish: datetime,
    generated_at: datetime,
) -> dict[str, Any]:
    cursor.execute(
        """SELECT COUNT(DISTINCT i.id) AS confirmed_intakes,
                  COALESCE(SUM(line.receipt_state = 'received'), 0) AS received_blocks,
                  COALESCE(SUM(line.receipt_state = 'missing'), 0) AS missing_blocks,
                  COALESCE(SUM(line.condition_code = 'damage'), 0) AS damaged_blocks
           FROM tn_intakes AS i
           LEFT JOIN tn_intake_lines AS line ON line.intake_id = i.id
           LEFT JOIN tn_intake_cancellations AS cancellation ON cancellation.intake_id = i.id
           WHERE i.status IN ('confirmed', 'discrepancy')
             AND cancellation.id IS NULL
             AND i.confirmed_at >= %s AND i.confirmed_at < %s""",
        (start, finish),
    )
    intake_stats = cursor.fetchone() or {}
    cursor.execute(
        """SELECT w.wagon_number, w.status, w.created_at, w.loaded_at, w.dispatched_at,
                  w.arrived_kodar_at, w.unloaded_bts_east_at, COUNT(loads.id) AS blocks_count
           FROM tn_wagons AS w
           LEFT JOIN tn_wagon_loads AS loads ON loads.wagon_id = w.id
           WHERE (w.loaded_at >= %s AND w.loaded_at < %s)
              OR (w.dispatched_at >= %s AND w.dispatched_at < %s)
              OR (w.arrived_kodar_at >= %s AND w.arrived_kodar_at < %s)
              OR (w.unloaded_bts_east_at >= %s AND w.unloaded_bts_east_at < %s)
           GROUP BY w.id
           ORDER BY COALESCE(w.unloaded_bts_east_at, w.arrived_kodar_at, w.dispatched_at, w.loaded_at), w.id""",
        (start, finish, start, finish, start, finish, start, finish),
    )
    wagons = [_row_datetime(dict(row)) for row in cursor.fetchall()]
    return {
        "date": date_value,
        "generated_at": iso_utc(generated_at),
        "timezone": DEFAULT_TIMEZONE,
        "intakes": {
            "confirmed": int(intake_stats.get("confirmed_intakes") or 0),
            "received_blocks": int(intake_stats.get("received_blocks") or 0),
            "missing_blocks": int(intake_stats.get("missing_blocks") or 0),
            "damaged_blocks": int(intake_stats.get("damaged_blocks") or 0),
        },
        "wagons": wagons,
    }


def enqueue_daily_report(date_value: Any) -> bool:
    """Поставить одну сводку новой Таксимо за смену в отдельную очередь MAX."""
    day, start, finish = _report_interval(date_value)
    key = f"daily-report:{day}"
    now = utc_now()
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM tn_notification_outbox WHERE idempotency_key = %s", (key,))
            if cursor.fetchone() is not None:
                return False
            _append_notification(
                cursor,
                event_type="daily_report",
                idempotency_key=key,
                payload=_daily_report_payload(
                    cursor, date_value=day, start=start, finish=finish, generated_at=now
                ),
                created_at=now,
            )
            return True


def list_pending_notifications(*, limit: int = 100) -> list[dict[str, Any]]:
    """Вернуть недоставленные сообщения нового контура без legacy-состояния."""
    parsed_limit = max(1, min(int(limit), 200))
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT * FROM tn_notification_outbox WHERE delivered_at IS NULL
                   ORDER BY created_at, id LIMIT %s""",
                (parsed_limit,),
            )
            result = []
            for row in cursor.fetchall():
                item = _row_datetime(dict(row))
                payload = item.pop("payload_json", "{}")
                item["payload"] = json.loads(payload) if isinstance(payload, str) else payload
                result.append(item)
            return result


def record_notification_delivery(
    public_id: Any,
    *,
    success: bool,
    result_note: Any = "",
    receipt_reference: Any = "",
) -> bool:
    """Записать попытку MAX и подтвердить сообщение только после успеха."""
    identifier = _required_text(public_id, "идентификатор уведомления", max_length=80)
    note = _optional_text(result_note, "результат попытки", max_length=500)
    reference = _optional_text(receipt_reference, "ссылку доставки", max_length=200)
    now = utc_now()
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM tn_notification_outbox WHERE public_id = %s AND delivered_at IS NULL FOR UPDATE",
                (identifier,),
            )
            row = cursor.fetchone()
            if row is None:
                return False
            cursor.execute(
                """INSERT INTO tn_notification_attempts (notification_id, success, result_note, attempted_at)
                   VALUES (%s, %s, %s, %s)""",
                (int(row["id"]), int(bool(success)), note, now),
            )
            if success:
                cursor.execute(
                    """UPDATE tn_notification_outbox SET delivered_at = %s, receipt_reference = %s
                       WHERE id = %s AND delivered_at IS NULL""",
                    (now, reference, int(row["id"])),
                )
                return cursor.rowcount == 1
            return True


def list_outbox(*, limit: int = 100) -> list[dict[str, Any]]:
    parsed_limit = max(1, min(int(limit), 200))
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT * FROM tn_integration_outbox WHERE delivered_at IS NULL
                   AND JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.physical_owner')) = 'taksimo_new'
                   AND JSON_EXTRACT(payload_json, '$.rumex_shipment_id') IS NOT NULL
                   ORDER BY created_at, id LIMIT %s""", (parsed_limit,)
            )
            result = []
            for row in cursor.fetchall():
                item = _row_datetime(dict(row))
                payload = item.pop("payload_json", "{}")
                item["payload"] = json.loads(payload) if isinstance(payload, str) else payload
                result.append(item)
            return result


def acknowledge_outbox(public_id: str, *, receipt_reference: Any) -> bool:
    reference = _optional_text(receipt_reference, "внешнюю ссылку", max_length=200)
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM tn_integration_outbox WHERE public_id = %s AND delivered_at IS NULL FOR UPDATE",
                (public_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return False
            cursor.execute(
                """UPDATE tn_integration_outbox SET delivered_at = %s, receipt_reference = %s
                   WHERE public_id = %s AND delivered_at IS NULL""",
                (utc_now(), reference, public_id),
            )
            if cursor.rowcount != 1:
                return False
            cursor.execute(
                """INSERT INTO tn_integration_outbox_attempts (outbox_id, success, result_note, attempted_at)
                   VALUES (%s, 1, %s, %s)""",
                (int(row["id"]), reference, utc_now()),
            )
            return True


def record_outbox_delivery_failure(public_id: str, *, result_note: Any) -> bool:
    """Сохранить неудачную попытку доставки факта без его удаления из очереди."""
    note = _required_text(result_note, "результат попытки", max_length=500)
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM tn_integration_outbox WHERE public_id = %s AND delivered_at IS NULL FOR UPDATE",
                (public_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return False
            cursor.execute(
                """INSERT INTO tn_integration_outbox_attempts (outbox_id, success, result_note, attempted_at)
                   VALUES (%s, 0, %s, %s)""",
                (int(row["id"]), note, utc_now()),
            )
            return True
