"""Доменная модель новой, изолированной PWA Таксимо.

Все сохранённые факты относятся только к MySQL-схеме ``taksimo_new``. Здесь
нет импортов старой SQLite-Таксимо или SQLite-реестра РУМЕКС.
"""

from __future__ import annotations

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
    if receipt_state == "missing":
        if yard_x not in (None, "") or yard_y not in (None, ""):
            raise ValueError("Для недостающего блока нельзя указывать место на площадке")
        parsed_x = parsed_y = None
    elif require_location:
        parsed_x = _as_int(yard_x, "координата X", minimum=1, maximum=13)
        parsed_y = _as_int(yard_y, "координата Y", minimum=1, maximum=25)
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
    reference = _required_text(payload.get("external_reference"), "внешний номер рейса", max_length=160)
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
            public_id = _public_id()
            cursor.execute(
                """INSERT INTO tn_intakes
                   (public_id, source_system, source_reference, ttn_number, vehicle_plate, driver_name,
                    planned_arrival_at, expected_blocks_count, status, created_at, updated_at)
                   VALUES (%s, 'rumex', %s, %s, %s, %s, %s, %s, 'expected', %s, %s)""",
                (
                    public_id, reference,
                    _optional_text(payload.get("ttn_number"), "номер ТТН", max_length=120),
                    _optional_text(payload.get("vehicle_plate"), "номер машины", max_length=40).upper(),
                    _optional_text(payload.get("driver_name"), "имя водителя", max_length=200),
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
                payload={"external_reference": reference, "expected_blocks_count": len(expected)}, occurred_at=now,
            )
            intake = _fetch_intake(cursor, intake_id)
            if intake is None:
                raise RuntimeError("Не удалось прочитать импортированный рейс")
            return _intake_payload(cursor, intake)


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
            if str(row["source_system"]) != "rumex" and len(normalized) != int(row["expected_blocks_count"]):
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
            for item in normalized:
                if item["receipt_state"] == "missing":
                    continue
                cursor.execute(
                    """INSERT INTO tn_blocks
                       (block_type, block_number, product_name, weight_kg, condition_code, current_location_kind,
                        yard_x, yard_y, yard_slot, received_intake_id, received_line_id, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, %s, 'yard', %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        item["block_type"], item["block_number"], item["product_name"], item["weight_kg"], item["condition_code"],
                        item["yard_x"], item["yard_y"], item["yard_slot"], intake_id,
                        saved_lines[(item["block_type"], item["block_number"])], now, now,
                    ),
                )
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
            }
            _append_event(
                cursor, event_type="intake_confirmed" if not discrepancy else "intake_discrepancy",
                subject_type="intake", subject_public_id=public_id,
                actor_kind=actor_kind, actor_id=actor_id, actor_name=actor_name,
                payload=outgoing_payload, occurred_at=now,
            )
            _append_outbox(
                cursor, event_type="taksimo.intake.confirmed" if not discrepancy else "taksimo.intake.discrepancy",
                idempotency_key=f"intake:{public_id}:{status}", payload=outgoing_payload, created_at=now,
            )
            updated = _fetch_intake(cursor, intake_id)
            if updated is None:
                raise RuntimeError("Не удалось прочитать подтверждённую приёмку")
            return _intake_payload(cursor, updated)


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
                    "SELECT id FROM tn_wagons WHERE wagon_number = %s AND status = 'dispatched' FOR UPDATE",
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
                cursor.execute("SELECT id FROM tn_wagon_loads WHERE id = %s FOR UPDATE", (subject_public_id,))
                if cursor.fetchone() is None:
                    raise KeyError(subject_public_id)
            else:
                cursor.execute("SELECT id FROM tn_wagons WHERE wagon_number = %s AND status = 'dispatched' FOR UPDATE", (subject_public_id,))
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
                """SELECT id, block_type, block_number, product_name, weight_kg, condition_code, yard_x, yard_y, yard_slot,
                          updated_at FROM tn_blocks WHERE current_location_kind = 'yard' ORDER BY yard_y, yard_x, block_type, block_number"""
            )
            blocks = [_row_datetime(dict(row)) for row in cursor.fetchall()]
    return {"grid": {"x": 13, "y": 25, "max_blocks_per_cell": 4}, "blocks": blocks}


def list_wagons() -> list[dict[str, Any]]:
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT w.id, w.wagon_number, w.status, w.created_at, w.dispatched_at,
                          COUNT(loads.id) AS blocks_count
                   FROM tn_wagons AS w LEFT JOIN tn_wagon_loads AS loads ON loads.wagon_id = w.id
                   GROUP BY w.id ORDER BY w.created_at DESC, w.id DESC"""
            )
            return [_row_datetime(dict(row)) for row in cursor.fetchall()]


def load_block_to_wagon(*, block_id: Any, wagon_number: Any, operator: Mapping[str, Any]) -> dict[str, Any]:
    parsed_block_id = _as_int(block_id, "блок", minimum=1)
    number = _required_text(wagon_number, "номер вагона", max_length=40).upper()
    now = utc_now()
    actor_kind, actor_id, actor_name = _operator_actor(operator)
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM tn_blocks WHERE id = %s FOR UPDATE", (parsed_block_id,))
            block = cursor.fetchone()
            if block is None:
                raise KeyError(parsed_block_id)
            if str(block["current_location_kind"]) != "yard":
                raise TaksimoNewConflictError("Блок уже не находится на площадке")
            cursor.execute("SELECT * FROM tn_wagons WHERE wagon_number = %s FOR UPDATE", (number,))
            wagon = cursor.fetchone()
            if wagon is None:
                cursor.execute(
                    "INSERT INTO tn_wagons (wagon_number, status, created_at) VALUES (%s, 'loading', %s)",
                    (number, now),
                )
                wagon_id = int(cursor.lastrowid)
            else:
                if str(wagon["status"]) != "loading":
                    raise TaksimoNewConflictError("Отправленный вагон нельзя догружать")
                wagon_id = int(wagon["id"])
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
            _append_outbox(
                cursor, event_type="taksimo.wagon_load.confirmed", idempotency_key=f"wagon-load:{load_id}",
                payload={
                    "load_id": load_id, "wagon_number": number, "block_id": parsed_block_id,
                    "block": f"{block['block_type']} {block['block_number']}", "loaded_at": iso_utc(now),
                }, created_at=now,
            )
            return {"id": load_id, "wagon_number": number, "block_id": parsed_block_id, "loaded_at": iso_utc(now)}


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
            if str(wagon["status"]) != "loading":
                raise TaksimoNewConflictError("Вагон уже отправлен")
            cursor.execute("SELECT COUNT(*) AS count FROM tn_wagon_loads WHERE wagon_id = %s", (int(wagon["id"]),))
            count = int(cursor.fetchone()["count"])
            if not count:
                raise ValueError("Нельзя отправить пустой вагон")
            cursor.execute(
                """UPDATE tn_wagons SET status = 'dispatched', dispatched_at = %s, dispatched_by_operator_id = %s
                   WHERE id = %s""",
                (now, int(operator["id"]), int(wagon["id"])),
            )
            payload = {"wagon_number": number, "blocks_count": count, "dispatched_at": iso_utc(now)}
            _append_event(
                cursor, event_type="wagon_dispatched", subject_type="wagon_dispatch", subject_public_id=number,
                actor_kind=actor_kind, actor_id=actor_id, actor_name=actor_name, payload=payload, occurred_at=now,
            )
            _append_outbox(
                cursor, event_type="taksimo.wagon.dispatched", idempotency_key=f"wagon:{number}:dispatched",
                payload=payload, created_at=now,
            )
    return {"wagon_number": number, "status": "dispatched", "blocks_count": count, "dispatched_at": iso_utc(now)}


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
            cursor.execute("SELECT COUNT(*) AS count FROM tn_integration_outbox WHERE delivered_at IS NULL")
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


def list_outbox(*, limit: int = 100) -> list[dict[str, Any]]:
    parsed_limit = max(1, min(int(limit), 200))
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT * FROM tn_integration_outbox WHERE delivered_at IS NULL
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
                """UPDATE tn_integration_outbox SET delivered_at = %s, receipt_reference = %s
                   WHERE public_id = %s AND delivered_at IS NULL""",
                (utc_now(), reference, public_id),
            )
            return cursor.rowcount == 1
