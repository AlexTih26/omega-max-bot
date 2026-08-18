"""Личный чат: резерв материалов под вагон."""

from __future__ import annotations

import logging
import re
from datetime import datetime

from maxapi import Bot
from maxapi.types import MessageCreated
from maxapi.types.attachments.buttons.callback_button import CallbackButton
from maxapi.types.updates.message_callback import MessageCallback
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

from materials_receipt_chat import (
    _format_qty,
    _get_user_state,
    _materials_menu_keyboard,
    _parse_quantity,
    _set_user_state,
    _tz,
    is_materials_master,
    is_private_dialog,
    _user_display_name,
)

logger = logging.getLogger(__name__)
_bot: Bot | None = None

FLOW_RESERVE = "reserve"

CB_MRC_RESERVE = "mrc_rsv"
CB_MRC_RSV_WAGON_PREFIX = "mrc_rw:"
CB_MRC_RSV_MAT_PREFIX = "mrc_rm:"
CB_MRC_RSV_KIT = "mrc_rkit"
CB_MRC_RSV_CONFIRM = "mrc_rok"
CB_MRC_RSV_CANCEL = "mrc_rcn"
CB_MRC_RSV_EDIT_QTY = "mrc_redit"
CB_MRC_RSV_BACK_WAGON = "mrc_rbw"

_STEP_RSV_SEARCH = "rsv_search"
_STEP_RSV_QTY = "rsv_qty"
_STEP_RSV_CONFIRM = "rsv_confirm"
_STEP_RSV_KIT_CONFIRM = "rsv_kit_confirm"


def set_bot(bot: Bot) -> None:
    global _bot
    _bot = bot


def _scheme_short(label: str) -> str:
    name = (label or "").strip()
    match = re.search(r"(\d+)", name)
    if match:
        return f"С{match.group(1)}"
    return name or "—"


def _occupied_wagons() -> list[dict]:
    from taksimo_store import wagon_plan

    plan = wagon_plan()
    out: list[dict] = []
    for zone_name in ("ТУРАН", "ГРУЗОВОЙ"):
        zone_label = "Т" if zone_name == "ТУРАН" else "Г"
        for slot in (plan.get("dead_ends") or {}).get(zone_name) or []:
            wagon_number = (slot.get("wagon_number") or "").strip()
            if not wagon_number:
                continue
            out.append(
                {
                    "zone": zone_name,
                    "zone_label": zone_label,
                    "slot_index": int(slot.get("slot_index") or 0),
                    "wagon_number": wagon_number,
                    "scheme_code": slot.get("scheme_code") or "scheme1",
                    "scheme_label": slot.get("scheme_label") or "",
                }
            )
    return out


def _wagon_button_label(wagon: dict) -> str:
    return (
        f"{wagon['zone_label']}{wagon['slot_index']} · "
        f"{wagon['wagon_number']} · {_scheme_short(wagon['scheme_label'])}"
    )


def _wagon_slot_short(wagon: dict) -> str:
    zone_label = (wagon.get("zone_label") or "").strip().lower()
    slot_index = wagon.get("slot_index")
    if zone_label and slot_index:
        return f"{zone_label}{slot_index}"
    return ""


def _wagon_search_haystack(wagon: dict) -> str:
    slot_short = _wagon_slot_short(wagon)
    parts = [
        wagon.get("wagon_number") or "",
        wagon.get("zone") or "",
        wagon.get("zone_label") or "",
        str(wagon.get("slot_index") or ""),
        slot_short,
        wagon.get("scheme_code") or "",
        wagon.get("scheme_label") or "",
        _scheme_short(wagon.get("scheme_label") or ""),
        f"слот {wagon.get('slot_index')}",
        f"тупик {wagon.get('zone')}",
        f"№{wagon.get('slot_index')}",
    ]
    return " ".join(parts).lower()


def _normalize_wagon_query(query: str) -> str:
    return re.sub(r"\s+", "", (query or "").strip().lower())


def _match_wagons_by_query(query: str) -> list[dict]:
    q = (query or "").strip().lower()
    q_compact = _normalize_wagon_query(query)
    wagons = _occupied_wagons()
    if not q:
        return wagons
    exact = [
        wagon
        for wagon in wagons
        if (wagon.get("wagon_number") or "").strip().lower() == q
    ]
    if len(exact) == 1:
        return exact
    matched: list[dict] = []
    for wagon in wagons:
        haystack = _wagon_search_haystack(wagon)
        if q in haystack:
            matched.append(wagon)
            continue
        slot_short = _wagon_slot_short(wagon)
        if q_compact and slot_short and q_compact == slot_short:
            matched.append(wagon)
    return matched


def _wagon_pick_keyboard_filtered(wagons: list[dict]) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    for wagon in wagons:
        kb.row(
            CallbackButton(
                text=_wagon_button_label(wagon),
                payload=f"{CB_MRC_RSV_WAGON_PREFIX}{wagon['wagon_number']}",
            )
        )
    kb.row(CallbackButton(text="← Меню", payload=CB_MRC_RSV_CANCEL))
    return kb


def _wagon_search_menu_keyboard() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(CallbackButton(text="← Меню", payload=CB_MRC_RSV_CANCEL))
    return kb


def _wagon_search_prompt_text(*, query: str = "", too_many: int = 0) -> str:
    lines = [
        "🔒 Резерв материалов",
        "",
        "Введите номер вагона, тупик (ТУРАН/ГРУЗОВОЙ) или слот.",
        "Пример: 12345678, Т1, Г3, туран",
    ]
    if query:
        if too_many:
            lines.extend(
                [
                    "",
                    f"По запросу «{query}» найдено {too_many} вагонов.",
                    "Уточните запрос (номер, тупик или слот).",
                ]
            )
        else:
            lines.extend(["", f"По запросу «{query}» вагоны не найдены."])
    elif not _occupied_wagons():
        lines.extend(["", "Сейчас нет вагонов в слотах ТУРАН/ГРУЗОВОЙ."])
    return "\n".join(lines)


def _wagon_state_from_meta(wagon_meta: dict) -> dict:
    return {
        "flow": FLOW_RESERVE,
        "wagon_number": wagon_meta["wagon_number"],
        "zone": wagon_meta["zone"],
        "slot_index": wagon_meta["slot_index"],
        "scheme_code": wagon_meta["scheme_code"],
        "scheme_label": wagon_meta["scheme_label"],
    }


def _wagon_materials_prompt(state: dict) -> str:
    return (
        f"Резерв под вагон\n{_wagon_header(state)}\n\n"
        "Выберите материал или комплект:"
    )


def _wagon_pick_keyboard() -> InlineKeyboardBuilder:
    return _wagon_pick_keyboard_filtered(_occupied_wagons())


def _reserve_materials_keyboard(*, scheme_code: str) -> InlineKeyboardBuilder:
    from taksimo_store import list_material_items

    kb = InlineKeyboardBuilder()
    if scheme_code == "scheme1":
        kb.row(CallbackButton(text="📦 Комплект схемы 1", payload=CB_MRC_RSV_KIT))
    for item in list_material_items():
        kb.row(
            CallbackButton(
                text=f"{item.get('name')} ({item.get('unit')})",
                payload=f"{CB_MRC_RSV_MAT_PREFIX}{item['id']}",
            )
        )
    kb.row(CallbackButton(text="← Вагоны", payload=CB_MRC_RSV_BACK_WAGON))
    return kb


def _reserve_confirm_keyboard() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(
        CallbackButton(text="✅ Зарезервировать", payload=CB_MRC_RSV_CONFIRM),
        CallbackButton(text="✏️ Изменить кол-во", payload=CB_MRC_RSV_EDIT_QTY),
    )
    kb.row(CallbackButton(text="❌ Отмена", payload=CB_MRC_RSV_CANCEL))
    return kb


def _kit_confirm_keyboard() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(CallbackButton(text="✅ Зарезервировать комплект", payload=CB_MRC_RSV_CONFIRM))
    kb.row(CallbackButton(text="← Назад", payload=CB_MRC_RSV_BACK_WAGON))
    return kb


def _wagon_header(state: dict) -> str:
    zone = state.get("zone") or ""
    slot = state.get("slot_index") or "—"
    wagon = state.get("wagon_number") or "—"
    scheme = state.get("scheme_label") or ""
    scheme_text = _scheme_short(scheme) if scheme else "—"
    return f"{zone} · слот {slot} · вагон {wagon} · {scheme_text}"


def _material_snapshot(material_id: int) -> dict:
    from taksimo_store import get_material_item

    return get_material_item(material_id) or {}


def _kit_plan_lines(wagon_number: str) -> list[str]:
    from taksimo_store import get_wagon_materials, list_material_templates

    wagon = get_wagon_materials(wagon_number) or {}
    lines: list[str] = []
    items = wagon.get("items") or []
    shortages = [item for item in items if float(item.get("shortage_qty") or 0) > 0]
    if shortages:
        for item in shortages:
            lines.append(
                f"• {item.get('name')}: {_format_qty(item.get('shortage_qty'))} {item.get('unit')}"
            )
        return lines

    for tpl in list_material_templates():
        scheme = (tpl.get("scheme_code") or "").strip()
        name = (tpl.get("name") or "").lower()
        if scheme != "scheme1" and "схема 1" not in name and "scheme1" not in name:
            continue
        for row in tpl.get("items") or []:
            qty = float(row.get("qty_norm") or 0)
            if qty > 0:
                lines.append(
                    f"• {row.get('material_name') or row.get('name')}: "
                    f"{_format_qty(qty)} {row.get('material_unit') or row.get('unit') or ''}".strip()
                )
        if lines:
            return lines
    return ["• по нормам схемы 1"]


def _reserve_qty_prompt(state: dict) -> str:
    unit = state.get("unit") or ""
    available = state.get("available_before")
    tail = f"\nСвободно на складе: {_format_qty(available)} {unit}" if available is not None else ""
    shortage = state.get("shortage_qty")
    if shortage is not None and float(shortage) > 0:
        tail += f"\nНе хватает на вагоне: {_format_qty(shortage)} {unit}"
    return (
        f"Резерв под вагон\n{_wagon_header(state)}\n\n"
        f"Материал: {state.get('material_name') or '—'}\n"
        f"Введите количество ({unit}).{tail}"
    )


def _reserve_confirm_text(state: dict) -> str:
    unit = state.get("unit") or ""
    lines = [
        "Подтвердите резерв:",
        "",
        _wagon_header(state),
        f"Материал: {state.get('material_name') or '—'}",
        f"Резерв: +{_format_qty(state.get('qty'))} {unit}",
        f"Свободно: было {_format_qty(state.get('available_before'))} → "
        f"станет {_format_qty(state.get('available_after'))} {unit}",
        f"В резерве: было {_format_qty(state.get('reserved_before'))} → "
        f"станет {_format_qty(state.get('reserved_after'))} {unit}",
    ]
    return "\n".join(lines)


def _kit_confirm_text(state: dict) -> str:
    lines = [
        "Подтвердите резерв комплекта схемы 1:",
        "",
        _wagon_header(state),
        "",
        "Будет зарезервировано:",
    ]
    lines.extend(_kit_plan_lines(str(state.get("wagon_number") or "")) or ["• по нормам схемы 1"])
    return "\n".join(lines)


def _enrich_reserve_confirm(state: dict) -> None:
    material_id = int(state.get("material_id") or 0)
    qty = float(state.get("qty") or 0)
    item = _material_snapshot(material_id)
    available = float(item.get("available") or 0)
    reserved = float(item.get("reserved") or 0)
    if qty > available:
        raise ValueError(
            f"Недостаточно на складе: свободно {_format_qty(available)} {item.get('unit') or ''}"
        )
    state["available_before"] = available
    state["reserved_before"] = reserved
    state["available_after"] = round(available - qty, 3)
    state["reserved_after"] = round(reserved + qty, 3)


async def _notify_reserve(
    *,
    master_name: str,
    master_id: int,
    wagon_number: str,
    zone: str,
    slot_index,
    material_name: str,
    qty,
    unit: str,
    available_after,
    kit: bool = False,
) -> None:
    if _bot is None:
        return
    now = datetime.now(_tz()).strftime("%d.%m.%Y %H:%M")
    title = "📦 Резерв комплекта" if kit else "🔒 Резерв под вагон"
    text = "\n".join(
        [
            title,
            "",
            f"Вагон: {wagon_number}",
            f"Тупик: {zone} · слот {slot_index}",
            f"Материал: {material_name}",
            f"Резерв: +{_format_qty(qty)} {unit}".rstrip(),
            f"Свободно после резерва: {_format_qty(available_after)} {unit}".rstrip(),
            "",
            f"Мастер: {master_name} (id {master_id})",
            f"Время: {now}",
        ]
    )
    for supply_id in materials_supply_ids():
        try:
            await _bot.send_message(user_id=supply_id, text=text)
        except Exception:
            logger.exception("Не удалось уведомить снабжение о резерве user_id=%s", supply_id)
    try:
        from materials_chat import notify_event

        await notify_event(
            f"🟡 Резерв через MAX · вагон {wagon_number} · {material_name}: "
            f"+{_format_qty(qty)} {unit} · {master_name}"
        )
    except Exception:
        logger.exception("Не удалось отправить резерв в группу расходников")


def materials_supply_ids() -> set[int]:
    from materials_receipt_chat import materials_supply_ids as _ids

    return _ids()


async def _apply_material_reserve(*, user_id: int, user_name: str, state: dict) -> tuple[bool, str]:
    from taksimo_store import get_material_item, reserve_material_for_wagon

    material_id = int(state.get("material_id") or 0)
    qty = float(state.get("qty") or 0)
    wagon_number = str(state.get("wagon_number") or "")
    try:
        wagon = reserve_material_for_wagon(
            material_id,
            wagon_number=wagon_number,
            quantity=qty,
            operator=f"{user_name} ({user_id})",
            note="MAX · резерв",
        )
    except Exception as exc:
        logger.exception("Ошибка резерва material_id=%s wagon=%s", material_id, wagon_number)
        return False, f"Не удалось зарезервировать: {exc}"

    item = get_material_item(material_id) or {}
    material_name = item.get("name") or state.get("material_name") or "—"
    unit = item.get("unit") or state.get("unit") or ""
    await _notify_reserve(
        master_name=user_name,
        master_id=user_id,
        wagon_number=wagon_number,
        zone=str(state.get("zone") or ""),
        slot_index=state.get("slot_index") or "—",
        material_name=material_name,
        qty=qty,
        unit=unit,
        available_after=item.get("available"),
    )
    shortage = wagon.get("shortage_count") if wagon else 0
    tail = f"\nДефицит на вагоне: {shortage} поз." if shortage else "\nКомплект по этой позиции закрыт."
    return True, (
        "✅ Резерв записан.\n\n"
        f"{_wagon_header(state)}\n"
        f"{material_name}: +{_format_qty(qty)} {unit}\n"
        f"Свободно: {_format_qty(item.get('available'))} {unit}"
        f"{tail}"
    )


async def _apply_kit_reserve(*, user_id: int, user_name: str, state: dict) -> tuple[bool, str]:
    from taksimo_store import format_kit_reserve_report, reserve_scheme1_kit_for_wagon

    wagon_number = str(state.get("wagon_number") or "")
    try:
        wagon = reserve_scheme1_kit_for_wagon(
            wagon_number,
            operator=f"{user_name} ({user_id})",
            note="MAX · комплект схемы 1",
        )
    except Exception as exc:
        logger.exception("Ошибка резерва комплекта wagon=%s", wagon_number)
        return False, f"Не удалось зарезервировать комплект: {exc}"

    kit_reserve = (wagon or {}).get("kit_reserve") or {}
    reserved_count = len(kit_reserve.get("reserved") or [])
    skipped_count = len(kit_reserve.get("skipped") or [])
    partial_count = len(kit_reserve.get("partial") or [])

    if reserved_count:
        await _notify_reserve(
            master_name=user_name,
            master_id=user_id,
            wagon_number=wagon_number,
            zone=str(state.get("zone") or ""),
            slot_index=state.get("slot_index") or "—",
            material_name="комплект схемы 1",
            qty="—",
            unit="",
            available_after="—",
            kit=True,
        )

    report_lines = format_kit_reserve_report(kit_reserve)
    shortage = wagon.get("shortage_count") if wagon else 0
    if not reserved_count:
        title = "⚠️ Комплект не зарезервирован"
        tail = "\nНа складе нет свободных остатков по нужным позициям."
    elif skipped_count or partial_count:
        title = "✅ Частичный резерв комплекта"
        tail = f"\nОстался дефицит на вагоне: {shortage} поз." if shortage else ""
    else:
        title = "✅ Комплект схемы 1 зарезервирован"
        tail = f"\nОстался дефицит: {shortage} поз." if shortage else "\nКомплект зарезервирован."

    body = "\n".join(report_lines) if report_lines else "Нечего резервировать — комплект уже закрыт."
    return True, (
        f"{title}\n\n"
        f"{_wagon_header(state)}\n\n"
        f"{body}{tail}"
    )


async def send_materials_reserve_menu(*, user_id: int) -> None:
    if _bot is None:
        return
    _set_user_state(user_id, {"flow": FLOW_RESERVE, "step": _STEP_RSV_SEARCH})
    await _bot.send_message(
        user_id=user_id,
        text=_wagon_search_prompt_text(),
        attachments=[_wagon_search_menu_keyboard().as_markup()],
    )


async def handle_materials_reserve_callback(event: MessageCallback, bot: Bot) -> bool:
    payload = event.callback.payload if event.callback else None
    reserve_payloads = {
        CB_MRC_RESERVE,
        CB_MRC_RSV_KIT,
        CB_MRC_RSV_CONFIRM,
        CB_MRC_RSV_CANCEL,
        CB_MRC_RSV_EDIT_QTY,
        CB_MRC_RSV_BACK_WAGON,
    }
    if not payload or (
        payload not in reserve_payloads
        and not payload.startswith(CB_MRC_RSV_WAGON_PREFIX)
        and not payload.startswith(CB_MRC_RSV_MAT_PREFIX)
    ):
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

    if payload == CB_MRC_RESERVE:
        _set_user_state(user_id, {"flow": FLOW_RESERVE, "step": _STEP_RSV_SEARCH})
        await event.answer(notification="Введите вагон")
        await event.edit(
            text=_wagon_search_prompt_text(),
            attachments=[_wagon_search_menu_keyboard().as_markup()],
        )
        return True

    state = _get_user_state(user_id) or {}

    if payload == CB_MRC_RSV_CANCEL:
        _set_user_state(user_id, None)
        await event.answer(notification="Отменено")
        from materials_receipt_chat import _materials_menu_text

        await event.edit(
            text="Резерв отменён.",
            attachments=[_materials_menu_keyboard().as_markup()],
        )
        return True

    if payload == CB_MRC_RSV_BACK_WAGON:
        _set_user_state(user_id, {"flow": FLOW_RESERVE, "step": _STEP_RSV_SEARCH})
        await event.answer(notification="Введите вагон")
        await event.edit(
            text=_wagon_search_prompt_text(),
            attachments=[_wagon_search_menu_keyboard().as_markup()],
        )
        return True

    if payload.startswith(CB_MRC_RSV_WAGON_PREFIX):
        wagon_number = payload[len(CB_MRC_RSV_WAGON_PREFIX) :]
        wagon_meta = next(
            (w for w in _occupied_wagons() if w["wagon_number"] == wagon_number),
            None,
        )
        if wagon_meta is None:
            await event.answer(notification="Вагон не найден")
            return True
        state = _wagon_state_from_meta(wagon_meta)
        _set_user_state(user_id, state)
        await event.answer(notification=wagon_number)
        await event.edit(
            text=_wagon_materials_prompt(state),
            attachments=[
                _reserve_materials_keyboard(scheme_code=str(wagon_meta["scheme_code"])).as_markup()
            ],
        )
        return True

    if payload.startswith(CB_MRC_RSV_MAT_PREFIX):
        if not state.get("wagon_number"):
            await event.answer(notification="Сначала выберите вагон")
            return True
        material_id = int(payload[len(CB_MRC_RSV_MAT_PREFIX) :])
        item = _material_snapshot(material_id)
        if not item:
            await event.answer(notification="Материал не найден")
            return True
        from taksimo_store import get_wagon_materials

        wagon = get_wagon_materials(str(state["wagon_number"])) or {}
        shortage_qty = 0.0
        for row in wagon.get("items") or []:
            if int(row.get("material_id") or row.get("id") or 0) == material_id:
                shortage_qty = float(row.get("shortage_qty") or 0)
                break
        state.update(
            {
                "flow": FLOW_RESERVE,
                "mode": "material",
                "material_id": material_id,
                "material_name": item.get("name") or "",
                "unit": item.get("unit") or "",
                "step": _STEP_RSV_QTY,
                "shortage_qty": shortage_qty,
                "available_before": float(item.get("available") or 0),
            }
        )
        _set_user_state(user_id, state)
        await event.answer(notification=item.get("name") or "материал")
        await event.edit(text=_reserve_qty_prompt(state), attachments=[])
        return True

    if payload == CB_MRC_RSV_KIT:
        if not state.get("wagon_number"):
            await event.answer(notification="Сначала выберите вагон")
            return True
        if str(state.get("scheme_code") or "") != "scheme1":
            await event.answer(notification="Комплект только для схемы 1")
            return True
        state["flow"] = FLOW_RESERVE
        state["mode"] = "kit"
        state["step"] = _STEP_RSV_KIT_CONFIRM
        _set_user_state(user_id, state)
        await event.answer(notification="Комплект схемы 1")
        await event.edit(
            text=_kit_confirm_text(state),
            attachments=[_kit_confirm_keyboard().as_markup()],
        )
        return True

    if payload == CB_MRC_RSV_EDIT_QTY:
        if state.get("mode") != "material":
            await event.answer(notification="Нечего менять")
            return True
        state["step"] = _STEP_RSV_QTY
        _set_user_state(user_id, state)
        await event.answer(notification="Введите количество")
        await event.edit(text=_reserve_qty_prompt(state), attachments=[])
        return True

    if payload == CB_MRC_RSV_CONFIRM:
        step = state.get("step")
        if step == _STEP_RSV_KIT_CONFIRM and state.get("mode") == "kit":
            ok, message = await _apply_kit_reserve(
                user_id=user_id, user_name=user_name, state=state
            )
        elif step == _STEP_RSV_CONFIRM and state.get("mode") == "material":
            ok, message = await _apply_material_reserve(
                user_id=user_id, user_name=user_name, state=state
            )
        else:
            await event.answer(notification="Нет данных для резерва")
            return True
        _set_user_state(user_id, None)
        await event.answer(notification="Зарезервировано" if ok else "Ошибка")
        attachments = [_materials_menu_keyboard().as_markup()] if ok else []
        await event.edit(text=message, attachments=attachments)
        return True

    return False


async def handle_materials_reserve_message(event: MessageCreated, bot: Bot) -> bool:
    if not is_private_dialog(event.message):
        return False
    sender = event.message.sender
    if not sender or sender.is_bot:
        return False
    if not is_materials_master(sender.user_id):
        return False

    state = _get_user_state(sender.user_id)
    if not state or state.get("flow") != FLOW_RESERVE:
        return False

    body = event.message.body
    text = body.text.strip() if body and body.text else ""
    if not text or text.startswith("/"):
        return False

    step = state.get("step")

    if step == _STEP_RSV_SEARCH:
        matches = _match_wagons_by_query(text)
        if not matches:
            await event.message.answer(
                _wagon_search_prompt_text(query=text),
                attachments=[_wagon_search_menu_keyboard().as_markup()],
            )
            return True
        if len(matches) == 1:
            wagon_meta = matches[0]
            state = _wagon_state_from_meta(wagon_meta)
            _set_user_state(sender.user_id, state)
            await event.message.answer(
                _wagon_materials_prompt(state),
                attachments=[
                    _reserve_materials_keyboard(
                        scheme_code=str(wagon_meta["scheme_code"])
                    ).as_markup()
                ],
            )
            return True
        if len(matches) > 12:
            await event.message.answer(
                _wagon_search_prompt_text(query=text, too_many=len(matches)),
                attachments=[_wagon_search_menu_keyboard().as_markup()],
            )
            return True
        await event.message.answer(
            f"Найдено вагонов: {len(matches)}\n\nВыберите вагон:",
            attachments=[_wagon_pick_keyboard_filtered(matches).as_markup()],
        )
        return True

    if step != _STEP_RSV_QTY or state.get("mode") != "material":
        return False

    qty = _parse_quantity(text)
    if qty is None:
        await event.message.answer(
            f"Введите количество ({state.get('unit') or ''}) одним числом больше нуля."
        )
        return True

    state["qty"] = qty
    state["step"] = _STEP_RSV_CONFIRM
    try:
        _enrich_reserve_confirm(state)
    except ValueError as exc:
        await event.message.answer(str(exc))
        return True
    _set_user_state(sender.user_id, state)
    await event.message.answer(
        _reserve_confirm_text(state),
        attachments=[_reserve_confirm_keyboard().as_markup()],
    )
    return True
