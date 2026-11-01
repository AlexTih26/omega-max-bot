"""Экспорт Склад Мастер в Excel.

Сводка строится по неизменяемому журналу движений ``sm_stock_ops``: приход,
расход и перемещения разворачиваются в один лист, остаток считается как сумма
приращений. Цена показывается там, где снабженец её проставил; пустая ячейка
означает, что цена ещё не введена, а не нулевую стоимость.
"""

from __future__ import annotations

import io
import sqlite3
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sklad_master_store import list_sites
import sklad_master_store as _store

SHEET_SUMMARY = "Сводка"
SHEET_RECEIPTS = "Приходы"
SHEET_MOVEMENTS = "Перемещения"
SHEET_HISTORY = "История"

OP_LABELS = {
    "receipt": "Приход",
    "issue": "Расход",
    "transfer_out": "Перемещение (откуда)",
    "transfer_in": "Перемещение (куда)",
    "inventory_adjustment": "Корректировка",
    "reversal": "Отмена операции",
}


def _tz() -> ZoneInfo:
    return ZoneInfo("Europe/Moscow")


def _moment(stamp: Any) -> str:
    """Дата и время операции в формате Excel, часовой пояс склада."""
    try:
        return datetime.fromtimestamp(float(stamp), tz=_tz()).strftime("%d.%m.%Y %H:%M")
    except (TypeError, ValueError):
        return ""


def _num(value: Any) -> float:
    try:
        return round(float(value or 0), 3)
    except (TypeError, ValueError):
        return 0.0


def _connect() -> sqlite3.Connection:
    # Путь к базе читается у модуля-владельца, а не копируется при импорте:
    # иначе подмена DB_PATH (тесты, песочница) не повлияет на выгрузку.
    conn = sqlite3.connect(_store.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _site_names() -> dict[int, str]:
    return {int(site["id"]): str(site["name"]) for site in list_sites(include_inactive=True)}


def _header(sheet, titles: list[str]) -> None:
    sheet.append(titles)
    for cell in sheet[sheet.max_row]:
        cell.font = _bold()


def _bold():
    from openpyxl.styles import Font

    return Font(bold=True)


def _autosize(sheet, widths: list[int]) -> None:
    from openpyxl.utils import get_column_letter

    for index, width in enumerate(widths, 1):
        sheet.column_dimensions[get_column_letter(index)].width = width


def _summary_rows(conn: sqlite3.Connection) -> list[dict]:
    """Приход, расход, перемещения и остаток по каждому материалу."""
    rows = conn.execute(
        """
        SELECT m.id, m.name, m.unit,
               COALESCE(SUM(CASE WHEN o.op_type='receipt' THEN o.quantity_delta END), 0) receipt_qty,
               COALESCE(SUM(CASE WHEN o.op_type='issue' THEN -o.quantity_delta END), 0) issue_qty,
               COALESCE(SUM(CASE WHEN o.op_type IN ('transfer_out','transfer_in')
                                 THEN o.quantity_delta END), 0) transfer_qty,
               COALESCE(SUM(CASE WHEN o.op_type IN ('inventory_adjustment','reversal')
                                 THEN o.quantity_delta END), 0) other_qty,
               COALESCE(SUM(o.quantity_delta), 0) balance,
               COALESCE(SUM(CASE WHEN o.op_type='receipt' THEN o.quantity END), 0) receipt_qty_abs
        FROM sm_materials m
        LEFT JOIN sm_stock_ops o ON o.material_id = m.id
        GROUP BY m.id
        HAVING receipt_qty_abs <> 0 OR balance <> 0 OR issue_qty <> 0
        ORDER BY m.name
        """
    ).fetchall()
    prices = {
        int(row["material_id"]): _num(row["price"])
        for row in conn.execute(
            """
            SELECT material_id, MAX(unit_price) price FROM sm_receipt_lines
            WHERE COALESCE(unit_price, 0) > 0 GROUP BY material_id
            """
        )
    }
    result = []
    for row in rows:
        material_id = int(row["id"])
        unit = str(row["unit"] or "")
        result.append(
            {
                "name": str(row["name"]),
                "receipt": _num(row["receipt_qty"]),
                "issue": _num(row["issue_qty"]),
                "transfer": _num(row["transfer_qty"]),
                "other": _num(row["other_qty"]),
                "balance": _num(row["balance"]),
                "unit": unit,
                "price": prices.get(material_id),
            }
        )
    return result


def build_summary_workbook(*, site_id: int | None = None) -> bytes:
    """Сводный отчёт: итоги прихода и расхода по материалам."""
    from openpyxl import Workbook

    conn = _connect()
    try:
        materials = _summary_rows(conn)
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = SHEET_SUMMARY
        sheet.append(["Склад Мастер · сводка по журналу движений"])
        sheet.append([f"Сформировано: {datetime.now(_tz()).strftime('%d.%m.%Y %H:%M')} (МСК)"])
        sheet.append([])
        _header(
            sheet,
            ["Материал", "Приход", "Расход", "Перемещения", "Корректировки",
             "Остаток", "Ед.", "Цена", "Сумма закупки"],
        )
        total_receipt = total_issue = total_amount = 0.0
        priced = 0
        for item in materials:
            amount = ""
            if item["price"] is not None and item["receipt"]:
                amount = round(item["receipt"] * item["price"], 2)
                total_amount += amount
                priced += 1
            sheet.append([
                item["name"], item["receipt"], item["issue"], item["transfer"],
                item["other"], item["balance"], item["unit"], item["price"] or "",
                amount,
            ])
            total_receipt += item["receipt"]
            total_issue += item["issue"]
        sheet.append([])
        sheet.append([
            "ИТОГО", round(total_receipt, 3), round(total_issue, 3), "", "", "", "", "",
            round(total_amount, 2),
        ])
        for cell in sheet[sheet.max_row]:
            cell.font = _bold()
        sheet.append([])
        sheet.append([
            f"Сумма закупки рассчитана по {priced} позициям из {len(materials)}: "
            "цена взята из приходов, где снабженец её проставил. "
            "Пустая ячейка цены означает, что цена ещё не введена, а не нулевую стоимость."
        ])
        _autosize(sheet, [42, 12, 12, 14, 15, 12, 8, 12, 14])
        buffer = io.BytesIO()
        workbook.save(buffer)
        return buffer.getvalue()
    finally:
        conn.close()


def build_movements_workbook(*, limit: int = 2000) -> bytes:
    """Журнал операций склада с площадкой отправления и назначения."""
    from openpyxl import Workbook

    sites = _site_names()
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT o.id, o.created_at, o.op_type, o.site_id, o.material_id,
                   o.quantity, o.quantity_delta, o.actor_name, o.actor_max_id,
                   o.note, o.transfer_id, o.related_op_id,
                   m.name material_name, m.unit material_unit
            FROM sm_stock_ops o JOIN sm_materials m ON m.id = o.material_id
            ORDER BY o.created_at DESC, o.id DESC LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        partner_site: dict[int, int] = {}
        for row in rows:
            if row["related_op_id"]:
                partner = conn.execute(
                    "SELECT site_id FROM sm_stock_ops WHERE id=?", (int(row["related_op_id"]),)
                ).fetchone()
                if partner:
                    partner_site[int(row["id"])] = int(partner["site_id"])
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = SHEET_HISTORY
        _header(
            sheet,
            ["Дата и время", "Операция", "Площадка", "Куда", "Материал",
             "Количество", "Ед.", "Изм. остатка", "Оформил", "Примечание"],
        )
        for row in rows:
            movement_id = int(row["id"])
            site_id = int(row["site_id"])
            target = ""
            if row["op_type"] == "transfer_out":
                partner = partner_site.get(movement_id)
                target = sites.get(partner, "") if partner else ""
            sheet.append([
                _moment(row["created_at"]),
                OP_LABELS.get(str(row["op_type"]), str(row["op_type"])),
                sites.get(site_id, ""),
                target,
                str(row["material_name"] or ""),
                _num(row["quantity"]),
                str(row["material_unit"] or ""),
                _num(row["quantity_delta"]),
                str(row["actor_name"] or ""),
                str(row["note"] or ""),
            ])
        _autosize(sheet, [18, 22, 14, 14, 34, 12, 7, 12, 18, 30])

        receipts = workbook.create_sheet(SHEET_RECEIPTS)
        receipt_rows = conn.execute(
            """
            SELECT r.id, r.created_at, r.actor_name, r.note, r.payment_status,
                   r.total_amount, s.name site_name, sup.name supplier_name,
                   rl.quantity, rl.quantity_unit, rl.billing_quantity, rl.billing_unit,
                   rl.unit_price, rl.line_total, m.name material_name
            FROM sm_receipts r
            JOIN sm_sites s ON s.id = r.site_id
            JOIN sm_suppliers sup ON sup.id = r.supplier_id
            LEFT JOIN sm_receipt_lines rl ON rl.receipt_id = r.id
            LEFT JOIN sm_materials m ON m.id = rl.material_id
            WHERE r.cancelled_at IS NULL
            ORDER BY r.created_at DESC, r.id DESC
            """
        ).fetchall()
        _header(
            receipts,
            ["Дата", "Приход №", "Площадка", "Поставщик", "Материал",
             "Количество", "Ед.", "Цена", "Сумма", "Принял", "Оплата", "Примечание"],
        )
        for row in receipt_rows:
            receipts.append([
                _moment(row["created_at"]),
                int(row["id"]),
                str(row["site_name"] or ""),
                str(row["supplier_name"] or ""),
                str(row["material_name"] or ""),
                _num(row["quantity"]),
                str(row["quantity_unit"] or ""),
                _num(row["unit_price"]) or "",
                _num(row["line_total"]) or "",
                str(row["actor_name"] or ""),
                str(row["payment_status"] or ""),
                str(row["note"] or ""),
            ])
        _autosize(receipts, [18, 10, 14, 24, 34, 12, 7, 12, 14, 18, 12, 26])

        transfers = workbook.create_sheet(SHEET_MOVEMENTS)
        _header(transfers, ["Дата и время", "Откуда", "Куда", "Материал",
                            "Количество", "Ед.", "Оформил", "Примечание"])
        for row in rows:
            if row["op_type"] != "transfer_out":
                continue
            partner = partner_site.get(int(row["id"]))
            transfers.append([
                _moment(row["created_at"]),
                sites.get(int(row["site_id"]), ""),
                sites.get(partner, "") if partner else "",
                str(row["material_name"] or ""),
                _num(row["quantity"]),
                str(row["material_unit"] or ""),
                str(row["actor_name"] or ""),
                str(row["note"] or ""),
            ])
        _autosize(transfers, [18, 14, 14, 34, 12, 7, 18, 30])
        buffer = io.BytesIO()
        workbook.save(buffer)
        return buffer.getvalue()
    finally:
        conn.close()
