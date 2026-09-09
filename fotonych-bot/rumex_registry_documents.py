"""Техническое формирование документов тестовой погрузки РУМЕКС.

Этот модуль не подменяет юридически утверждённые бланки. Сейчас он умеет
создавать только тестовую ТТН на основе загруженного технического XLSX-шаблона.
Экспедиторская расписка остаётся внешним документом Контура, пока не появится
утверждённая форма ЭР.
"""

from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime
from html import escape
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from zoneinfo import ZoneInfo

REGISTRY_DOCUMENTS_DIR = Path(__file__).resolve().parent.parent / "docs" / "registry"
TEST_TN_TEMPLATE_PATH = REGISTRY_DOCUMENTS_DIR / "ТТН — шаблон автозаполнения.xlsx"
TEST_DOCUMENT_EXPORTS_DIR = REGISTRY_DOCUMENTS_DIR / "exports"


def _timezone() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv("RUMEX_REGISTRY_TIMEZONE", "Asia/Irkutsk"))
    except Exception:
        return ZoneInfo("Asia/Irkutsk")


def _format_timestamp(value: float | int | None, *, with_time: bool = True) -> str:
    if not value:
        return ""
    pattern = "%d.%m.%Y %H:%M" if with_time else "%d.%m.%Y"
    return datetime.fromtimestamp(float(value), _timezone()).strftime(pattern)


def _safe_filename_part(value: str, *, fallback: str) -> str:
    text = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", (value or "").strip())
    text = re.sub(r"\s+", " ", text).strip(" .")
    return (text or fallback)[:160]


def test_ttn_filename(shipment: dict, *, issued_at: float) -> str:
    snapshot = shipment.get("document_snapshot") or {}
    driver = (snapshot.get("driver") or {}).get("full_name") or "Водитель не указан"
    return (
        "ТТН №"
        + _safe_filename_part(str(shipment.get("registry_number") or ""), fallback="без номера")
        + " — "
        + _safe_filename_part(str(driver), fallback="Водитель не указан")
        + " — "
        + _format_timestamp(issued_at, with_time=False)
        + ".xlsx"
    )


def _inline_cell(reference: str, value: str, *, style: int = 4) -> str:
    text = escape(value, quote=False)
    preserve = ' xml:space="preserve"' if value[:1].isspace() or value[-1:].isspace() else ""
    return f'<c r="{reference}" s="{style}" t="inlineStr"><is><t{preserve}>{text}</t></is></c>'


def _row(index: int, cells: list[str], *, height: int | None = None) -> str:
    height_attrs = f' ht="{height}" customHeight="1"' if height else ""
    return f'<row r="{index}"{height_attrs}>' + "".join(cells) + "</row>"


def _test_ttn_sheet_xml(shipment: dict, *, issued_at: float) -> bytes:
    snapshot = shipment.get("document_snapshot") or {}
    profile = snapshot.get("document_profile") or {}
    carrier = snapshot.get("carrier") or {}
    vehicle = snapshot.get("vehicle") or {}
    driver = snapshot.get("driver") or {}
    items = shipment.get("items") or []
    registry_number = str(shipment.get("registry_number") or "")
    loaded_at = _format_timestamp(shipment.get("loaded_at"))
    total_weight = sum(int(item.get("weight_kg") or 0) for item in items)
    vehicle_label = " · ".join(
        part for part in (str(vehicle.get("full_plate") or ""), str(vehicle.get("model") or "")) if part
    )
    special_notes = "Тестовая погрузка. Техническая форма: требует утверждения бухгалтерией."

    rows = [
        _row(1, [_inline_cell("A1", "ТОВАРНО-ТРАНСПОРТНАЯ НАКЛАДНАЯ", style=1)], height=28),
        _row(
            2,
            [_inline_cell("A2", "Тестовая техническая форма — не юридически утверждённый бланк.", style=2)],
            height=30,
        ),
        _row(4, [
            _inline_cell("A4", "Номер ТТН", style=3),
            _inline_cell("B4", f"ТТН №{registry_number}"),
            _inline_cell("D4", "Дата выпуска", style=3),
            _inline_cell("E4", _format_timestamp(issued_at, with_time=False)),
        ], height=28),
        _row(5, [
            _inline_cell("A5", "Грузоотправитель", style=3),
            _inline_cell("B5", str(profile.get("sender_name") or "")),
            _inline_cell("D5", "ИНН / КПП", style=3),
            _inline_cell("E5", " / ".join(filter(None, [str(profile.get("sender_inn") or ""), str(profile.get("sender_kpp") or "")]))),
        ], height=28),
        _row(6, [
            _inline_cell("A6", "Адрес грузоотправителя", style=3),
            _inline_cell("B6", str(profile.get("sender_legal_address") or "")),
        ], height=28),
        _row(7, [
            _inline_cell("A7", "Грузополучатель", style=3),
            _inline_cell("B7", str(profile.get("recipient_name") or "")),
            _inline_cell("D7", "ИНН / КПП", style=3),
            _inline_cell("E7", " / ".join(filter(None, [str(profile.get("recipient_inn") or ""), str(profile.get("recipient_kpp") or "")]))),
        ], height=28),
        _row(8, [
            _inline_cell("A8", "Адрес выгрузки", style=3),
            _inline_cell("B8", str(profile.get("delivery_location") or "")),
        ], height=28),
        _row(9, [
            _inline_cell("A9", "Перевозчик", style=3),
            _inline_cell("B9", str(carrier.get("name") or "")),
            _inline_cell("D9", "ИНН / КПП", style=3),
            _inline_cell("E9", " / ".join(filter(None, [str(carrier.get("inn") or ""), str(carrier.get("kpp") or "")]))),
        ], height=28),
        _row(10, [
            _inline_cell("A10", "Адрес перевозчика", style=3),
            _inline_cell("B10", str(carrier.get("legal_address") or "")),
        ], height=28),
        _row(11, [
            _inline_cell("A11", "Автомобиль", style=3),
            _inline_cell("B11", vehicle_label),
            _inline_cell("D11", "Хвост машины", style=3),
            _inline_cell("E11", str(vehicle.get("plate_tail") or "")),
        ], height=28),
        _row(12, [
            _inline_cell("A12", "Водитель", style=3),
            _inline_cell("B12", str(driver.get("full_name") or "")),
            _inline_cell("D12", "Проверено для документов", style=3),
            _inline_cell("E12", _format_timestamp(snapshot.get("document_binding_checked_at"))),
        ], height=28),
        _row(13, [
            _inline_cell("A13", "Адрес погрузки", style=3),
            _inline_cell("B13", str(profile.get("pickup_location") or "")),
            _inline_cell("D13", "Фактическая погрузка", style=3),
            _inline_cell("E13", loaded_at),
        ], height=28),
        _row(14, [
            _inline_cell("A14", "Особые отметки", style=3),
            _inline_cell("B14", special_notes),
        ], height=34),
        _row(15, [_inline_cell("A15", "СВЕДЕНИЯ О ГРУЗЕ", style=5)]),
        _row(16, [
            _inline_cell("A16", "№", style=5),
            _inline_cell("B16", "Наименование груза", style=5),
            _inline_cell("C16", "Кол-во мест", style=5),
            _inline_cell("D16", "Масса, кг", style=5),
            _inline_cell("E16", "Тип блока", style=5),
            _inline_cell("F16", "Индивидуальный номер", style=5),
        ], height=34),
    ]
    row_number = 17
    for index, item in enumerate(items, start=1):
        code = str(item.get("block_type_code") or "")
        block_number = str(item.get("block_number") or "")
        rows.append(
            _row(row_number, [
                _inline_cell(f"A{row_number}", str(index), style=6),
                _inline_cell(f"B{row_number}", str(item.get("product_name") or "")),
                _inline_cell(f"C{row_number}", "1"),
                _inline_cell(f"D{row_number}", str(item.get("weight_kg") or "")),
                _inline_cell(f"E{row_number}", code),
                _inline_cell(f"F{row_number}", block_number),
            ], height=26)
        )
        row_number += 1
    rows.append(
        _row(row_number, [
            _inline_cell(f"A{row_number}", "Итого", style=5),
            _inline_cell(f"C{row_number}", str(len(items)), style=5),
            _inline_cell(f"D{row_number}", f"{total_weight} кг", style=5),
        ], height=28)
    )
    row_number += 1
    rows.append(
        _row(row_number, [
            _inline_cell(f"A{row_number}", "Груз сдал: ____________________", style=7),
            _inline_cell(f"D{row_number}", f"Водитель принял: {driver.get('full_name') or ''}", style=7),
        ], height=42)
    )
    row_number += 1
    rows.append(
        _row(row_number, [
            _inline_cell(f"A{row_number}", "Техническая ТТН сформирована для тестовой погрузки РУМЕКС.", style=7),
        ], height=32)
    )

    merged = [
        "A1:F1", "A2:F2", "B4:C4", "E4:F4", "B5:C5", "E5:F5", "B6:F6",
        "B7:C7", "E7:F7", "B8:F8", "B9:C9", "E9:F9", "B10:F10", "B11:C11",
        "E11:F11", "B12:C12", "E12:F12", "B13:C13", "E13:F13", "B14:F14", "A15:F15",
        f"A{row_number - 1}:C{row_number - 1}", f"D{row_number - 1}:F{row_number - 1}",
        f"A{row_number}:F{row_number}",
    ]
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetViews><sheetView workbookViewId="0"/></sheetViews>'
        '<sheetFormatPr defaultRowHeight="15"/>'
        '<cols><col min="1" max="1" width="10" customWidth="1"/>'
        '<col min="2" max="2" width="36" customWidth="1"/>'
        '<col min="3" max="3" width="15" customWidth="1"/>'
        '<col min="4" max="4" width="18" customWidth="1"/>'
        '<col min="5" max="5" width="18" customWidth="1"/>'
        '<col min="6" max="6" width="26" customWidth="1"/></cols>'
        '<sheetData>' + "".join(rows) + '</sheetData>'
        '<mergeCells count="' + str(len(merged)) + '">' + "".join(
            f'<mergeCell ref="{reference}"/>' for reference in merged
        ) + '</mergeCells>'
        '<pageMargins left="0.3" right="0.3" top="0.5" bottom="0.5" header="0.2" footer="0.2"/>'
        '<pageSetup orientation="landscape" fitToWidth="1" fitToHeight="0"/>'
        '</worksheet>'
    )
    return xml.encode("utf-8")


def build_test_ttn_workbook(shipment: dict, *, issued_at: float) -> bytes:
    """Собрать XLSX из технического шаблона, сохранив его стили и структуру."""
    if not TEST_TN_TEMPLATE_PATH.is_file():
        raise ValueError("Технический шаблон ТТН не загружен")
    sheet_xml = _test_ttn_sheet_xml(shipment, issued_at=issued_at)
    with ZipFile(TEST_TN_TEMPLATE_PATH, "r") as source:
        if "xl/worksheets/sheet1.xml" not in source.namelist():
            raise ValueError("Технический шаблон ТТН имеет неверную структуру")
        with tempfile.SpooledTemporaryFile(max_size=2 * 1024 * 1024) as output:
            with ZipFile(output, "w", compression=ZIP_DEFLATED) as result:
                for info in source.infolist():
                    content = sheet_xml if info.filename == "xl/worksheets/sheet1.xml" else source.read(info.filename)
                    result.writestr(info, content)
            output.seek(0)
            return output.read()


def write_test_ttn_workbook(shipment: dict, *, issued_at: float) -> Path:
    """Сохранить сформированную техническую ТТН атомарно в изолированной папке."""
    content = build_test_ttn_workbook(shipment, issued_at=issued_at)
    filename = test_ttn_filename(shipment, issued_at=issued_at)
    TEST_DOCUMENT_EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    destination = TEST_DOCUMENT_EXPORTS_DIR / filename
    with tempfile.NamedTemporaryFile(dir=TEST_DOCUMENT_EXPORTS_DIR, delete=False) as temporary:
        temporary.write(content)
        temp_path = Path(temporary.name)
    try:
        temp_path.replace(destination)
    finally:
        temp_path.unlink(missing_ok=True)
    return destination
