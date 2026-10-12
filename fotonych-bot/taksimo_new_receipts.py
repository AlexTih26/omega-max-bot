"""Экспедиторская расписка (ЭР) и операторская приёмка на площадке.

Документ бухгалтера: бухгалтер готовит ЭР по рейсу с выданной ТТН, отмечает
отправку в Диадок/Контур вручную и возврат, когда расписка вернулась.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from taksimo_new_db import transaction, utc_now
from taksimo_new_store import (
    ACCOUNTANT_ROLES,
    ADMIN_ROLES,
    _append_action_log,
    _as_int,
    _optional_text,
    _public_id,
    _require_role,
    _required_text,
    _row_datetime,
)


class TaksimoNewConflictError(ValueError):
    """Конфликт состояния: документ уже в другом статусе."""


def _fetch_receipt(cursor: Any, receipt_id: int, *, for_update: bool = False) -> Mapping[str, Any] | None:
    suffix = " FOR UPDATE" if for_update else ""
    cursor.execute(
        f"SELECT * FROM tn_expedition_receipts WHERE id = %s{suffix}",
        (receipt_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    cursor.execute("SELECT public_id FROM tn_trips WHERE id = %s", (row["trip_id"],))
    trip = cursor.fetchone()
    result = dict(row)
    result["trip_public_id"] = str(trip["public_id"]) if trip else None
    return result


def _price(value: Any) -> float:
    if value is None:
        return 0.0
    return round(float(value), 2)


def _receipt_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    result = _row_datetime(dict(row))
    result["id"] = int(row["id"])
    result["total_weight_kg"] = int(row["total_weight_kg"] or 0)
    result["total_price_rub"] = _price(row.get("total_price_rub"))
    return result


def _trip_blocks(cursor: Any, trip_id: int) -> list[dict[str, Any]]:
    cursor.execute(
        """SELECT block_letter, block_number, weight_kg, price_rub
           FROM tn_trip_blocks WHERE trip_id = %s ORDER BY id""",
        (trip_id,),
    )
    return [dict(row) for row in cursor.fetchall()]


def create_receipt(trip_public_id: str, *, operator: Mapping[str, Any], note: str = "") -> dict[str, Any]:
    _require_role(operator, ACCOUNTANT_ROLES | ADMIN_ROLES, "бухгалтер или администратор")
    trip_key = _required_text(trip_public_id, "рейс", max_length=36)
    receipt_note = _optional_text(note, "примечание", max_length=500)
    now = utc_now()
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM tn_trips WHERE public_id = %s FOR UPDATE", (trip_key,))
            trip = cursor.fetchone()
            if trip is None:
                raise ValueError("Рейс не найден")
            if str(trip["status"]) in ("draft", "cancelled"):
                raise ValueError("Для рейса не выдан номер ТТН")
            cursor.execute("SELECT ttn_number FROM tn_ttn_numbers WHERE trip_id = %s", (trip["id"],))
            ttn = cursor.fetchone()
            if ttn is None:
                raise ValueError("Для рейса не выдан номер ТТН")
            cursor.execute("SELECT id FROM tn_expedition_receipts WHERE trip_id = %s", (trip["id"],))
            existing = cursor.fetchone()
            if existing is not None:
                row = _fetch_receipt(cursor, int(existing["id"]))
                return _receipt_payload(row)
            blocks = _trip_blocks(cursor, int(trip["id"]))
            blocks_summary = ", ".join(
                f"{block['block_letter']} {block['block_number']}" for block in blocks
            )
            total_weight_kg = sum(int(block["weight_kg"] or 0) for block in blocks)
            total_price_rub = round(sum(Decimal(block["price_rub"] or 0) for block in blocks), 2)
            public_id = _public_id()
            cursor.execute(
                """INSERT INTO tn_expedition_receipts
                   (public_id, trip_id, ttn_number, driver_name, vehicle_plate, carrier_name,
                    carrier_inn, blocks_summary, total_weight_kg, total_price_rub, status, note,
                    created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'draft', %s, %s, %s)""",
                (
                    public_id, trip["id"], str(ttn["ttn_number"]), str(trip["driver_name"]),
                    str(trip["vehicle_plate"]), str(trip["carrier_name"]), str(trip["carrier_inn"]),
                    blocks_summary, total_weight_kg, total_price_rub, receipt_note, now, now,
                ),
            )
            receipt_id = int(cursor.lastrowid)
            _append_action_log(
                cursor, operator=operator, action="receipt_created",
                subject_type="expedition_receipt", subject_id=public_id,
                details={"trip_public_id": trip_key},
            )
            row = _fetch_receipt(cursor, receipt_id)
            return _receipt_payload(row)


def get_receipt(public_id: str) -> dict[str, Any]:
    key = _required_text(public_id, "расписка", max_length=36)
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM tn_expedition_receipts WHERE public_id = %s", (key,))
            found = cursor.fetchone()
            if found is None:
                raise KeyError(public_id)
            row = _fetch_receipt(cursor, int(found["id"]))
            return _receipt_payload(row)


def list_receipts(*, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    count = _as_int(limit, "предел", minimum=1, maximum=1000)
    where = ""
    parameters: list[Any] = []
    if status:
        where = "WHERE r.status = %s"
        parameters.append(_required_text(status, "статус", max_length=24))
    parameters.append(count)
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT r.*, t.public_id AS trip_public_id
                   FROM tn_expedition_receipts AS r
                   LEFT JOIN tn_trips AS t ON t.id = r.trip_id
                   """ + where + """
                   ORDER BY r.id DESC
                   LIMIT %s""",
                tuple(parameters),
            )
            return [_receipt_payload(dict(row)) for row in cursor.fetchall()]


def mark_receipt_sent(
    public_id: str, *, operator: Mapping[str, Any], external_reference: str = ""
) -> dict[str, Any]:
    _require_role(operator, ACCOUNTANT_ROLES | ADMIN_ROLES, "бухгалтер или администратор")
    key = _required_text(public_id, "расписка", max_length=36)
    reference = _optional_text(external_reference, "внешнюю ссылку", max_length=200)
    now = utc_now()
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM tn_expedition_receipts WHERE public_id = %s FOR UPDATE", (key,))
            found = cursor.fetchone()
            if found is None:
                raise KeyError(public_id)
            receipt_id = int(found["id"])
            row = _fetch_receipt(cursor, receipt_id, for_update=True)
            if row["status"] == "sent":
                raise TaksimoNewConflictError("Расписка уже отправлена")
            if str(row["status"]) != "draft":
                raise TaksimoNewConflictError("Отправить можно только расписку в статусе «черновик»")
            cursor.execute(
                """UPDATE tn_expedition_receipts
                   SET status = 'sent', sent_at = %s, sent_by_account_id = %s,
                       external_reference = %s, updated_at = %s
                   WHERE id = %s""",
                (now, int(operator["id"]), reference, now, receipt_id),
            )
            _append_action_log(
                cursor, operator=operator, action="receipt_sent",
                subject_type="expedition_receipt", subject_id=public_id,
                details={"external_reference": reference},
            )
            row = _fetch_receipt(cursor, receipt_id)
            return _receipt_payload(row)


def mark_receipt_returned(
    public_id: str, *, operator: Mapping[str, Any], note: str = ""
) -> dict[str, Any]:
    _require_role(operator, ACCOUNTANT_ROLES | ADMIN_ROLES, "бухгалтер или администратор")
    key = _required_text(public_id, "расписка", max_length=36)
    receipt_note = _optional_text(note, "примечание", max_length=500)
    now = utc_now()
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM tn_expedition_receipts WHERE public_id = %s FOR UPDATE", (key,))
            found = cursor.fetchone()
            if found is None:
                raise KeyError(public_id)
            receipt_id = int(found["id"])
            row = _fetch_receipt(cursor, receipt_id, for_update=True)
            if str(row["status"]) == "draft":
                raise ValueError("Расписку сначала нужно отправить")
            if str(row["status"]) != "sent":
                raise TaksimoNewConflictError("Вернуть можно только отправленную расписку")
            cursor.execute(
                """UPDATE tn_expedition_receipts
                   SET status = 'returned', returned_at = %s, returned_by_account_id = %s,
                       note = %s, updated_at = %s
                   WHERE id = %s""",
                (now, int(operator["id"]), receipt_note, now, receipt_id),
            )
            _append_action_log(
                cursor, operator=operator, action="receipt_returned",
                subject_type="expedition_receipt", subject_id=public_id,
                details={"note": receipt_note},
            )
            row = _fetch_receipt(cursor, receipt_id)
            return _receipt_payload(row)


def receipt_blocks_text(public_id: str, *, operator: Mapping[str, Any] | None = None) -> str:
    if operator is not None:
        _require_role(operator, ACCOUNTANT_ROLES | ADMIN_ROLES, "бухгалтер")
    key = _required_text(public_id, "расписка", max_length=36)
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT trip_id FROM tn_expedition_receipts WHERE public_id = %s", (key,))
            found = cursor.fetchone()
            if found is None:
                raise KeyError(public_id)
            blocks = _trip_blocks(cursor, int(found["trip_id"]))
    return "\n".join(
        f"{block['block_letter']} {block['block_number']} — "
        f"{int(block['weight_kg'])} кг — {float(block['price_rub']):.2f} ₽"
        for block in blocks
    )
