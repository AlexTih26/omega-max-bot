"""Уведомления Таксимо в чат MAX."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from maxapi import Bot

from keyboards import taksimo_menu_attachments
from taksimo_store import (
    MAX_WAGON_SLABS,
    WAGON_ZONES,
    get_session,
    get_wagon_load_info,
    list_sessions_for_date,
    list_wagon_loads_for_daily_report,
    mark_session_completed_notified,
    mark_session_departure_notified,
    mark_session_started_notified,
    stats_for_date,
    yard_stats,
)
from taksimo_time import format_session_completion, report_tz, site_tz_label

logger = logging.getLogger(__name__)

_bot: Bot | None = None
_STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "taksimo_notify_state.json"
_MAX_MSG_LEN = 3500
_DEPARTURE_DELAY_SEC = 300
_departure_tasks: dict[int, asyncio.Task] = {}


def set_bot(bot: Bot) -> None:
    global _bot
    _bot = bot


def notify_chat_id() -> int | None:
    raw = (os.getenv("TAKSIMO_NOTIFY_CHAT_ID") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning("TAKSIMO_NOTIFY_CHAT_ID не число: %s", raw)
        return None


def _tz() -> ZoneInfo:
    return report_tz()


def _report_time() -> tuple[int, int]:
    hour = int(os.getenv("TAKSIMO_REPORT_HOUR", "17"))
    minute = int(os.getenv("TAKSIMO_REPORT_MINUTE", "0"))
    return hour % 24, minute % 60


def _load_state() -> dict:
    if not _STATE_PATH.is_file():
        return {}
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _date_label(unload_date: str) -> str:
    try:
        return date.fromisoformat(unload_date).strftime("%d.%m.%Y")
    except ValueError:
        return unload_date


def _msk_now_label() -> str:
    return datetime.now(_tz()).strftime("%H:%M")


def _ru_plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    mod100 = n % 100
    mod10 = n % 10
    if 11 <= mod100 <= 19:
        return many
    if mod10 == 1:
        return one
    if 2 <= mod10 <= 4:
        return few
    return many


def _slab_short(slab: dict) -> str:
    if slab.get("placed") or (
        slab.get("pos_x", 0) > 0 and slab.get("pos_y", 0) > 0
    ):
        place = f"{slab['pos_x']}/{slab['pos_y']}"
    else:
        place = slab.get("place") or "—"
    suffix = (slab.get("suffix") or "").strip()
    if suffix:
        place += suffix
    return f"{slab['letter']}{slab['number']} {place}"


def _vehicle_plate(session: dict) -> str:
    vehicle = session.get("vehicle") or {}
    return (vehicle.get("plate") or "").strip()


def _driver_name(session: dict) -> str:
    vehicle = session.get("vehicle") or {}
    return (session.get("driver") or vehicle.get("driver") or "").strip()


def format_started_message(session: dict) -> str:
    plate = _vehicle_plate(session)
    crane_start = (session.get("crane_start") or "").strip()
    time_part = f" · кран с {crane_start}" if crane_start else ""
    if plate:
        return f"Начали приём {plate.lower()}{time_part}"
    return f"Начали приём{time_part}"


def format_complete_message(session: dict) -> str:
    slabs = session.get("slabs") or []
    count = len(slabs)
    lines = [
        "✅ Приём завершён · Таксимо",
        f"Дата: {_date_label(session.get('unload_date') or '')}",
        f"{count} {_ru_plural(count, 'блок', 'блока', 'блоков')}: "
        + ", ".join(_slab_short(s) for s in slabs),
    ]

    plate = _vehicle_plate(session)
    driver = _driver_name(session)
    if driver and plate:
        lines.append(f"{driver} · {plate}")
    elif driver:
        lines.append(driver)
    elif plate:
        lines.append(plate)

    trn = (session.get("trn") or "").strip()
    if trn:
        lines.append(f"ТРН {trn}")

    crane_start = (session.get("crane_start") or "").strip()
    crane_end = (session.get("crane_end") or "").strip()
    crane_minutes = session.get("crane_minutes")
    if crane_start or crane_end or crane_minutes:
        crane_parts: list[str] = []
        if crane_start or crane_end:
            crane_parts.append(f"{crane_start}–{crane_end}".strip("–"))
        if crane_minutes:
            crane_parts.append(f"{crane_minutes} мин")
        site_label = site_tz_label()
        lines.append(f"Кран: {', '.join(crane_parts)} ({site_label})")

    unload_dt = format_session_completion(session)
    if unload_dt:
        lines.append(f"Завершено: {unload_dt}")

    return "\n".join(lines)


def format_departure_message(session: dict, *, msk_time: str | None = None) -> str:
    plate = _vehicle_plate(session)
    driver = _driver_name(session)
    when = (msk_time or _msk_now_label()).strip()
    lines = [
        "🚛 Выезд с площадки · Таксимо",
        f"Водитель {driver or '—'}, {plate or '—'} — выехал с площадки в {when} (время МСК)",
        "Рейс ~6–8 ч, Таксимо → Северомуйск",
    ]
    return "\n".join(lines)


def format_unload_message(session: dict) -> str:
    """Совместимость: вечерний отчёт и старые вызовы."""
    return format_complete_message(session)


def _group_blocks_by_vehicle(blocks: list[dict]) -> list[tuple[str, list[str]]]:
    """Группировка плит по машине с сохранением порядка появления."""
    grouped: dict[str, list[str]] = {}
    order: list[str] = []
    unknown: list[str] = []
    for block in blocks:
        label = (block.get("label") or "").strip()
        if not label:
            continue
        plate = (block.get("vehicle_plate") or "").strip()
        if not plate:
            unknown.append(label)
            continue
        if plate not in grouped:
            grouped[plate] = []
            order.append(plate)
        grouped[plate].append(label)
    result = [(plate, grouped[plate]) for plate in order]
    if unknown:
        result.append(("—", unknown))
    return result


def _format_vehicle_block_lines(
    blocks: list[dict],
    *,
    prefix: str = "· ",
    empty_label: str = "—",
) -> list[str]:
    grouped = _group_blocks_by_vehicle(blocks)
    if not grouped:
        return [empty_label]
    lines: list[str] = []
    for plate, labels in grouped:
        joined = ", ".join(labels) if labels else "—"
        if plate == "—":
            lines.append(f"{prefix}без машины — {joined}")
        else:
            lines.append(f"{prefix}{plate} — {joined}")
    return lines


def _wagon_full_state_key(info: dict) -> str:
    zone = (info.get("zone") or "").strip().upper()
    wagon = (info.get("wagon_number") or "").strip()
    return f"{zone}|{wagon}"


def _wagon_full_signature(info: dict) -> str:
    labels = sorted(str(label).strip() for label in (info.get("labels") or []) if str(label).strip())
    parts = [
        (info.get("zone") or "").strip().upper(),
        (info.get("wagon_number") or "").strip(),
        str(int(info.get("count") or 0)),
        "|".join(labels),
    ]
    return "||".join(parts)


def _dispatch_blocks(dispatch: dict) -> list[dict]:
    blocks = dispatch.get("blocks") or []
    if blocks:
        return blocks
    return [{"label": label} for label in (dispatch.get("block_labels") or []) if label]


def format_wagon_full_message(info: dict) -> str:
    zone = info.get("zone") or "—"
    wagon = (info.get("wagon_number") or "").strip() or "—"
    count = int(info.get("count") or 0)
    max_count = int(info.get("max") or MAX_WAGON_SLABS)
    labels = info.get("labels") or []
    blocks = ", ".join(labels) if labels else "—"
    lines = [
        "🚃 Вагон загружен · Таксимо",
        f"{wagon} · {zone} · {count}/{max_count}",
        f"Плиты: {blocks}",
    ]
    last = (info.get("last_loading") or "").strip()
    if last:
        lines.append(f"Погрузка: {last} MSK")
    return "\n".join(lines)


def format_wagon_daily_lines(wagons: list[dict]) -> list[str]:
    if not wagons:
        return ["🚃 Погрузка вагонов (до 16:00): не было."]
    hour, minute = _report_time()
    lines = [
        f"🚃 Погрузка вагонов (до {hour:02d}:{minute:02d} МСК): {len(wagons)} "
        f"{_ru_plural(len(wagons), 'вагон', 'вагона', 'вагонов')}",
        "",
    ]
    for item in wagons:
        wagon = (item.get("wagon_number") or "").strip() or "—"
        zone = item.get("zone") or "—"
        count = int(item.get("count") or 0)
        max_count = int(item.get("max") or MAX_WAGON_SLABS)
        labels = item.get("labels") or []
        blocks = ", ".join(labels) if labels else "—"
        last = (item.get("last_loading") or "").strip()
        lines.append(f"• {wagon} · {zone} · {count}/{max_count}")
        lines.append(f"  Блоки: {blocks}")
        block_rows = item.get("blocks") or []
        if block_rows:
            lines.append("  Машины:")
            lines.extend(
                _format_vehicle_block_lines(block_rows, prefix="    · ")
            )
        if last:
            lines.append(f"  Последняя: {last} MSK")
        lines.append("")
    return lines


def format_wagon_departure_message(slab: dict) -> str:
    """Совместимость: старые вызовы."""
    wagon = (slab.get("wagon_number") or "").strip()
    zone = (slab.get("platform_zone") or "").strip()
    if wagon and zone:
        return format_wagon_full_message(get_wagon_load_info(wagon, zone))
    return format_wagon_full_message(
        {
            "wagon_number": wagon,
            "zone": zone,
            "count": 1,
            "max": MAX_WAGON_SLABS,
            "labels": [f"{slab.get('letter', '')}{slab.get('number', '')}"],
            "last_loading": (slab.get("loading_date") or "").strip(),
        }
    )


def format_kodar_dispatch_message(dispatch: dict) -> str:
    block_rows = _dispatch_blocks(dispatch)
    labels = dispatch.get("block_labels") or []
    blocks = ", ".join(labels) if labels else "—"
    when = (dispatch.get("dispatched_at_label") or _msk_now_label()).strip()
    slot = dispatch.get("slot_index")
    zone = dispatch.get("slot_zone") or "—"
    wagon = dispatch.get("wagon_number") or "—"
    operator = (dispatch.get("dispatched_by") or "").strip()
    lines = [
        "🚃 Вагон отправлен в Кодар",
        f"Слот №{slot} · {zone}",
        f"Вагон {wagon}",
        f"Блоки: {blocks}",
        "Машины:",
        *_format_vehicle_block_lines(block_rows),
        f"Отправлен: {when} МСК",
    ]
    if operator:
        lines.append(f"Оператор: {operator}")
    return "\n".join(lines)


def format_kodar_received_message(dispatch: dict) -> str:
    block_rows = _dispatch_blocks(dispatch)
    labels = dispatch.get("block_labels") or []
    blocks = ", ".join(labels) if labels else "—"
    when = (dispatch.get("received_at_label") or _msk_now_label()).strip()
    wagon = dispatch.get("wagon_number") or "—"
    customer = dispatch.get("customer") or "БТС Восток"
    operator = (dispatch.get("received_by") or "").strip()
    lines = [
        "📥 Кодар · выгружен у клиента",
        f"Вагон {wagon}",
        f"Заказчик: {customer}",
        f"Блоки: {blocks}",
        "Машины:",
        *_format_vehicle_block_lines(block_rows),
        f"Принят: {when} МСК",
    ]
    if operator:
        lines.append(f"Оператор: {operator}")
    return "\n".join(lines)


async def notify_kodar_dispatch(dispatch: dict) -> bool:
    if notify_chat_id() is None:
        return False
    return await send_text(format_kodar_dispatch_message(dispatch), with_menu=False)


async def notify_kodar_received(dispatch: dict) -> bool:
    if notify_chat_id() is None:
        return False
    return await send_text(format_kodar_received_message(dispatch), with_menu=False)


def format_kodar_blocked_message(payload: dict) -> str:
    wagon = payload.get("wagon_number") or "—"
    slot = payload.get("slot_index")
    zone = payload.get("zone") or "—"
    missing = payload.get("missing_letters") or []
    missing_label = ", ".join(missing) if missing else "—"
    lines = [
        "⚠️ На Кодар не отправлять",
        f"Слот №{slot} · {zone}",
        f"Вагон {wagon}",
        f"Не хватает для полного кольца: {missing_label}",
        "Довезите блок до полного кольца A–K",
    ]
    hints = payload.get("hints") or []
    if hints:
        lines.append("Подсказка: " + hints[0])
    return "\n".join(lines)


async def notify_kodar_blocked(payload: dict) -> bool:
    if notify_chat_id() is None:
        return False
    return await send_text(format_kodar_blocked_message(payload), with_menu=False)


def format_fleet_extras_message(extras: dict, *, zone: str = "ТУРАН") -> str:
    lines = [f"★★ Допы по парку · {zone}"]
    for hint in extras.get("hints") or []:
        lines.append(hint)
    if len(lines) == 1:
        lines.append("Нет данных по допам — вагоны без целых колец.")
    return "\n".join(lines)


async def notify_fleet_extras(extras: dict, *, zone: str = "ТУРАН") -> bool:
    if notify_chat_id() is None:
        return False
    return await send_text(format_fleet_extras_message(extras, zone=zone), with_menu=False)


def format_daily_report(unload_date: str) -> list[str]:
    label = _date_label(unload_date)
    now_label = datetime.now(_tz()).strftime("%H:%M")
    sessions = list_sessions_for_date(unload_date)
    stats = stats_for_date(unload_date)
    header = f"📊 Таксимо · отчёт за {label} · {now_label} МСК"

    hour, minute = _report_time()
    wagons = list_wagon_loads_for_daily_report(unload_date, end_hour=hour, end_minute=minute)
    if not sessions:
        blocks = [header, ""]
        blocks.extend(format_wagon_daily_lines(wagons))
        if not wagons:
            blocks.append("Выгрузок не было.")
        return _split_messages("\n".join(blocks).strip())

    summary = (
        f"Итого: {stats['sessions']} "
        f"{_ru_plural(stats['sessions'], 'машина', 'машины', 'машин')} · "
        f"{stats['slabs']} {_ru_plural(stats['slabs'], 'плита', 'плиты', 'плит')}"
    )
    blocks = [header, "", summary, ""]
    blocks.extend(format_wagon_daily_lines(wagons))
    blocks.append("")
    for session in sessions:
        blocks.append(format_complete_message(session))
        blocks.append("")

    return _split_messages("\n".join(blocks).strip())


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


async def send_text(text: str, *, with_menu: bool = True) -> bool:
    chat_id = notify_chat_id()
    if chat_id is None:
        logger.warning("Таксимо: отправка пропущена — TAKSIMO_NOTIFY_CHAT_ID не задан")
        return False
    if _bot is None:
        logger.warning("Таксимо: отправка пропущена — бот не инициализирован")
        return False
    attachments = taksimo_menu_attachments() if with_menu else None
    try:
        await _bot.send_message(chat_id=chat_id, text=text, attachments=attachments)
        logger.info("Таксимо: сообщение в chat_id=%s", chat_id)
        return True
    except Exception:
        logger.exception("Таксимо: не удалось отправить в chat_id=%s", chat_id)
        return False


async def send_messages(texts: list[str]) -> bool:
    if not texts:
        return False
    ok = True
    for text in texts:
        if not await send_text(text):
            ok = False
        if len(texts) > 1:
            await asyncio.sleep(0.4)
    return ok


def _cancel_departure_task(session_id: int) -> None:
    task = _departure_tasks.pop(session_id, None)
    if task and not task.done():
        task.cancel()


def _departure_delay_sec(session: dict) -> float:
    recorded_at = session.get("crane_end_recorded_at")
    if recorded_at:
        elapsed = max(0.0, time.time() - float(recorded_at))
        return max(0.0, _DEPARTURE_DELAY_SEC - elapsed)
    return float(_DEPARTURE_DELAY_SEC)


async def _departure_worker(session_id: int, delay_sec: float) -> None:
    try:
        await asyncio.sleep(delay_sec)
        session = get_session(session_id)
        if session is None:
            return
        if session.get("departure_notified"):
            return
        if not (session.get("crane_end") or "").strip():
            return
        msk_time = _msk_now_label()
        ok = await send_text(format_departure_message(session, msk_time=msk_time))
        if ok:
            mark_session_departure_notified(session_id)
            try:
                from drivers_chat import _now_label as drivers_now_label
                from drivers_chat import bridge_taksimo_yard_departure

                await bridge_taksimo_yard_departure(
                    session,
                    when_label=drivers_now_label(),
                )
            except Exception:
                logger.exception(
                    "Таксимо: мост выезда в чат водителей session_id=%s",
                    session_id,
                )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Таксимо: ошибка отложенного выезда session_id=%s", session_id)
    finally:
        _departure_tasks.pop(session_id, None)


def schedule_departure_notify(session_id: int) -> None:
    if notify_chat_id() is None:
        return
    session = get_session(session_id)
    if session is None:
        return
    if session.get("departure_notified"):
        return
    if not (session.get("crane_end") or "").strip():
        return
    if session.get("status") != "completed":
        return
    delay_sec = _departure_delay_sec(session)
    _cancel_departure_task(session_id)
    _departure_tasks[session_id] = asyncio.create_task(
        _departure_worker(session_id, delay_sec)
    )


async def notify_started(session: dict) -> None:
    if notify_chat_id() is None:
        return
    if await send_text(format_started_message(session)):
        mark_session_started_notified(session["id"])
    try:
        from drivers_chat import bridge_taksimo_yard_arrival

        await bridge_taksimo_yard_arrival(session)
    except Exception:
        logger.exception(
            "Таксимо: мост прибытия в чат водителей session_id=%s",
            session.get("id"),
        )


def schedule_started_notify(session: dict) -> None:
    if notify_chat_id() is None:
        return
    if session.get("started_notified"):
        return
    asyncio.create_task(notify_started(session))


async def notify_complete(session: dict) -> None:
    if notify_chat_id() is None:
        return
    if await send_text(format_complete_message(session)):
        mark_session_completed_notified(session["id"])


def schedule_complete_notify(session: dict) -> None:
    if notify_chat_id() is None:
        return
    if session.get("completed_notified"):
        return
    asyncio.create_task(notify_complete(session))


async def notify_unload(session: dict) -> None:
    await notify_complete(session)


def schedule_unload_notify(session: dict) -> None:
    schedule_complete_notify(session)


def handle_session_notifications(
    session: dict,
    *,
    action: str,
    is_new: bool,
) -> None:
    """Оркестрация: начали приём / завершение / выезд через 1 мин."""
    if action == "none":
        return
    if action == "draft":
        if session.get("started_notified"):
            return
        if not (session.get("crane_start") or "").strip():
            return
        if not (session.get("slabs") or []):
            return
        schedule_started_notify(session)
        return
    if action == "complete":
        schedule_complete_notify(session)
        schedule_departure_notify(session["id"])


async def notify_wagon_full(info: dict) -> None:
    if notify_chat_id() is None:
        return
    state = _load_state()
    sent = state.get("wagon_full_signatures") or {}
    key = _wagon_full_state_key(info)
    signature = _wagon_full_signature(info)
    if sent.get(key) == signature:
        return
    ok = await send_text(format_wagon_full_message(info), with_menu=False)
    if ok:
        sent[key] = signature
        state["wagon_full_signatures"] = sent
        _save_state(state)


def schedule_wagon_full_checks(
    before: dict[tuple[str, str], int],
    after: dict[tuple[str, str], int],
) -> None:
    if notify_chat_id() is None:
        return
    for key, new_count in after.items():
        if new_count >= MAX_WAGON_SLABS:
            zone, wagon = key
            info = get_wagon_load_info(wagon, zone)
            asyncio.create_task(notify_wagon_full(info))


async def notify_wagon_departure(slab: dict) -> None:
    if notify_chat_id() is None:
        return
    wagon = (slab.get("wagon_number") or "").strip()
    zone = (slab.get("platform_zone") or "").strip()
    if wagon and zone in WAGON_ZONES:
        await notify_wagon_full(get_wagon_load_info(wagon, zone))
        return
    await send_text(format_wagon_departure_message(slab), with_menu=False)


def schedule_wagon_notify(slab: dict, old_zone: str | None) -> None:
    """Совместимость: мгновенные сообщения только при 9/9 через schedule_wagon_full_checks."""
    return


async def send_daily_report(unload_date: str | None = None) -> bool:
    if unload_date is None:
        unload_date = datetime.now(_tz()).date().isoformat()
    return await send_messages(format_daily_report(unload_date))


def _report_due(now: datetime, *, hour: int, minute: int) -> bool:
    """Пора слать отчёт: с HH:MM до конца суток (догоняем, если пропустили минуту)."""
    if now.hour > hour:
        return True
    if now.hour == hour and now.minute >= minute:
        return True
    return False


async def _try_send_daily_report(today: str) -> bool:
    if notify_chat_id() is None:
        logger.warning("Таксимо: вечерний отчёт за %s не отправлен — нет chat_id", today)
        return False
    ok = await send_daily_report(today)
    if ok:
        state = _load_state()
        state["last_daily_report"] = today
        _save_state(state)
        logger.info("Таксимо: вечерний отчёт отправлен за %s", today)
    else:
        logger.warning("Таксимо: вечерний отчёт за %s не отправлен (ошибка MAX API)", today)
    return ok


async def daily_report_loop() -> None:
    hour, minute = _report_time()
    logger.info("Таксимо: вечерний отчёт в %02d:%02d (%s)", hour, minute, _tz().key)
    while True:
        try:
            now = datetime.now(_tz())
            today = now.date().isoformat()
            state = _load_state()
            last = state.get("last_daily_report")
            if last != today and _report_due(now, hour=hour, minute=minute):
                await _try_send_daily_report(today)
                await asyncio.sleep(60)
            else:
                await asyncio.sleep(20)
        except Exception:
            logger.exception("Таксимо: ошибка цикла вечернего отчёта")
            await asyncio.sleep(60)
