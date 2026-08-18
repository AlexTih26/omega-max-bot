"""Чат расходников мастера: сводки, флаги и контроль по тупикам."""

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
from maxapi.types import MessageCreated

logger = logging.getLogger(__name__)

MATERIALS_WELCOME = (
    "✅ Чат расходников подключён.\n"
    "Здесь будут остатки, дефицит и сводки по материалам.\n"
)

MATERIALS_USER_WELCOME = (
    "{name}, добро пожаловать в чат расходников.\n"
    "Здесь будут остатки, дефицит и сводки по материалам."
)

_MAX_MSG_LEN = 3500
_STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "materials_chat_state.json"
_bot: Bot | None = None


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


def _report_times() -> list[tuple[int, int]]:
    raw = (os.getenv("MATERIALS_REPORT_HOURS") or "9,14,18").strip()
    minute_raw = (os.getenv("MATERIALS_REPORT_MINUTE") or "0").strip()
    try:
        minute = max(0, min(59, int(minute_raw)))
    except ValueError:
        minute = 0
    out: list[tuple[int, int]] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            hour = int(part)
        except ValueError:
            continue
        if 0 <= hour <= 23:
            out.append((hour, minute))
    return out or [(9, 0), (14, 0), (18, 0)]


def _load_state() -> dict:
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        logger.exception("Не удалось сохранить состояние материалов чата")


def _user_display_name(user) -> str:
    first = (getattr(user, "first_name", None) or "").strip()
    if first:
        return first
    username = (getattr(user, "username", None) or "").strip()
    if username:
        return username
    return "коллега"


def format_materials_user_welcome(user) -> str:
    return MATERIALS_USER_WELCOME.format(name=_user_display_name(user))


def materials_chat_id() -> int | None:
    raw = (os.getenv("MATERIALS_CHAT_ID") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning("MATERIALS_CHAT_ID не число: %s", raw)
        return None


def is_materials_chat(chat_id: int | None) -> bool:
    cid = materials_chat_id()
    return chat_id is not None and cid is not None and chat_id == cid


def set_bot(bot: Bot) -> None:
    global _bot
    _bot = bot


def _split_messages(text: str) -> list[str]:
    if len(text) <= _MAX_MSG_LEN:
        return [text]
    parts: list[str] = []
    current = ""
    for block in text.split("\n\n"):
        chunk = block if not current else current + "\n\n" + block
        if len(chunk) > _MAX_MSG_LEN and current:
            parts.append(current.strip())
            current = block
        elif len(block) > _MAX_MSG_LEN:
            if current:
                parts.append(current.strip())
                current = ""
            for i in range(0, len(block), _MAX_MSG_LEN):
                parts.append(block[i : i + _MAX_MSG_LEN])
        else:
            current = chunk
    if current.strip():
        parts.append(current.strip())
    return parts


async def send_text(text: str) -> bool:
    chat_id = materials_chat_id()
    if chat_id is None:
        logger.warning("Материалы: MATERIALS_CHAT_ID не задан")
        return False
    if _bot is None:
        logger.warning("Материалы: бот не инициализирован")
        return False
    try:
        await _bot.send_message(chat_id=chat_id, text=text)
        return True
    except Exception:
        logger.exception("Материалы: не удалось отправить в chat_id=%s", chat_id)
        return False


async def send_messages(texts: list[str]) -> bool:
    if not texts:
        return False
    ok = True
    for text in texts:
        if not await send_text(text):
            ok = False
        if len(texts) > 1:
            await asyncio.sleep(0.3)
    return ok


def _format_qty(value) -> str:
    try:
        num = float(value or 0)
    except (TypeError, ValueError):
        return "0"
    if abs(num - round(num)) < 0.001:
        return str(int(round(num)))
    return f"{num:.3f}".rstrip("0").rstrip(".")


def _material_icon(item: dict) -> str:
    if float(item.get("shortage_in_slots") or 0) > 0:
        return "🔴"
    if item.get("low_stock"):
        return "🟡"
    return "🟢"


def _wagon_icon(wagon: dict) -> str:
    if wagon.get("has_shortage"):
        return "🔴"
    if wagon.get("needs_setup") or wagon.get("skol_count"):
        return "🟡"
    return "🟢"


def _scheme_label(raw: str) -> str:
    name = (raw or "").strip()
    if not name:
        return "схема не назначена"
    match = re.search(r"(\d+)", name)
    if match:
        return f"схема №{match.group(1)}"
    return name


def _collect_context() -> dict:
    from taksimo_store import get_wagon_materials, materials_dashboard, wagon_plan

    dashboard = materials_dashboard()
    plan = wagon_plan()
    materials = dashboard.get("materials") or []
    zones_out: list[dict] = []
    slot_wagons_total = 0
    wagons_with_shortage = 0
    wagons_with_skol = 0
    demand_map: dict[int, dict] = {}

    for zone in ("ТУРАН", "ГРУЗОВОЙ"):
        zone_slots = (plan.get("dead_ends") or {}).get(zone) or []
        wagons_out: list[dict] = []
        zone_need_count = 0
        zone_skol_count = 0
        for slot in zone_slots:
            wagon_number = (slot.get("wagon_number") or "").strip()
            if not wagon_number:
                continue
            slot_wagons_total += 1
            wagon = get_wagon_materials(wagon_number) or {}
            items = wagon.get("items") or []
            prep = wagon.get("prep") or {}
            needs_setup = not bool((prep.get("template_name") or "").strip())
            shortage_lines: list[str] = []
            for item in items:
                material_id = int(item.get("id") or 0)
                demand = demand_map.setdefault(
                    material_id,
                    {
                        "name": item.get("name") or "",
                        "unit": item.get("unit") or "",
                        "norm": 0.0,
                        "reserved": 0.0,
                        "shortage": 0.0,
                    },
                )
                if not needs_setup:
                    demand["norm"] += float(item.get("norm_per_wagon") or 0)
                    demand["reserved"] += float(item.get("reserved_qty") or 0)
                    demand["shortage"] += float(item.get("shortage_qty") or 0)
                if not needs_setup and float(item.get("shortage_qty") or 0) > 0:
                    shortage_lines.append(
                        f"{item.get('name')} { _format_qty(item.get('shortage_qty')) } {item.get('unit')}".replace("  ", " ")
                    )
            skol_slabs = [
                slab
                for slab in (slot.get("slabs") or [])
                if (slab.get("suffix") or "").strip().lower() == "скол"
            ]
            if shortage_lines:
                wagons_with_shortage += 1
                zone_need_count += 1
            if skol_slabs:
                wagons_with_skol += 1
                zone_skol_count += 1
            wagons_out.append(
                {
                    "slot_index": int(slot.get("slot_index") or 0),
                    "wagon_number": wagon_number,
                    "scheme_code": slot.get("scheme_code") or "",
                    "scheme_label": slot.get("scheme_label") or "",
                    "template_name": prep.get("template_name") or "",
                    "needs_setup": needs_setup,
                    "shortage_lines": shortage_lines,
                    "has_shortage": bool(shortage_lines),
                    "skol_labels": [slab.get("label") or "плита" for slab in skol_slabs],
                    "skol_count": len(skol_slabs),
                    "slab_count": int(slot.get("slab_count") or 0),
                    "is_complete": bool(slot.get("is_complete")),
                }
            )
        zones_out.append(
            {
                "name": zone,
                "wagon_count": len(wagons_out),
                "need_count": zone_need_count,
                "skol_count": zone_skol_count,
                "wagons": wagons_out,
            }
        )

    material_lines = []
    urgent_materials = []
    material_map = {int(item.get("id") or 0): item for item in materials}
    for item in materials:
        material_id = int(item.get("id") or 0)
        demand = demand_map.get(
            material_id,
            {"norm": 0.0, "reserved": 0.0, "shortage": 0.0},
        )
        line = {
            "id": material_id,
            "name": item.get("name") or "",
            "unit": item.get("unit") or "",
            "on_hand": float(item.get("on_hand") or 0),
            "reserved": float(item.get("reserved") or 0),
            "available": float(item.get("available") or 0),
            "min_level": float(item.get("min_level") or 0),
            "available_wagons": item.get("available_wagons"),
            "low_stock": bool(item.get("low_stock")),
            "need_in_slots": round(float(demand.get("norm") or 0), 3),
            "reserved_in_slots": round(float(demand.get("reserved") or 0), 3),
            "shortage_in_slots": round(float(demand.get("shortage") or 0), 3),
        }
        material_lines.append(line)
        if line["low_stock"] or line["shortage_in_slots"] > 0:
            urgent_materials.append(line)

    return {
        "dashboard": dashboard,
        "zones": zones_out,
        "materials": material_lines,
        "urgent_materials": urgent_materials,
        "slot_wagons_total": slot_wagons_total,
        "wagons_with_shortage": wagons_with_shortage,
        "wagons_with_skol": wagons_with_skol,
    }


def _format_zone_line(wagon: dict) -> str:
    bits = [f"вагон {wagon['wagon_number']}"]
    scheme = (wagon.get("scheme_label") or "").strip()
    if not scheme and wagon.get("template_name"):
        scheme = _scheme_label(wagon["template_name"])
    if scheme:
        bits.append(scheme if scheme.startswith("схема") else _scheme_label(scheme))
    else:
        bits.append("схема не назначена")
    bits.append(f"слот {wagon['slot_index']}")
    if wagon.get("needs_setup"):
        bits.append("комплект ещё не настроен админом")
    elif wagon.get("has_shortage"):
        bits.append("не хватает: " + ", ".join(wagon.get("shortage_lines") or []))
    else:
        bits.append("комплект закрыт")
    if wagon.get("skol_count"):
        bits.append(f"проверить скол: {wagon['skol_count']}")
    return _wagon_icon(wagon) + " " + " · ".join(bits)


def format_materials_report() -> list[str]:
    ctx = _collect_context()
    now = datetime.now(_tz()).strftime("%d.%m.%Y %H:%M")
    summary = (ctx["dashboard"].get("summary") or {})
    scheme_summary = (ctx["dashboard"].get("scheme_summary") or {})
    return_queue = (ctx["dashboard"].get("return_queue") or [])
    lines = [
        f"🧱 Материалы · {now}",
        "",
        f"В слотах вагонов: {ctx['slot_wagons_total']}",
        f"🔴 С дефицитом: {ctx['wagons_with_shortage']}",
        f"🟡 Со 'скол': {ctx['wagons_with_skol']}",
        f"🟢 Готовы по комплекту: {summary.get('ready_wagons', 0)}",
        f"Хватит ещё на вагонов: {summary.get('overall_wagons_left', '—')}",
        f"K по истории: {summary.get('historical_k_total', 0)}",
        f"Допов A–F по истории: {summary.get('historical_extra_units', 0)}",
        f"Возвратов по ящикам в работе: {summary.get('returning_box_wagons', 0)}",
        "",
        "Остатки по материалам:",
    ]
    for item in ctx["materials"]:
        marker = " · ниже минимума" if item["low_stock"] else ""
        need_tail = ""
        if item["need_in_slots"] > 0 or item["shortage_in_slots"] > 0:
            need_tail = (
                f" · нужно в слотах { _format_qty(item['need_in_slots']) } {item['unit']}"
                f" · дефицит { _format_qty(item['shortage_in_slots']) } {item['unit']}"
            )
        lines.append(
            _material_icon(item) + " "
            + f"{item['name']} — факт { _format_qty(item['on_hand']) } {item['unit']}"
            + f" · резерв { _format_qty(item['reserved']) } {item['unit']}"
            + f" · свободно { _format_qty(item['available']) } {item['unit']}"
            + f" · минимум { _format_qty(item['min_level']) } {item['unit']}"
            + marker
            + need_tail
        )
    for zone in ctx["zones"]:
        lines.extend(
            [
                "",
                f"📍 {zone['name']}: вагонов {zone['wagon_count']} · нуждаются в материалах {zone['need_count']} · со 'скол' {zone['skol_count']}",
            ]
        )
        if zone["wagons"]:
            for wagon in zone["wagons"]:
                lines.append(_format_zone_line(wagon))
        else:
            lines.append("🟢 пусто")
    if scheme_summary:
        lines.extend(
            [
                "",
                "📦 Циклы схем:",
                "• назначено: С1 {} · С2 {} · С3 {}".format(
                    (scheme_summary.get("assigned") or {}).get("scheme1", 0),
                    (scheme_summary.get("assigned") or {}).get("scheme2", 0),
                    (scheme_summary.get("assigned") or {}).get("scheme3", 0),
                ),
                "• ушло по схеме 2: {} · K накоплено: {}".format(
                    (scheme_summary.get("dispatched") or {}).get("scheme2", 0),
                    scheme_summary.get("historical_k_total", 0),
                ),
                "• допов A–F по истории: {}".format(
                    scheme_summary.get("historical_extra_units", 0)
                ),
            ]
        )
    if return_queue:
        lines.extend(["", "📥 Возвраты по ящикам:"])
        for item in return_queue[:8]:
            lines.append(
                "• вагон {} · {} · откуда {} · куда {} · {}".format(
                    item.get("wagon_number") or "—",
                    item.get("template_name") or "схема",
                    item.get("origin_zone") or "—",
                    item.get("return_target_zone") or item.get("origin_zone") or "—",
                    item.get("stage_label") or "—",
                )
            )
    skol_lines = []
    for zone in ctx["zones"]:
        for wagon in zone["wagons"]:
            if wagon["skol_count"]:
                skol_lines.append(
                    f"• {zone['name']} · вагон {wagon['wagon_number']} · плит со 'скол': {wagon['skol_count']}"
                )
    if skol_lines:
        lines.extend(["", "🟡 Проверить вагоны по 'скол':"])
        lines.extend(skol_lines)
    return _split_messages("\n".join(lines).strip())


def format_materials_alerts() -> list[str]:
    ctx = _collect_context()
    blocks: list[str] = []
    urgent_materials = ctx["urgent_materials"]
    if urgent_materials:
        lines = ["🔴 Срочно по материалам", ""]
        for item in urgent_materials:
            tail = []
            if item["low_stock"]:
                tail.append(
                    f"ниже минимума ({_format_qty(item['available'])}/{_format_qty(item['min_level'])} {item['unit']})"
                )
            if item["shortage_in_slots"] > 0:
                tail.append(
                    f"не хватает { _format_qty(item['shortage_in_slots']) } {item['unit']} на вагоны в слотах"
                )
            lines.append(
                "🔴 "
                + f"{item['name']} — факт { _format_qty(item['on_hand']) } {item['unit']}"
                + f", резерв { _format_qty(item['reserved']) } {item['unit']}"
                + f", свободно { _format_qty(item['available']) } {item['unit']}"
                + (" · " + " ; ".join(tail) if tail else "")
            )
        blocks.append("\n".join(lines))

    shortage_lines: list[str] = []
    for zone in ctx["zones"]:
        for wagon in zone["wagons"]:
            if wagon["has_shortage"]:
                shortage_lines.append(
                    f"🔴 {zone['name']} · слот {wagon['slot_index']} · вагон {wagon['wagon_number']} — "
                    + ", ".join(wagon["shortage_lines"])
                )
    if shortage_lines:
        blocks.append(
            "\n".join(["🔴 Какие тупики нуждаются в материалах", ""] + shortage_lines)
        )

    skol_lines: list[str] = []
    for zone in ctx["zones"]:
        for wagon in zone["wagons"]:
            if wagon["skol_count"]:
                skol_lines.append(
                    f"🟡 {zone['name']} · слот {wagon['slot_index']} · вагон {wagon['wagon_number']} — проверить 'скол' ({wagon['skol_count']})"
                )
    if skol_lines:
        blocks.append(
            "\n".join(["🟡 Проверить вагоны со 'скол'", ""] + skol_lines)
        )

    return_queue = (ctx["dashboard"].get("return_queue") or [])
    return_lines = []
    for item in return_queue:
        status = item.get("return_status") or ""
        if status in ("planned_return", "in_transit_back"):
            return_lines.append(
                "🟡 вагон {} · {} · возврат {} -> {}".format(
                    item.get("wagon_number") or "—",
                    item.get("template_name") or "схема",
                    item.get("origin_zone") or "—",
                    item.get("return_target_zone") or item.get("origin_zone") or "—",
                )
            )
    if return_lines:
        blocks.append(
            "\n".join(["🟡 Ожидаются возвраты материалов", ""] + return_lines[:8])
        )

    return blocks


def _wagon_lookup(ctx: dict) -> dict[tuple[str, int], dict]:
    out: dict[tuple[str, int], dict] = {}
    for zone in ctx.get("zones") or []:
        zone_name = zone.get("name") or ""
        for wagon in zone.get("wagons") or []:
            out[(zone_name, int(wagon.get("slot_index") or 0))] = wagon
    return out


def _format_stock_slot_line(*, slot: dict, wagon_info: dict | None) -> str:
    slot_index = int(slot.get("slot_index") or 0)
    wagon_number = (slot.get("wagon_number") or "").strip()
    if not wagon_number:
        return f"   слот {slot_index} — пусто"

    info = wagon_info or {}
    scheme = (slot.get("scheme_label") or info.get("scheme_label") or "").strip()
    if not scheme and info.get("template_name"):
        scheme = _scheme_label(info["template_name"])
    elif scheme and re.search(r"\d", scheme):
        scheme = _scheme_label(scheme)
    if not scheme:
        scheme = "схема не назначена"
    icon = _wagon_icon(info) if info else "🟢"
    slab_count = int(slot.get("slab_count") or info.get("slab_count") or 0)
    bits = [f"слот {slot_index}", f"вагон {wagon_number}", scheme]
    if slab_count:
        bits.append(f"{slab_count} плит")
    return f"   {icon} " + " · ".join(bits)


def format_materials_stock_brief() -> list[str]:
    from taksimo_store import wagon_plan

    ctx = _collect_context()
    plan = wagon_plan()
    wagon_lookup = _wagon_lookup(ctx)
    now = datetime.now(_tz()).strftime("%d.%m.%Y %H:%M")
    lines = [f"📦 На складе · {now}", "", "Остаток склада:"]
    materials = ctx.get("materials") or []
    if not materials:
        lines.append("   нет материалов в справочнике")
    else:
        for item in materials:
            marker = _material_icon(item)
            low = " · ниже минимума" if item.get("low_stock") else ""
            lines.append(
                f"{marker} {item['name']}\n"
                f"   факт {_format_qty(item['on_hand'])} {item['unit']}"
                f" · резерв {_format_qty(item['reserved'])} {item['unit']}"
                f" · свободно {_format_qty(item['available'])} {item['unit']}"
                f" · мин. {_format_qty(item['min_level'])} {item['unit']}"
                f"{low}"
            )

    for zone_name in ("ТУРАН", "ГРУЗОВОЙ"):
        zone_slots = (plan.get("dead_ends") or {}).get(zone_name) or []
        occupied_slots = [
            slot
            for slot in zone_slots
            if (slot.get("wagon_number") or "").strip()
        ]
        lines.extend(["", f"📍 {zone_name} · в слотах ({len(occupied_slots)}):"])
        if not occupied_slots:
            lines.append("   нет вагонов")
            continue
        for slot in sorted(
            occupied_slots, key=lambda row: int(row.get("slot_index") or 0)
        ):
            slot_index = int(slot.get("slot_index") or 0)
            wagon_info = wagon_lookup.get((zone_name, slot_index))
            lines.append(_format_stock_slot_line(slot=slot, wagon_info=wagon_info))

    summary = (ctx["dashboard"].get("summary") or {})
    lines.extend(
        [
            "",
            f"Всего вагонов в слотах: {ctx.get('slot_wagons_total', 0)}",
            f"Хватит материалов ещё на вагонов: {summary.get('overall_wagons_left', '—')}",
        ]
    )
    return _split_messages("\n".join(lines).strip())


def format_materials_need_brief() -> list[str]:
    ctx = _collect_context()
    now = datetime.now(_tz()).strftime("%d.%m.%Y %H:%M")
    blocks: list[str] = []

    urgent = ctx.get("urgent_materials") or []
    if urgent:
        lines = [f"🔴 Что надо · {now}", "", "По материалам:"]
        for item in urgent:
            tail: list[str] = []
            if item.get("low_stock"):
                tail.append(
                    f"ниже минимума (свободно {_format_qty(item['available'])}"
                    f" / мин. {_format_qty(item['min_level'])} {item['unit']})"
                )
            if float(item.get("shortage_in_slots") or 0) > 0:
                tail.append(
                    f"не хватает {_format_qty(item['shortage_in_slots'])} {item['unit']} на вагоны в слотах"
                )
            if float(item.get("need_in_slots") or 0) > 0:
                tail.append(
                    f"нужно в слотах {_format_qty(item['need_in_slots'])} {item['unit']}"
                )
            lines.append(
                f"🔴 {item['name']} — "
                + (" · ".join(tail) if tail else "проверить")
            )
        blocks.append("\n".join(lines))

    shortage_lines: list[str] = []
    for zone in ctx.get("zones") or []:
        for wagon in zone.get("wagons") or []:
            if wagon.get("has_shortage"):
                shortage_lines.append(
                    f"🔴 {zone['name']} · слот {wagon['slot_index']} · "
                    f"вагон {wagon['wagon_number']} — "
                    + ", ".join(wagon.get("shortage_lines") or [])
                )
    if shortage_lines:
        blocks.append(
            "\n".join(["🔴 По вагонам в слотах:", ""] + shortage_lines)
        )

    if not blocks:
        return [
            f"🟢 Что надо · {now}\n\n"
            "Дефицита нет — по материалам и вагонам в слотах всё закрыто."
        ]

    return _split_messages("\n\n".join(blocks))


async def send_materials_welcome(bot: Bot, *, chat_id: int) -> None:
    try:
        await bot.send_message(chat_id=chat_id, text=MATERIALS_WELCOME)
    except Exception:
        logger.exception("Не удалось отправить приветствие материалов chat_id=%s", chat_id)


async def send_materials_user_welcome(bot: Bot, *, chat_id: int, user) -> None:
    if getattr(user, "is_bot", False):
        return
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=format_materials_user_welcome(user),
        )
    except Exception:
        logger.exception(
            "Не удалось приветствовать пользователя chat_id=%s user_id=%s",
            chat_id,
            getattr(user, "user_id", None),
        )


async def send_report_now() -> bool:
    return await send_messages(format_materials_report())


async def send_alerts_now() -> bool:
    return await send_messages(format_materials_alerts())


async def notify_event(text: str) -> bool:
    if not text.strip():
        return False
    return await send_text(text.strip())


async def materials_report_loop() -> None:
    chat_id = materials_chat_id()
    if chat_id is None:
        return
    schedule = _report_times()
    while True:
        await asyncio.sleep(45)
        if _bot is None:
            continue
        now = datetime.now(_tz())
        current_hm = (now.hour, now.minute)
        if current_hm not in schedule:
            continue
        state = _load_state()
        sent = state.get("sent_reports") or {}
        report_key = now.strftime("%Y-%m-%d %H:%M")
        if sent.get(report_key):
            continue
        if await send_report_now():
            sent[report_key] = True
            state["sent_reports"] = sent
            _save_state(state)
            logger.info("Материалы: плановая сводка отправлена key=%s", report_key)


async def materials_watch_loop() -> None:
    chat_id = materials_chat_id()
    if chat_id is None:
        return
    while True:
        await asyncio.sleep(45)
        if _bot is None:
            continue
        try:
            alerts = format_materials_alerts()
            signature = json.dumps(alerts, ensure_ascii=False)
            state = _load_state()
            previous = state.get("last_alert_signature") or ""
            if alerts and signature != previous:
                if await send_messages(alerts):
                    state["last_alert_signature"] = signature
                    state["last_alert_at"] = int(datetime.now(_tz()).timestamp())
                    _save_state(state)
                    logger.info("Материалы: отправлены флаги")
            elif not alerts and previous:
                state["last_alert_signature"] = ""
                _save_state(state)
        except Exception:
            logger.exception("Материалы: ошибка цикла флагов")


async def handle_materials_chat_message(event: MessageCreated) -> None:
    body = event.message.body
    text = body.text.strip() if body and body.text else ""
    if not text or not text.startswith("/"):
        return
    if text.startswith("/materials_chat") or text.startswith("/taksimo_chat"):
        chat_id = event.message.recipient.chat_id
        if chat_id is not None:
            await event.message.answer(
                "Чат расходников:\n\n"
                f"MATERIALS_CHAT_ID={chat_id}\n\n"
                "Добавьте в .env и перезапустите бота."
            )
        return
    if text.startswith("/materials_now") or text.startswith("/materials_report"):
        await send_report_now()
        return
    if text.startswith("/materials_alerts"):
        await send_alerts_now()
        return
