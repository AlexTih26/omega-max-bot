"""Личный чат: приход материалов на склад (пилот через MAX)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from maxapi import Bot
from maxapi.enums.chat_type import ChatType
from maxapi.types import MessageCreated
from maxapi.types.attachments.buttons.callback_button import CallbackButton
from maxapi.types.updates.message_callback import MessageCallback
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

logger = logging.getLogger(__name__)

_STATE_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "materials_receipt_state.json"
)
_bot: Bot | None = None

CB_MRC_MENU = "mrc_menu"
CB_MRC_RECEIPT = "mrc_rcpt"
CB_MRC_STOCK = "mrc_stock"
CB_MRC_NEED = "mrc_need"
CB_MRC_MAT_PREFIX = "mrc_mat:"
CB_MRC_CONFIRM = "mrc_ok"
CB_MRC_CANCEL = "mrc_cancel"
CB_MRC_EDIT_QTY = "mrc_edit"
CB_MRC_COND_NEW = "mrc_cnew"
CB_MRC_COND_USED = "mrc_cused"
CB_MRC_UNIT_M3 = "mrc_um3"
CB_MRC_UNIT_PCS = "mrc_upcs"
CB_MRC_UNIT_M = "mrc_um"
CB_MRC_UNIT_KG = "mrc_ukg"
CB_MRC_LEN_PREFIX = "mrc_len:"
CB_MRC_BACK_UNIT = "mrc_bunit"

_COND_NEW = "new"
_COND_USED = "used"

_STEP_UNIT = "unit"
_STEP_LENGTH = "length"
_STEP_QTY = "qty"
_STEP_CONFIRM = "confirm"

_UNIT_M3 = "м³"
_UNIT_PCS = "шт"
_UNIT_M = "м"
_UNIT_KG = "кг"

_KIND_TIMBER = "timber"
_KIND_METAL = "metal"

_RECEIPT_CATALOG: list[dict] = [
    {
        "key": "brus150",
        "label": "Брус 150×150",
        "enabled": True,
        "kind": _KIND_TIMBER,
        "unit": "м³",
        "warehouse_unit": "м³",
        "input_units": (_UNIT_M3, _UNIT_PCS),
        "cross_section_m2": 0.0225,
        "max_length_m": 4.0,
        "default_length_m": 3.0,
        "length_presets": (2.0, 3.0, 4.0),
        "db_names": ("Брус 150×150, м³", "Брус 150×150", "брус общая, м3"),
    },
    {
        "key": "brus100",
        "label": "Брус 100×150",
        "enabled": False,
        "kind": _KIND_TIMBER,
        "unit": "м³",
        "warehouse_unit": "м³",
        "input_units": (_UNIT_M3, _UNIT_PCS),
        "cross_section_m2": 0.015,
        "max_length_m": 4.0,
        "default_length_m": 3.0,
        "length_presets": (2.0, 3.0, 4.0),
        "db_names": (),
    },
    {"key": "doska50100", "label": "Доска 50×100, м³", "enabled": False, "kind": _KIND_TIMBER, "unit": "м³", "db_names": ()},
    {
        "key": "shvel20",
        "label": "Швеллер 20 ГОСТ",
        "enabled": True,
        "kind": _KIND_METAL,
        "unit": "м",
        "warehouse_unit": "м",
        "input_units": (_UNIT_M, _UNIT_KG),
        "kg_per_meter": 2.0,
        "db_names": ("швеллер 20 П", "Швеллер 20 ГОСТ"),
    },
    {"key": "prov6", "label": "Проволока 6 мм, кг", "enabled": False, "unit": "кг", "db_names": ()},
    {"key": "gvozdi", "label": "Гвозди 200/150 мм, кг", "enabled": False, "unit": "кг", "db_names": ()},
    {"key": "shpilka", "label": "Шпилька M20 1 м, шт", "enabled": False, "unit": "шт", "db_names": ()},
    {"key": "gayka20", "label": "Гайка 20, шт", "enabled": False, "unit": "шт", "db_names": ()},
    {
        "key": "ugolok",
        "label": "Уголок 120–140 мм",
        "enabled": False,
        "kind": _KIND_METAL,
        "unit": "м",
        "warehouse_unit": "м",
        "input_units": (_UNIT_M, _UNIT_KG),
        "kg_per_meter": 3.5,
        "db_names": (),
    },
    {"key": "disk320", "label": "Диск 320 мм, шт", "enabled": False, "unit": "шт", "db_names": ()},
]

_CATALOG_BY_KEY = {item["key"]: item for item in _RECEIPT_CATALOG}


def set_bot(bot: Bot) -> None:
    global _bot
    _bot = bot


def _tz() -> ZoneInfo:
    name = (
        os.getenv("MATERIALS_TIMEZONE")
        or os.getenv("TAKSIMO_TIMEZONE")
        or "Europe/Moscow"
    ).strip()
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("Europe/Moscow")


def _parse_id_set(raw: str) -> set[int]:
    out: set[int] = set()
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part))
        except ValueError:
            logger.warning("Пропущен некорректный MAX id: %s", part)
    return out


def materials_master_ids() -> set[int]:
    return _parse_id_set(os.getenv("MATERIALS_MASTER_MAX_IDS", ""))


def materials_supply_ids() -> set[int]:
    return _parse_id_set(os.getenv("MATERIALS_SUPPLY_MAX_IDS", ""))


def is_materials_master(user_id: int | None) -> bool:
    if user_id is None:
        return False
    masters = materials_master_ids()
    return bool(masters) and user_id in masters


def is_private_dialog(message) -> bool:
    if message is None:
        return False
    recipient = getattr(message, "recipient", None)
    if recipient is None:
        return False
    return getattr(recipient, "chat_type", None) == ChatType.DIALOG


def _load_state() -> dict:
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"users": {}}


def _save_state(state: dict) -> None:
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        logger.exception("Не удалось сохранить состояние прихода материалов")


def _get_user_state(user_id: int) -> dict | None:
    users = (_load_state().get("users") or {})
    raw = users.get(str(user_id))
    return raw if isinstance(raw, dict) else None


def _set_user_state(user_id: int, data: dict | None) -> None:
    state = _load_state()
    users = state.setdefault("users", {})
    key = str(user_id)
    if data is None:
        users.pop(key, None)
    else:
        users[key] = data
    _save_state(state)


def _user_display_name(user) -> str:
    first = (getattr(user, "first_name", None) or "").strip()
    if first:
        return first
    username = (getattr(user, "username", None) or "").strip()
    if username:
        return username
    return "мастер"


def _format_qty(value) -> str:
    try:
        num = float(value or 0)
    except (TypeError, ValueError):
        return "0"
    if abs(num - round(num)) < 0.001:
        return str(int(round(num)))
    text = f"{num:.3f}".rstrip("0").rstrip(".")
    return text or "0"


def _parse_quantity(text: str) -> float | None:
    return _parse_positive_number(text)


def _parse_positive_number(text: str) -> float | None:
    raw = (text or "").strip().replace(",", ".")
    if not raw:
        return None
    if not re.fullmatch(r"\d+(?:\.\d+)?", raw):
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    if value <= 0:
        return None
    return value


def _catalog_entry(key: str) -> dict | None:
    return _CATALOG_BY_KEY.get(key)


def _entry_kind(entry: dict) -> str:
    return (entry.get("kind") or "").strip()


def _needs_length_step(state: dict) -> bool:
    entry = _catalog_entry(str(state.get("material_key") or ""))
    if not entry:
        return False
    return _entry_kind(entry) == _KIND_TIMBER


def _max_length_m(entry: dict) -> float:
    raw = entry.get("max_length_m")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    env_key = f"MATERIALS_{entry.get('key', '').upper()}_MAX_LENGTH_M"
    raw_env = (os.getenv(env_key) or os.getenv("MATERIALS_TIMBER_MAX_LENGTH_M") or "4").strip()
    try:
        return float(raw_env.replace(",", "."))
    except ValueError:
        return 4.0


def _default_length_m(entry: dict) -> float:
    raw = entry.get("default_length_m")
    if raw is not None:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = 3.0
    else:
        env_key = f"MATERIALS_{entry.get('key', '').upper()}_DEFAULT_LENGTH_M"
        raw_env = (os.getenv(env_key) or os.getenv("MATERIALS_TIMBER_DEFAULT_LENGTH_M") or "3").strip()
        try:
            value = float(raw_env.replace(",", "."))
        except ValueError:
            value = 3.0
    return min(value, _max_length_m(entry))


def _length_presets(entry: dict) -> tuple[float, ...]:
    raw = entry.get("length_presets")
    if raw:
        return tuple(float(x) for x in raw)
    max_len = _max_length_m(entry)
    return (2.0, 3.0, max_len)


def _parse_length(text: str, *, max_m: float) -> float | None:
    value = _parse_positive_number(text)
    if value is None:
        return None
    if value > max_m:
        return None
    return value


def _piece_length_m(state: dict) -> float:
    entry = _catalog_entry(str(state.get("material_key") or "")) or {}
    raw = state.get("piece_length_m")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    return _default_length_m(entry)


def _cross_section_m2(entry: dict) -> float:
    raw = entry.get("cross_section_m2")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    return 0.0225


def _kg_per_meter(material_key: str, entry: dict | None = None) -> float:
    entry = entry or _catalog_entry(material_key) or {}
    env_key = f"MATERIALS_{material_key.upper()}_KG_PER_M"
    raw = (os.getenv(env_key) or "").strip()
    if raw:
        try:
            value = float(raw.replace(",", "."))
            if value > 0:
                return value
        except ValueError:
            pass
    raw_entry = entry.get("kg_per_meter")
    if raw_entry is not None:
        try:
            value = float(raw_entry)
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass
    return 0.0


def _material_input_units(entry: dict) -> tuple[str, ...]:
    raw = entry.get("input_units")
    if raw:
        return tuple(raw)
    unit = (entry.get("unit") or _UNIT_PCS).strip() or _UNIT_PCS
    return (unit,)


def _needs_unit_choice(entry: dict) -> bool:
    return len(_material_input_units(entry)) > 1


def _warehouse_unit(entry: dict) -> str:
    return (entry.get("warehouse_unit") or entry.get("unit") or _UNIT_PCS).strip()


def _piece_volume_m3(state: dict) -> float:
    entry = _catalog_entry(str(state.get("material_key") or "")) or {}
    length_m = _piece_length_m(state)
    return round(_cross_section_m2(entry) * length_m, 4)


def _convert_input_to_warehouse(state: dict) -> float:
    qty_input = float(state.get("qty_input") or state.get("qty") or 0)
    input_unit = (state.get("input_unit") or state.get("unit") or "").strip()
    warehouse_unit = (state.get("warehouse_unit") or state.get("unit") or "").strip()
    material_key = str(state.get("material_key") or "")

    if input_unit == _UNIT_PCS and warehouse_unit in (_UNIT_M3, "м3"):
        factor = _piece_volume_m3(state)
        if factor <= 0:
            raise ValueError("Не задан пересчёт шт → м³")
        return round(qty_input * factor, 3)

    if input_unit == _UNIT_KG and warehouse_unit == _UNIT_M:
        factor = _kg_per_meter(material_key)
        if factor <= 0:
            raise ValueError("Не задан пересчёт кг → м")
        return round(qty_input / factor, 3)

    return qty_input


def _receipt_qty_label(state: dict) -> str:
    unit = state.get("input_unit") or state.get("unit") or ""
    qty_input = state.get("qty_input", state.get("qty"))
    warehouse_unit = state.get("warehouse_unit") or state.get("unit") or ""
    converted = state.get("qty")

    if unit == _UNIT_PCS and warehouse_unit in (_UNIT_M3, "м3"):
        return f"+{_format_qty(qty_input)} {unit} (= {_format_qty(converted)} {warehouse_unit})"
    if unit == _UNIT_KG and warehouse_unit == _UNIT_M:
        return f"+{_format_qty(qty_input)} {unit} (= {_format_qty(converted)} {warehouse_unit})"
    return f"+{_format_qty(qty_input)} {unit}"


def _unit_choice_text(entry: dict) -> str:
    kind = _entry_kind(entry)
    if kind == _KIND_TIMBER:
        return (
            f"Материал: {entry['label']}\n\n"
            "Как принять на склад?\n"
            "• м³ — объём\n"
            "• шт — количество штук\n\n"
            "Дальше укажете длину (не более 4 м) и количество."
        )
    if kind == _KIND_METAL:
        return (
            f"Материал: {entry['label']}\n\n"
            "Как принять на склад?\n"
            "• м — длина в метрах\n"
            "• кг — вес (пересчитаем в метры для склада)"
        )
    units = ", ".join(_material_input_units(entry))
    return f"Материал: {entry['label']}\n\nЕдиница прихода: {units}"


def _length_prompt_text(state: dict) -> str:
    entry = _catalog_entry(str(state.get("material_key") or "")) or {}
    max_len = _max_length_m(entry)
    default_len = _default_length_m(entry)
    unit = state.get("input_unit") or state.get("unit") or ""
    lines = [
        f"Материал: {state.get('label') or '—'}",
        f"Приход: {unit}",
        "",
        f"Длина одной штуки, м (не более {_format_qty(max_len)}):",
        f"Предложение: {_format_qty(default_len)} м",
        "",
        "Нажмите кнопку или введите число.",
    ]
    return "\n".join(lines)


def _qty_prompt_text(state: dict) -> str:
    unit = state.get("input_unit") or state.get("unit") or "ед."
    entry = _catalog_entry(str(state.get("material_key") or "")) or {}
    lines = [
        f"Материал: {state.get('label') or '—'}",
        f"Единица: {unit}",
    ]
    if _needs_length_step(state):
        lines.append(f"Длина: {_format_qty(_piece_length_m(state))} м")
    lines.append("")

    if unit == _UNIT_PCS:
        factor = _piece_volume_m3(state)
        lines.append(
            f"1 шт = {_format_qty(factor)} м³ (сечение 150×150, "
            f"длина {_format_qty(_piece_length_m(state))} м)."
        )
        lines.append("")
        lines.append("Введите количество штук целым числом.")
        lines.append("Пример: 10")
    elif unit == _UNIT_M3:
        if _needs_length_step(state):
            lines.append(
                f"Длина партии: {_format_qty(_piece_length_m(state))} м (не более 4 м)."
            )
            lines.append("")
        lines.append("Введите объём (м³) одним числом.")
        lines.append("Пример: 2.5")
    elif unit == _UNIT_KG:
        factor = _kg_per_meter(str(state.get("material_key") or ""), entry)
        lines.append(f"1 м ≈ {_format_qty(factor)} кг на складе.")
        lines.append("")
        lines.append("Введите вес (кг) одним числом.")
        lines.append("Пример: 120")
    elif unit == _UNIT_M:
        lines.append("Введите длину (м) одним числом.")
        lines.append("Пример: 12")
    else:
        lines.append(f"Введите количество ({unit}) одним числом.")
    return "\n".join(lines)


def _resolve_material_id(entry: dict) -> int:
    from taksimo_store import create_material_item, list_material_items

    env_key = f"MATERIALS_PILOT_{entry['key'].upper()}_ID"
    raw_id = (os.getenv(env_key) or "").strip()
    if raw_id:
        return int(raw_id)

    names = {name.strip().lower() for name in entry.get("db_names") or () if name.strip()}
    for item in list_material_items():
        item_name = (item.get("name") or "").strip().lower()
        if item_name in names:
            return int(item["id"])

    primary_name = next(iter(entry.get("db_names") or ()), entry.get("label") or entry["key"])
    created = create_material_item(name=primary_name, unit=entry.get("unit") or "шт")
    return int(created["id"])


def _condition_label(raw: str | None) -> str:
    if raw == _COND_USED:
        return "б/у"
    return "новый"


def _stock_snapshot(material_id: int) -> tuple[float, float]:
    from taksimo_store import get_material_item

    item = get_material_item(material_id) or {}
    on_hand = float(item.get("on_hand") or 0)
    available = float(item.get("available") or 0)
    return on_hand, available


def _enrich_confirm_state(state: dict) -> None:
    material_id = int(state.get("material_id") or 0)
    try:
        state["qty"] = _convert_input_to_warehouse(state)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    qty = float(state.get("qty") or 0)
    on_hand_before, available_before = _stock_snapshot(material_id)
    state.setdefault("condition", _COND_NEW)
    state["on_hand_before"] = on_hand_before
    state["available_before"] = available_before
    state["on_hand_after"] = round(on_hand_before + qty, 3)
    state["available_after"] = round(available_before + qty, 3)


def _before_after_line(*, label: str, before, after, unit: str) -> str:
    return (
        f"{label}: было {_format_qty(before)} → "
        f"станет {_format_qty(after)} {unit}".strip()
    )


def _before_after_done_line(*, label: str, before, after, unit: str) -> str:
    return (
        f"{label}: было {_format_qty(before)} → "
        f"стало {_format_qty(after)} {unit}".strip()
    )


def _materials_menu_text() -> str:
    return (
        "📦 Материалы\n\n"
        "• Приход на склад — записать поступление\n"
        "• Резерв — зарезервировать под вагон\n"
        "• На складе — текущие остатки\n"
        "• Что надо — дефицит по вагонам и минимумам"
    )


def _materials_menu_keyboard() -> InlineKeyboardBuilder:
    from materials_reserve_chat import CB_MRC_RESERVE

    kb = InlineKeyboardBuilder()
    kb.row(CallbackButton(text="📦 Приход на склад", payload=CB_MRC_RECEIPT))
    kb.row(CallbackButton(text="🔒 Резерв", payload=CB_MRC_RESERVE))
    kb.row(
        CallbackButton(text="📋 На складе", payload=CB_MRC_STOCK),
        CallbackButton(text="🔴 Что надо", payload=CB_MRC_NEED),
    )
    return kb


def _unit_choice_keyboard(entry: dict) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    row: list[CallbackButton] = []
    for unit in _material_input_units(entry):
        if unit == _UNIT_M3:
            row.append(CallbackButton(text="📐 м³", payload=CB_MRC_UNIT_M3))
        elif unit == _UNIT_PCS:
            row.append(CallbackButton(text="🪵 шт", payload=CB_MRC_UNIT_PCS))
        elif unit == _UNIT_M:
            row.append(CallbackButton(text="📏 м", payload=CB_MRC_UNIT_M))
        elif unit == _UNIT_KG:
            row.append(CallbackButton(text="⚖️ кг", payload=CB_MRC_UNIT_KG))
    if row:
        if len(row) == 1:
            kb.row(row[0])
        else:
            kb.row(*row[:2])
            if len(row) > 2:
                kb.row(*row[2:])
    kb.row(CallbackButton(text="← Назад", payload=CB_MRC_RECEIPT))
    return kb


def _length_choice_keyboard(state: dict) -> InlineKeyboardBuilder:
    entry = _catalog_entry(str(state.get("material_key") or "")) or {}
    kb = InlineKeyboardBuilder()
    presets = [p for p in _length_presets(entry) if p <= _max_length_m(entry)]
    if presets:
        kb.row(
            *[
                CallbackButton(
                    text=f"{_format_qty(preset)} м",
                    payload=f"{CB_MRC_LEN_PREFIX}{preset}",
                )
                for preset in presets
            ]
        )
    kb.row(CallbackButton(text="← Назад", payload=CB_MRC_BACK_UNIT))
    return kb


def _unit_payload_to_input(unit_payload: str) -> str | None:
    mapping = {
        CB_MRC_UNIT_M3: _UNIT_M3,
        CB_MRC_UNIT_PCS: _UNIT_PCS,
        CB_MRC_UNIT_M: _UNIT_M,
        CB_MRC_UNIT_KG: _UNIT_KG,
    }
    return mapping.get(unit_payload)


def _advance_after_unit(state: dict) -> None:
    entry = _catalog_entry(str(state.get("material_key") or "")) or {}
    if _needs_length_step(state):
        state["step"] = _STEP_LENGTH
        state["piece_length_m"] = _default_length_m(entry)
    else:
        state["step"] = _STEP_QTY


def _materials_list_keyboard() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    for item in _RECEIPT_CATALOG:
        prefix = "✅ " if item.get("enabled") else "⏳ "
        kb.row(
            CallbackButton(
                text=f"{prefix}{item['label']}",
                payload=f"{CB_MRC_MAT_PREFIX}{item['key']}",
            )
        )
    kb.row(CallbackButton(text="← Назад", payload=CB_MRC_MENU))
    return kb


def _confirm_keyboard(state: dict) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    condition = state.get("condition") or _COND_NEW
    new_mark = " ✓" if condition == _COND_NEW else ""
    used_mark = " ✓" if condition == _COND_USED else ""
    kb.row(
        CallbackButton(text=f"🆕 Новый{new_mark}", payload=CB_MRC_COND_NEW),
        CallbackButton(text=f"♻️ Б/у{used_mark}", payload=CB_MRC_COND_USED),
    )
    kb.row(
        CallbackButton(text="✅ Записать", payload=CB_MRC_CONFIRM),
        CallbackButton(text="✏️ Изменить кол-во", payload=CB_MRC_EDIT_QTY),
    )
    kb.row(CallbackButton(text="❌ Отмена", payload=CB_MRC_CANCEL))
    return kb


def master_private_attachments(user_id: int | None) -> list:
    if user_id is None or not is_materials_master(user_id):
        return []
    return [_materials_menu_keyboard().as_markup()]


async def send_materials_receipt_menu(*, user_id: int) -> None:
    if _bot is None:
        return
    await _bot.send_message(
        user_id=user_id,
        text=_materials_menu_text(),
        attachments=[_materials_menu_keyboard().as_markup()],
    )


async def _send_master_messages(
    *,
    user_id: int,
    texts: list[str],
    with_menu: bool = True,
) -> None:
    if _bot is None or not texts:
        return
    attachments = [_materials_menu_keyboard().as_markup()] if with_menu else None
    for index, text in enumerate(texts):
        is_last = index == len(texts) - 1
        await _bot.send_message(
            user_id=user_id,
            text=text,
            attachments=attachments if is_last and with_menu else None,
        )
        if not is_last:
            await asyncio.sleep(0.25)


async def send_materials_stock_info(*, user_id: int) -> None:
    from materials_chat import format_materials_stock_brief

    await _send_master_messages(
        user_id=user_id,
        texts=format_materials_stock_brief(),
    )


async def send_materials_need_info(*, user_id: int) -> None:
    from materials_chat import format_materials_need_brief

    await _send_master_messages(
        user_id=user_id,
        texts=format_materials_need_brief(),
    )


async def _notify_supply(
    *,
    master_name: str,
    master_id: int,
    material_name: str,
    receipt_label: str,
    warehouse_unit: str,
    condition: str,
    on_hand_before,
    on_hand_after,
    available_before,
    available_after,
) -> None:
    if _bot is None:
        return
    now = datetime.now(_tz()).strftime("%d.%m.%Y %H:%M")
    cond = _condition_label(condition)
    text = "\n".join(
        [
            "📥 Новый приход на склад",
            "",
            f"Материал: {material_name}",
            f"Приход: {receipt_label} · {cond}",
            _before_after_done_line(
                label="Факт",
                before=on_hand_before,
                after=on_hand_after,
                unit=warehouse_unit,
            ),
            _before_after_done_line(
                label="Свободно",
                before=available_before,
                after=available_after,
                unit=warehouse_unit,
            ),
            "",
            f"Мастер: {master_name} (id {master_id})",
            f"Время: {now}",
        ]
    )
    for supply_id in materials_supply_ids():
        try:
            await _bot.send_message(user_id=supply_id, text=text)
        except Exception:
            logger.exception("Не удалось уведомить снабжение user_id=%s", supply_id)

    try:
        from materials_chat import notify_event

        await notify_event(
            "\n".join(
                [
                    "🟢 Приход через MAX",
                    f"{material_name}: {receipt_label} · {cond}",
                    _before_after_done_line(
                        label="Факт",
                        before=on_hand_before,
                        after=on_hand_after,
                        unit=warehouse_unit,
                    ),
                    f"Мастер: {master_name}",
                ]
            )
        )
    except Exception:
        logger.exception("Не удалось отправить приход в группу расходников")


def _confirm_text(state: dict) -> str:
    warehouse_unit = state.get("warehouse_unit") or state.get("unit") or ""
    cond = _condition_label(state.get("condition"))
    lines = [
        "Подтвердите приход:",
        "",
        f"Материал: {state.get('label') or '—'}",
        f"Приход: {_receipt_qty_label(state)}",
    ]
    if _needs_length_step(state):
        lines.append(f"Длина: {_format_qty(_piece_length_m(state))} м")
    lines.append(f"Состояние: {cond}")
    if state.get("on_hand_before") is not None:
        lines.append(
            _before_after_line(
                label="Факт",
                before=state.get("on_hand_before"),
                after=state.get("on_hand_after"),
                unit=warehouse_unit,
            )
        )
        lines.append(
            _before_after_line(
                label="Свободно",
                before=state.get("available_before"),
                after=state.get("available_after"),
                unit=warehouse_unit,
            )
        )
    lines.extend(["", "Выберите новый или б/у и нажмите «Записать»."])
    return "\n".join(lines)


async def _apply_receipt(*, user_id: int, user_name: str, state: dict) -> tuple[bool, str]:
    from taksimo_store import add_material_receipt

    material_id = int(state.get("material_id") or 0)
    qty = float(state.get("qty") or 0)
    if material_id <= 0 or qty <= 0:
        return False, "Не хватает данных для записи. Начните сначала."

    note_parts = ["MAX", state.get("label") or ""]
    input_unit = state.get("input_unit")
    if _needs_length_step(state):
        note_parts.append(f"L={_format_qty(_piece_length_m(state))} м")
    if input_unit == _UNIT_PCS and state.get("qty_input") is not None:
        note_parts.append(
            f"{_format_qty(state.get('qty_input'))} шт → {_format_qty(qty)} м³"
        )
    elif input_unit == _UNIT_KG and state.get("qty_input") is not None:
        note_parts.append(
            f"{_format_qty(state.get('qty_input'))} кг → {_format_qty(qty)} м"
        )
    elif input_unit == _UNIT_M3 and _needs_length_step(state):
        note_parts.append(f"{_format_qty(state.get('qty_input'))} м³")
    note_parts.append(_condition_label(state.get("condition")))
    note = " · ".join(part for part in note_parts if part)
    on_hand_before = float(state.get("on_hand_before") or 0)
    available_before = float(state.get("available_before") or 0)
    try:
        item = add_material_receipt(
            material_id,
            quantity=qty,
            operator=f"{user_name} ({user_id})",
            note=note,
        )
    except Exception as exc:
        logger.exception("Ошибка прихода material_id=%s qty=%s", material_id, qty)
        return False, f"Не удалось записать: {exc}"

    warehouse_unit = item.get("unit") or state.get("warehouse_unit") or state.get("unit") or ""
    cond = _condition_label(state.get("condition"))
    receipt_label = _receipt_qty_label(state)
    await _notify_supply(
        master_name=user_name,
        master_id=user_id,
        material_name=item.get("name") or state.get("label") or "—",
        receipt_label=receipt_label,
        warehouse_unit=warehouse_unit,
        condition=state.get("condition") or _COND_NEW,
        on_hand_before=on_hand_before,
        on_hand_after=item.get("on_hand"),
        available_before=available_before,
        available_after=item.get("available"),
    )
    return True, "\n".join(
        [
            "✅ Приход записан.",
            "",
            f"{item.get('name')}: {receipt_label} · {cond}",
            _before_after_done_line(
                label="Факт",
                before=on_hand_before,
                after=item.get("on_hand"),
                unit=warehouse_unit,
            ),
            _before_after_done_line(
                label="Свободно",
                before=available_before,
                after=item.get("available"),
                unit=warehouse_unit,
            ),
        ]
    )


async def handle_materials_receipt_callback(event: MessageCallback, bot: Bot) -> bool:
    payload = event.callback.payload if event.callback else None
    if not payload or not payload.startswith("mrc_"):
        return False

    if not is_private_dialog(event.message):
        await event.answer(notification="Только в личном чате с ботом")
        return True

    user = event.callback.user
    user_id = user.user_id if user else None
    if user_id is None:
        await event.answer(notification="Ошибка пользователя")
        return True

    if not is_materials_master(user_id):
        await event.answer(notification="Нет доступа")
        return True

    user_name = _user_display_name(user)

    if payload == CB_MRC_MENU:
        _set_user_state(user_id, None)
        await event.answer(notification="Меню материалов")
        await event.edit(
            text=_materials_menu_text(),
            attachments=[_materials_menu_keyboard().as_markup()],
        )
        return True

    if payload == CB_MRC_STOCK:
        await event.answer(notification="Остатки")
        await send_materials_stock_info(user_id=user_id)
        return True

    if payload == CB_MRC_NEED:
        await event.answer(notification="Дефицит")
        await send_materials_need_info(user_id=user_id)
        return True

    if payload == CB_MRC_RECEIPT:
        _set_user_state(user_id, None)
        await event.answer(notification="Выберите материал")
        await event.edit(
            text="Выберите материал для прихода:",
            attachments=[_materials_list_keyboard().as_markup()],
        )
        return True

    if payload.startswith(CB_MRC_MAT_PREFIX):
        key = payload[len(CB_MRC_MAT_PREFIX) :]
        entry = _catalog_entry(key)
        if entry is None:
            await event.answer(notification="Неизвестный материал")
            return True
        if not entry.get("enabled"):
            await event.answer(notification="Скоро будет доступно")
            return True
        try:
            material_id = _resolve_material_id(entry)
        except Exception as exc:
            logger.exception("Не удалось определить material_id key=%s", key)
            await event.answer(notification="Ошибка материала")
            await event.edit(text=f"Не удалось подготовить материал: {exc}")
            return True
        base_state = {
            "material_key": key,
            "material_id": material_id,
            "label": entry["label"],
            "warehouse_unit": _warehouse_unit(entry),
        }
        if _needs_unit_choice(entry):
            base_state["step"] = _STEP_UNIT
            _set_user_state(user_id, base_state)
            await event.answer(notification="Выберите единицу")
            await event.edit(
                text=_unit_choice_text(entry),
                attachments=[_unit_choice_keyboard(entry).as_markup()],
            )
            return True

        input_unit = _material_input_units(entry)[0]
        _set_user_state(
            user_id,
            {
                **base_state,
                "step": _STEP_QTY,
                "input_unit": input_unit,
                "unit": input_unit,
            },
        )
        await event.answer(notification="Введите количество")
        await event.edit(
            text=_qty_prompt_text(
                {
                    **base_state,
                    "input_unit": input_unit,
                    "unit": input_unit,
                }
            ),
            attachments=[],
        )
        return True

    state = _get_user_state(user_id)

    if payload in (CB_MRC_UNIT_M3, CB_MRC_UNIT_PCS, CB_MRC_UNIT_M, CB_MRC_UNIT_KG):
        if not state or state.get("step") != _STEP_UNIT:
            await event.answer(notification="Сначала выберите материал")
            return True
        input_unit = _unit_payload_to_input(payload)
        if input_unit is None:
            await event.answer(notification="Неизвестная единица")
            return True
        state["input_unit"] = input_unit
        state["unit"] = input_unit
        _advance_after_unit(state)
        _set_user_state(user_id, state)
        await event.answer(notification=input_unit)
        if state.get("step") == _STEP_LENGTH:
            await event.edit(
                text=_length_prompt_text(state),
                attachments=[_length_choice_keyboard(state).as_markup()],
            )
        else:
            await event.edit(
                text=_qty_prompt_text(state),
                attachments=[],
            )
        return True

    if payload.startswith(CB_MRC_LEN_PREFIX):
        if not state or state.get("step") != _STEP_LENGTH:
            await event.answer(notification="Сначала выберите материал")
            return True
        entry = _catalog_entry(str(state.get("material_key") or "")) or {}
        raw_len = payload[len(CB_MRC_LEN_PREFIX) :]
        try:
            length = float(raw_len.replace(",", "."))
        except ValueError:
            await event.answer(notification="Некорректная длина")
            return True
        if length <= 0 or length > _max_length_m(entry):
            await event.answer(notification=f"Длина до {_format_qty(_max_length_m(entry))} м")
            return True
        state["piece_length_m"] = length
        state["step"] = _STEP_QTY
        _set_user_state(user_id, state)
        await event.answer(notification=f"{_format_qty(length)} м")
        await event.edit(
            text=_qty_prompt_text(state),
            attachments=[],
        )
        return True

    if payload == CB_MRC_BACK_UNIT:
        if not state:
            await event.answer(notification="Сессия сброшена")
            return True
        entry = _catalog_entry(str(state.get("material_key") or "")) or {}
        state["step"] = _STEP_UNIT
        _set_user_state(user_id, state)
        await event.answer(notification="Единица прихода")
        await event.edit(
            text=_unit_choice_text(entry),
            attachments=[_unit_choice_keyboard(entry).as_markup()],
        )
        return True

    if payload == CB_MRC_CANCEL:
        _set_user_state(user_id, None)
        await event.answer(notification="Отменено")
        await event.edit(
            text="Приход отменён.",
            attachments=[_materials_menu_keyboard().as_markup()],
        )
        return True

    if payload == CB_MRC_EDIT_QTY:
        if not state:
            await event.answer(notification="Сессия сброшена")
            return True
        state["step"] = _STEP_QTY
        _set_user_state(user_id, state)
        await event.answer(notification="Введите новое количество")
        await event.edit(
            text=_qty_prompt_text(state),
            attachments=[],
        )
        return True

    if payload in (CB_MRC_COND_NEW, CB_MRC_COND_USED):
        if not state or state.get("step") != _STEP_CONFIRM:
            await event.answer(notification="Сначала введите количество")
            return True
        state["condition"] = _COND_NEW if payload == CB_MRC_COND_NEW else _COND_USED
        _set_user_state(user_id, state)
        label = _condition_label(state["condition"])
        await event.answer(notification=label)
        await event.edit(
            text=_confirm_text(state),
            attachments=[_confirm_keyboard(state).as_markup()],
        )
        return True

    if payload == CB_MRC_CONFIRM:
        if not state or state.get("step") != _STEP_CONFIRM:
            await event.answer(notification="Нет данных для записи")
            return True
        ok, message = await _apply_receipt(
            user_id=user_id,
            user_name=user_name,
            state=state,
        )
        _set_user_state(user_id, None)
        await event.answer(notification="Записано" if ok else "Ошибка")
        attachments = [_materials_menu_keyboard().as_markup()] if ok else []
        await event.edit(text=message, attachments=attachments)
        return True

    return False


async def handle_materials_receipt_message(event: MessageCreated, bot: Bot) -> bool:
    if not is_private_dialog(event.message):
        return False

    sender = event.message.sender
    if not sender or sender.is_bot:
        return False

    user_id = sender.user_id
    body = event.message.body
    text = body.text.strip() if body and body.text else ""

    if text.startswith("/materials") or text.startswith("/receipt"):
        if not is_materials_master(user_id):
            await event.message.answer("Команда доступна только мастерам склада.")
            return True
        await send_materials_receipt_menu(user_id=user_id)
        return True

    if text.startswith("/stock") or text.startswith("/ostatki"):
        if not is_materials_master(user_id):
            await event.message.answer("Команда доступна только мастерам склада.")
            return True
        await send_materials_stock_info(user_id=user_id)
        return True

    if text.startswith("/need") or text.startswith("/deficit"):
        if not is_materials_master(user_id):
            await event.message.answer("Команда доступна только мастерам склада.")
            return True
        await send_materials_need_info(user_id=user_id)
        return True

    if not is_materials_master(user_id):
        return False

    lowered = text.lower()
    if lowered in {"остатки", "на складе", "склад сейчас", "что на складе"}:
        await send_materials_stock_info(user_id=user_id)
        return True
    if lowered in {"что надо", "дефицит", "нужно", "не хватает", "надо"}:
        await send_materials_need_info(user_id=user_id)
        return True
    if lowered in {"резерв", "reserve"}:
        from materials_reserve_chat import send_materials_reserve_menu

        await send_materials_reserve_menu(user_id=user_id)
        return True

    keywords = {"приход", "материалы", "materials", "склад"}
    if lowered in keywords:
        await send_materials_receipt_menu(user_id=user_id)
        return True

    state = _get_user_state(user_id)
    if not state or state.get("flow") == "reserve":
        return False

    if text.startswith("/"):
        return False

    step = state.get("step")

    if step == _STEP_LENGTH:
        entry = _catalog_entry(str(state.get("material_key") or "")) or {}
        max_len = _max_length_m(entry)
        length = _parse_length(text, max_m=max_len)
        if length is None:
            await event.message.answer(
                f"Введите длину в метрах от 0 до {_format_qty(max_len)}.\n"
                f"Пример: {_format_qty(_default_length_m(entry))}"
            )
            return True
        state["piece_length_m"] = length
        state["step"] = _STEP_QTY
        _set_user_state(user_id, state)
        await event.message.answer(_qty_prompt_text(state))
        return True

    if step != _STEP_QTY:
        return False

    qty = _parse_quantity(text)
    if qty is None:
        unit = state.get("input_unit") or state.get("unit") or ""
        if unit == _UNIT_PCS:
            example = "10"
        elif unit == _UNIT_KG:
            example = "120"
        elif unit == _UNIT_M:
            example = "12"
        else:
            example = "2.5"
        await event.message.answer(
            f"Введите количество ({unit}) одним числом больше нуля.\n"
            f"Пример: {example}"
        )
        return True

    if (state.get("input_unit") or state.get("unit")) == _UNIT_PCS:
        if abs(qty - round(qty)) > 0.001:
            await event.message.answer("Для штук введите целое число.\nПример: 10")
            return True
        qty = float(int(round(qty)))

    state["qty_input"] = qty
    state["step"] = _STEP_CONFIRM
    try:
        _enrich_confirm_state(state)
    except ValueError as exc:
        await event.message.answer(str(exc))
        return True
    _set_user_state(user_id, state)
    await event.message.answer(
        _confirm_text(state),
        attachments=[_confirm_keyboard(state).as_markup()],
    )
    return True
