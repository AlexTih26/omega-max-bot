"""Формирование четырёх экземпляров утверждённой ТТН РУМЕКС.

Источник формы неизменяем: для каждого экземпляра открывается его отдельная
копия, в которую подставляются только согласованные документные данные.
"""

from __future__ import annotations

import os
import re
import tempfile
from copy import copy
from hashlib import sha256
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.styles import PatternFill
from zoneinfo import ZoneInfo

APPROVED_TTN_TEMPLATE_PATH = (
    Path(__file__).resolve().parent / "rumex_templates" / "ТТН РУМЕКС — утверждённый шаблон.xlsx"
)
TEST_DOCUMENT_EXPORTS_DIR = Path(__file__).resolve().parent.parent / "docs" / "registry" / "exports"
TEST_TTN_ARCHIVE_DIR = Path(__file__).resolve().parent.parent / "docs" / "registry" / "ttn-archive"

OMEGA_DETAILS = (
    "ООО «Омега-М», ИНН 5406829253, КПП 502401001, "
    "143405, Московская область, г. Красногорск, ул. Почтовая, д. 3"
)
RUMEX_DETAILS = (
    "ООО «РУМЕКС», ИНН 9728126848, КПП 201001001, "
    "364030, Чеченская Республика, г.о. город Грозный, "
    "г. Грозный, р-н Байсангуровский, ул. Сайханова, двлд. 222"
)
CARRIER_DETAILS = (
    "ООО «Комсомольская ТК», ИНН 2721252270, "
    "664025, Иркутская область, г. Иркутск, ул. Сурикова, д. 6, офис 2"
)
CARRIER_DETAILS_SECOND_PAGE = (
    "ООО «Комсомольская ТК», ИНН 2721252270\n"
    "664025, Иркутская область, г. Иркутск, ул. Сурикова, д. 6, офис 2"
)
OMEGA_DETAILS_SECOND_PAGE = (
    "ООО «Омега-М»\n"
    "ИНН 5406829253, КПП 502401001\n"
    "143405, Московская область, г. Красногорск, ул. Почтовая, д. 3"
)
PICKUP_LOCATION = (
    "Завод по производству тоннельной обделки на восточном\n"
    "портале Северомуйского тоннеля пгт. Северомуйск,\n"
    "расположенный Республика Бурятия, Муйский р-н,\n"
    "ГП «Северомуйское», пгт. Северомуйск"
)
DELIVERY_LOCATION = "ж/д станция Таксимо (код станции ЕСР 90440)"
PRODUCT_NAME = "БЛОК 9,7/8,8"
COPY_NUMBERS = (1, 2, 3, 4)


def _timezone() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv("RUMEX_REGISTRY_TIMEZONE", "Asia/Irkutsk"))
    except Exception:
        return ZoneInfo("Asia/Irkutsk")


def _safe_filename_part(value: str, *, fallback: str) -> str:
    text = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", (value or "").strip())
    text = re.sub(r"\s+", " ", text).strip(" .")
    return (text or fallback)[:160]


def _loaded_datetime(shipment: dict) -> datetime:
    try:
        timestamp = float(shipment["loaded_at"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("У ТТН не указана фактическая дата погрузки") from None
    if timestamp <= 0:
        raise ValueError("У ТТН не указана фактическая дата погрузки")
    return datetime.fromtimestamp(timestamp, _timezone())


def _copy_number(value: Any) -> int:
    try:
        copy_number = int(value)
    except (TypeError, ValueError):
        raise ValueError("Укажите экземпляр ТТН от 1 до 4") from None
    if copy_number not in COPY_NUMBERS:
        raise ValueError("Укажите экземпляр ТТН от 1 до 4")
    return copy_number


def _approved_ttn_number(shipment: dict) -> str:
    number = str(shipment.get("ttn_number") or "").strip()
    if not re.fullmatch(r"ТТН №РМ-\d{4}-\d{6}", number):
        raise ValueError("Для погрузки ещё не выдан утверждённый номер ТТН")
    return number


def _required_snapshot_text(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"В снимке документа не указано: {label}")
    return text


def _surname_with_initials(full_name: str) -> str:
    """Подготовить ФИО для печатной формы, сохранив полное имя в реестре."""
    text = " ".join(str(full_name or "").split())
    parts = text.split(" ")
    if len(parts) < 2:
        return text

    surname, rest = parts[0], " ".join(parts[1:])
    existing_initials = re.findall(r"[A-Za-zА-Яа-яЁё]\.", rest)
    if existing_initials and re.fullmatch(r"[A-Za-zА-Яа-яЁё.\s]+", rest):
        return surname + " " + "".join(existing_initials)

    initials = "".join(part[0].upper() + "." for part in parts[1:] if part)
    return surname + " " + initials if initials else surname


def _make_yellow_cells_white(sheet) -> None:
    """Убрать служебную жёлтую подсветку из выдаваемого экземпляра ТТН."""
    for row in sheet.iter_rows():
        for cell in row:
            fill = cell.fill
            color = fill.fgColor
            rgb = str(color.rgb or "").upper() if color.type == "rgb" else ""
            if fill.fill_type == "solid" and rgb.endswith("FFFF00"):
                cell.fill = PatternFill(fill_type="solid", fgColor="FFFFFFFF", bgColor="FFFFFFFF")


def _total_weight(items: list[dict]) -> int:
    if len(items) not in {3, 4, 5, 6}:
        raise ValueError("Утверждённая ТТН формируется только для погрузки от 3 до 6 блоков")
    try:
        return sum(int(item["weight_kg"]) for item in items)
    except (KeyError, TypeError, ValueError):
        raise ValueError("В ТТН отсутствует утверждённый вес одного из блоков") from None


def _clear_cells(sheet, *references: str) -> None:
    for reference in references:
        sheet[reference] = None


def test_ttn_filename(shipment: dict, *, copy_number: int) -> str:
    """Имя отдельного файла одного из четырёх обязательных экземпляров."""
    number = _approved_ttn_number(shipment)
    copy_value = _copy_number(copy_number)
    return (
        _safe_filename_part(number, fallback="ТТН")
        + f" — Экземпляр № {copy_value}.xlsx"
    )


def build_test_ttn_workbook(shipment: dict, *, copy_number: int) -> bytes:
    """Сформировать один отдельный экземпляр утверждённого бланка ТТН.

    Макет, объединения, стили, юридический текст, формулы и настройки печати
    остаются из утверждённого исходного файла. Исходник никогда не изменяется.
    """
    if not APPROVED_TTN_TEMPLATE_PATH.is_file():
        raise ValueError("Утверждённый шаблон ТТН не загружен")
    number = _approved_ttn_number(shipment)
    copy_value = _copy_number(copy_number)
    loaded_at = _loaded_datetime(shipment)
    items = list(shipment.get("items") or [])
    total_weight_kg = _total_weight(items)
    snapshot = shipment.get("document_snapshot") or {}
    vehicle = snapshot.get("vehicle") or {}
    driver = snapshot.get("driver") or {}
    driver_name = _surname_with_initials(
        _required_snapshot_text(driver.get("full_name"), "ФИО водителя")
    )
    driver_license = _required_snapshot_text(driver.get("license_number"), "водительское удостоверение")
    vehicle_model = _required_snapshot_text(vehicle.get("model"), "модель автомобиля")
    vehicle_plate = _required_snapshot_text(vehicle.get("full_plate"), "государственный номер автомобиля")

    workbook = load_workbook(APPROVED_TTN_TEMPLATE_PATH, data_only=False)
    if "ТТН" not in workbook.sheetnames:
        raise ValueError("Утверждённый шаблон ТТН имеет неверную структуру")
    sheet = workbook["ТТН"]
    _make_yellow_cells_white(sheet)
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 2
    sheet.page_setup.scale = None

    # Шапка и стороны перевозки.
    sheet["G5"] = loaded_at.date()
    sheet["AC5"] = number
    sheet["W6"] = str(copy_value)
    sheet["W8"] = "✓"
    sheet["A9"] = OMEGA_DETAILS
    sheet["BO9"] = RUMEX_DETAILS
    _clear_cells(sheet, "A11", "BO11")
    sheet["A14"] = OMEGA_DETAILS
    sheet["A16"] = DELIVERY_LOCATION

    # Печатаем только фактические блоки. Неиспользованные строки утверждённого
    # бланка очищаем и скрываем, чтобы они не создавали третью страницу.
    for row_number, item in zip(range(20, 27), items, strict=False):
        code = _required_snapshot_text(item.get("block_type_code"), "тип блока")
        block_number = _required_snapshot_text(item.get("block_number"), "номер блока")
        sheet[f"A{row_number}"] = PRODUCT_NAME
        sheet[f"BF{row_number}"] = f"{code} {block_number}"
        sheet.row_dimensions[row_number].hidden = False
    for row_number in range(20 + len(items), 27):
        _clear_cells(sheet, f"A{row_number}", f"BF{row_number}")
        sheet.row_dimensions[row_number].hidden = True
    sheet["A28"] = f"{total_weight_kg:,} кг".replace(",", " ")
    _clear_cells(sheet, "A30", "BE30", "A33", "A35")
    sheet["B38"] = "-"

    # Перевозчик и неизменяемый бухгалтерский снимок машины/водителя.
    sheet["B43"] = CARRIER_DETAILS
    sheet["BE43"] = driver_name
    sheet["CG43"] = driver_license
    _clear_cells(sheet, "CX43")
    sheet["B46"] = vehicle_model
    sheet["BE46"] = vehicle_plate
    sheet["A49"] = "Тип владения: собственность"

    # Приём у отправителя. Формулы BE56, CO66 и CO82 сохраняются из бланка.
    sheet["A52"] = PICKUP_LOCATION
    sheet["A54"] = RUMEX_DETAILS
    sheet["A56"] = PICKUP_LOCATION
    sheet["A58"] = loaded_at.date()
    sheet["AC58"] = loaded_at.time().replace(second=0, microsecond=0)
    sheet["A60"] = f"{total_weight_kg:,}".replace(",", " ") + " кг, расчётная масса"
    sheet["A62"] = len(items)
    sheet["BE62"] = "без тары"
    sheet["A64"] = "-"
    _clear_cells(sheet, "BE58", "CH58")

    # Выдача груза оформляется вручную после прибытия: никаких примерных фактов.
    sheet["A74"] = DELIVERY_LOCATION
    _clear_cells(
        sheet,
        "A76",
        "Z76",
        "BE76",
        "A78",
        "BE78",
        "AF80",
        "BE80",
        "B90",
        "B96",
        "B97",
    )

    # Реквизиты составителей, но не подписи, основания, расчёты или оплату.
    for reference, value in (
        ("B92", CARRIER_DETAILS_SECOND_PAGE),
        ("BF92", OMEGA_DETAILS_SECOND_PAGE),
        ("BF96", OMEGA_DETAILS_SECOND_PAGE),
    ):
        cell = sheet[reference]
        cell.value = value
        font = copy(cell.font)
        font.sz = 8
        cell.font = font
        alignment = copy(cell.alignment)
        alignment.wrap_text = True
        alignment.vertical = "center"
        cell.alignment = alignment

    # Компактные однострочные реквизиты: оставляем в форме ИНН, КПП и адрес,
    # но не выводим ОГРН. Полный реквизит сохраняется в карточке контрагента.
    for row_number, height in ((9, 30), (54, 18), (92, 48), (96, 48)):
        sheet.row_dimensions[row_number].height = height

    calculation = workbook.calculation
    calculation.fullCalcOnLoad = True
    calculation.forceFullCalc = True
    calculation.calcMode = "auto"
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def write_test_ttn_workbook(shipment: dict, *, copy_number: int) -> Path:
    """Сохранить один отдельный экземпляр в изолированную папку экспорта."""
    content = build_test_ttn_workbook(shipment, copy_number=copy_number)
    filename = test_ttn_filename(shipment, copy_number=copy_number)
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


def archive_test_ttn_workbooks(shipment: dict) -> list[dict[str, Any]]:
    """Сохранить неизменяемые экземпляры ТТН до их выдачи водителю."""
    shipment_id = int(shipment.get("id") or 0)
    if shipment_id <= 0:
        raise ValueError("Не указан номер погрузки для архива ТТН")
    TEST_TTN_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    archived: list[dict[str, Any]] = []
    for copy_number in COPY_NUMBERS:
        content = build_test_ttn_workbook(shipment, copy_number=copy_number)
        digest = sha256(content).hexdigest()
        filename = f"test-shipment-{shipment_id}-ttn-copy-{copy_number}.xlsx"
        destination = TEST_TTN_ARCHIVE_DIR / filename
        if destination.exists():
            if sha256(destination.read_bytes()).hexdigest() != digest:
                raise ValueError("Архивный экземпляр ТТН не совпадает с выданным документом")
        else:
            with tempfile.NamedTemporaryFile(dir=TEST_TTN_ARCHIVE_DIR, delete=False) as temporary:
                temporary.write(content)
                temp_path = Path(temporary.name)
            try:
                temp_path.replace(destination)
            finally:
                temp_path.unlink(missing_ok=True)
        archived.append({
            "copy_number": copy_number,
            "filename": filename,
            "sha256": digest,
            "size_bytes": len(content),
        })
    return archived


def archived_test_ttn_path(filename: str) -> Path | None:
    """Вернуть файл архива только по безопасному имени из учётной записи."""
    name = Path(str(filename or "")).name
    if not re.fullmatch(r"test-shipment-\d+-ttn-copy-[1-4]\.xlsx", name):
        return None
    path = TEST_TTN_ARCHIVE_DIR / name
    return path if path.is_file() else None
