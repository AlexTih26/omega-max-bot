"""Чат водителей (слой 0): выезд с Румекс, окна выгрузки Таксимо."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from maxapi import Bot
from maxapi.types import MessageCreated
from maxapi.types.attachments.buttons.open_app_button import OpenAppButton
from maxapi.types.updates.message_callback import MessageCallback
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

logger = logging.getLogger(__name__)

_bot: Bot | None = None
_MAX_BOT_USERNAME = os.getenv("MAX_BOT_USERNAME", "id5406829253_bot")
_MINIAPP_PAYLOAD = "panel"

_STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "drivers_chat.json"
_REGISTRY_PATH = Path(__file__).resolve().parent.parent / "data" / "drivers_registry.json"
_MENU_STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "drivers_menu_state.json"

CB_DRV_FACTORY_ARRIVAL = "drv_farr"
CB_DRV_FACTORY = "drv_fac"
CB_DRV_TAKSIMO_ARRIVAL = "drv_tarr"

ACTION_PAYLOADS = {
    "factory_arrival": CB_DRV_FACTORY_ARRIVAL,
    "factory": CB_DRV_FACTORY,
    "taksimo_arrival": CB_DRV_TAKSIMO_ARRIVAL,
}

BUTTONS_CAPTION = (
    "📱 Панель рейса\n\n"
    "Чат — только уведомления. Действия через панель ↓\n"
    "Водитель, диспетчер Румекс — своя панель по вашему MAX id."
)

REMIND_TEXT = (
    "☀️ Доброе утро!\n"
    "Откройте панель ↓ — отметьте рейс или посмотрите ленту в чате."
)


@dataclass
class DriverActionResult:
    ok: bool
    notification: str
    public_message: str | None = None


def set_drivers_bot(bot: Bot) -> None:
    global _bot
    _bot = bot


def drivers_chat_id() -> int | None:
    raw = (os.getenv("DRIVERS_CHAT_ID") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning("DRIVERS_CHAT_ID не число: %s", raw)
        return None


def _tz() -> ZoneInfo:
    name = (os.getenv("DRIVERS_TIMEZONE") or "Asia/Irkutsk").strip()
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("Asia/Irkutsk")


def _tz_label() -> str:
    return (os.getenv("DRIVERS_TIMEZONE_LABEL") or "MSK+5").strip()


def _parse_max_ids_env(name: str) -> set[int]:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return set()
    out: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part))
        except ValueError:
            continue
    return out


def _dispatcher_ids() -> set[int]:
    return _parse_max_ids_env("DRIVERS_DISPATCHER_MAX_IDS")


def _admin_ids() -> set[int]:
    admins = _parse_max_ids_env("DRIVERS_ADMIN_MAX_IDS")
    if admins:
        return admins
    return _dispatcher_ids()


def _dispatcher_notify_ids() -> set[int]:
    ids = _dispatcher_ids()
    if ids:
        return ids
    return _admin_ids()


def _drivers_chat_readonly() -> bool:
    return (os.getenv("DRIVERS_CHAT_READONLY") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def _drivers_max_slots() -> int:
    raw = (os.getenv("DRIVERS_MAX") or "10").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 7


async def _notify_admins(bot: Bot, text: str, *, prefix: str = "🚛 Водители") -> None:
    """Копия события администратору в личку MAX."""
    ids = _admin_ids()
    if not ids:
        return
    message = f"{prefix}\n{text}" if prefix else text
    for uid in ids:
        try:
            await bot.send_message(user_id=uid, text=message)
        except Exception:
            logger.exception("Не удалось отправить админу user_id=%s", uid)


async def notify_admin_plain(text: str) -> None:
    """Личное сообщение админу без префикса чата водителей."""
    bot = _bot
    if bot is None:
        logger.warning("notify_admin_plain: бот не инициализирован")
        return
    await _notify_admins(bot, text, prefix="")


def _load_state() -> dict:
    if not _STATE_PATH.is_file():
        return {"drivers": {}, "pending": {}}
    try:
        data = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"drivers": {}, "pending": {}}
    data.setdefault("drivers", {})
    data.setdefault("pending", {})
    data.setdefault("events", [])
    return data


_EVENT_ICONS = {
    "factory_arrival": "📍",
    "factory": "🏭",
    "loaded": "📦",
    "documents": "📄",
    "rumex_undo_load": "↩️",
    "rumex_stayed": "🏭",
    "rumex_reset": "🔄",
    "window": "🕐",
    "taksimo_arrival": "📍",
    "taksimo_yard_depart": "🚛",
    "taksimo": "🚛",
}
_MAX_EVENTS = 500


def _save_state(state: dict) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_preset_env() -> list[dict]:
    raw = (os.getenv("DRIVERS_PRESET") or "").strip()
    if not raw:
        return []
    out: list[dict] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        chunks = part.split(":")
        if len(chunks) < 3:
            continue
        try:
            uid = int(chunks[0].strip())
        except ValueError:
            continue
        out.append(
            {
                "max_user_id": uid,
                "plate_tail": chunks[1].strip(),
                "name": ":".join(chunks[2:]).strip(),
            }
        )
    return out


def _registry_active(rec: dict) -> bool:
    val = rec.get("active")
    if val is None:
        return True
    if isinstance(val, str):
        return val.strip().lower() not in ("0", "false", "no")
    return bool(val)


def _clear_driver_trip(rec: dict) -> None:
    for key in (
        "arrived_factory_at",
        "departed_at",
        "departed_iso",
        "arrived_taksimo_at",
        "window",
        "window_at",
        "left_taksimo_at",
        "loaded_at",
        "documents_at",
        "awaiting_factory_docs_at",
        "awaiting_factory_eta",
        "factory_depart_self",
    ):
        rec.pop(key, None)


def _driver_connected(rec: dict) -> bool:
    try:
        return int(rec.get("max_user_id") or 0) > 0
    except (TypeError, ValueError):
        return False


def _registry_storage_key(rec: dict) -> str:
    uid = int(rec.get("max_user_id") or 0)
    tail = str(rec.get("plate_tail") or "").strip()
    if uid > 0:
        return str(uid)
    return f"plate:{tail}"


def _load_registry_records() -> list[dict]:
    records: list[dict] = []
    if _REGISTRY_PATH.is_file():
        try:
            data = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
            for item in data.get("drivers") or []:
                if not isinstance(item, dict):
                    continue
                try:
                    uid = int(item.get("max_user_id") or 0)
                except (TypeError, ValueError):
                    continue
                tail = str(item.get("plate_tail") or "").strip()
                name = str(item.get("name") or "").strip()
                if not name:
                    continue
                active = _registry_active(item)
                if not tail and active:
                    continue
                records.append(
                    {
                        "max_user_id": uid,
                        "plate_tail": tail,
                        "name": name,
                        "vehicle": str(item.get("vehicle") or "").strip(),
                        "active": active,
                        "reserve": bool(item.get("reserve")),
                    }
                )
        except Exception:
            logger.exception("Не удалось прочитать %s", _REGISTRY_PATH)
    for item in _parse_preset_env():
        records.append({**item, "vehicle": ""})
    return records


def _cur_driver_from_registry(state: dict, rec: dict) -> dict:
    key = _registry_storage_key(rec)
    cur = state["drivers"].get(key)
    if isinstance(cur, dict):
        return cur
    uid = int(rec.get("max_user_id") or 0)
    if uid > 0:
        plate_key = f"plate:{rec['plate_tail']}"
        old = state["drivers"].get(plate_key)
        if isinstance(old, dict):
            return old
    return {}


def sync_drivers_registry() -> int:
    """Подтянуть реестр с сервера; не трогает выезд/окна в сессии."""
    records = _load_registry_records()
    if not records:
        return 0
    state = _load_state()
    active_records = [r for r in records if _registry_active(r) and str(r.get("plate_tail") or "").strip()]
    inactive_tails = {
        str(r.get("plate_tail") or "").strip()
        for r in records
        if not _registry_active(r) and str(r.get("plate_tail") or "").strip()
    }
    inactive_uids = {
        int(r.get("max_user_id") or 0)
        for r in records
        if not _registry_active(r) and int(r.get("max_user_id") or 0) > 0
    }

    for key, rec in list(state.get("drivers", {}).items()):
        if not isinstance(rec, dict):
            continue
        tail = str(rec.get("plate_tail") or "").strip()
        try:
            uid = int(rec.get("max_user_id") or 0)
        except (TypeError, ValueError):
            uid = 0
        if tail in inactive_tails or (uid > 0 and uid in inactive_uids):
            state["drivers"].pop(key, None)

    n = 0
    for rec in active_records:
        key = _registry_storage_key(rec)
        cur = _cur_driver_from_registry(state, rec)
        uid = int(rec.get("max_user_id") or 0)
        plate_key = f"plate:{rec['plate_tail']}"
        old_plate = state["drivers"].get(plate_key)
        if isinstance(old_plate, dict):
            try:
                old_uid = int(old_plate.get("max_user_id") or 0)
            except (TypeError, ValueError):
                old_uid = 0
            if uid > 0 and old_uid > 0 and uid != old_uid:
                cur = {}
        try:
            cur_uid = int(cur.get("max_user_id") or 0)
        except (TypeError, ValueError):
            cur_uid = 0
        if uid > 0 and cur_uid > 0 and uid != cur_uid:
            cur = {}
        if uid > 0:
            state["drivers"].pop(plate_key, None)
        state["drivers"][key] = {
            "max_user_id": uid,
            "name": rec["name"],
            "plate_tail": rec["plate_tail"],
            "vehicle": rec.get("vehicle") or "",
            "connected": uid > 0,
            "fleet_active": True,
            "linked_at": cur.get("linked_at") or _now_label(),
            "arrived_factory_at": cur.get("arrived_factory_at"),
            "departed_at": cur.get("departed_at"),
            "departed_iso": cur.get("departed_iso"),
            "arrived_taksimo_at": cur.get("arrived_taksimo_at") or cur.get("window_at"),
            "window": cur.get("window"),
            "window_at": cur.get("window_at"),
            "left_taksimo_at": cur.get("left_taksimo_at"),
            "loaded_at": cur.get("loaded_at"),
            "documents_at": cur.get("documents_at"),
            "awaiting_factory_docs_at": cur.get("awaiting_factory_docs_at"),
            "awaiting_factory_eta": cur.get("awaiting_factory_eta"),
            "factory_depart_self": cur.get("factory_depart_self"),
        }
        if uid > 0:
            state["pending"].pop(str(uid), None)
        n += 1
    _save_state(state)
    logger.info("Реестр водителей: %s активных из %s записей", n, len(records))
    return n


def is_drivers_chat(chat_id: int | None) -> bool:
    cid = drivers_chat_id()
    return chat_id is not None and cid is not None and chat_id == cid


def _is_dispatcher(user_id: int | None) -> bool:
    if user_id is None:
        return False
    ids = _dispatcher_ids()
    if not ids:
        return True
    return user_id in ids


def _now_label() -> str:
    return datetime.now(_tz()).strftime("%d.%m %H:%M")


def _factory_eta_hours() -> int:
    raw = (os.getenv("RUMEX_FACTORY_ETA_HOURS") or "8").strip()
    try:
        return max(1, min(int(raw), 24))
    except ValueError:
        return 8


def _leg_min_hours() -> int:
    raw = (os.getenv("DRIVERS_LEG_MIN_HOURS") or "4").strip()
    try:
        return max(1, min(int(raw), 24))
    except ValueError:
        return 4


def _departed_factory_dt(driver: dict) -> datetime | None:
    iso = driver.get("departed_iso")
    if iso:
        try:
            dt = datetime.fromisoformat(str(iso))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_tz())
            return dt.astimezone(_tz())
        except ValueError:
            pass
    return _label_to_dt(str(driver.get("departed_at") or ""))


def _left_yard_dt(driver: dict) -> datetime | None:
    return _label_to_dt(str(driver.get("left_taksimo_at") or ""))


def _format_wait_until(dt: datetime) -> str:
    return dt.astimezone(_tz()).strftime("%d.%m %H:%M")


def _leg_available_at(from_dt: datetime | None, hours: int) -> datetime | None:
    if from_dt is None:
        return None
    return from_dt + timedelta(hours=hours)


def _leg_block_taksimo_arrival(driver: dict) -> tuple[str | None, str | None]:
    if _has_taksimo_arrival(driver):
        return None, None
    departed = _departed_factory_dt(driver)
    if departed is None:
        return None, None
    until = _leg_available_at(departed, _leg_min_hours())
    if until is None:
        return None, None
    now = datetime.now(_tz())
    if now >= until:
        return None, None
    reason = (
        f"До Таксimo минимум {_leg_min_hours()} ч после выезда с завода. "
        f"Можно после {_format_wait_until(until)}"
    )
    return reason, _format_wait_until(until)


def _leg_block_factory_arrival_after_yard(driver: dict) -> tuple[str | None, str | None]:
    if not driver.get("left_taksimo_at"):
        return None, None
    left = _left_yard_dt(driver)
    if left is None:
        return None, None
    until = _leg_available_at(left, _leg_min_hours())
    if until is None:
        return None, None
    now = datetime.now(_tz())
    if now >= until:
        return None, None
    reason = (
        f"До завода минимум {_leg_min_hours()} ч после выезда с площадки. "
        f"Можно после {_format_wait_until(until)}"
    )
    return reason, _format_wait_until(until)


def _label_to_dt(label: str) -> datetime | None:
    text = (label or "").strip()
    if not text:
        return None
    try:
        now = datetime.now(_tz())
        dt = datetime.strptime(text, "%d.%m %H:%M").replace(year=now.year, tzinfo=_tz())
        if dt - now > timedelta(days=180):
            dt = dt.replace(year=now.year - 1)
        elif now - dt > timedelta(days=180):
            dt = dt.replace(year=now.year + 1)
        return dt
    except ValueError:
        return None


def _label_after(left: str, right: str) -> bool:
    dl = _label_to_dt(left)
    dr = _label_to_dt(right)
    if dl is None or dr is None:
        return False
    return dl >= dr


def _eta_factory_label(when_label: str | None = None) -> str:
    base = _label_to_dt(when_label or _now_label())
    if base is None:
        return _now_label()
    return (base + timedelta(hours=_factory_eta_hours())).strftime("%d.%m %H:%M")


def _clear_taksimo_leg(driver: dict) -> None:
    for key in (
        "arrived_taksimo_at",
        "window",
        "window_at",
        "left_taksimo_at",
        "awaiting_factory_docs_at",
        "awaiting_factory_eta",
        "factory_depart_self",
    ):
        driver.pop(key, None)


def _can_start_new_taksimo_leg(driver: dict) -> bool:
    """Новый заезд на площадку, если прошлый цикл уже закрыт или водитель снова на заводе."""
    left = driver.get("left_taksimo_at")
    if not left:
        return True
    docs = driver.get("documents_at")
    if docs and _label_after(docs, left):
        return True
    arrived = driver.get("arrived_factory_at")
    if arrived and _label_after(arrived, left):
        return True
    departed = driver.get("departed_at")
    if departed and _label_after(departed, left):
        return True
    return False


def _prepare_taksimo_leg(driver: dict) -> bool:
    """Сбросить прошлый цикл Таксimo перед новым событием оператора."""
    if _can_start_new_taksimo_leg(driver):
        if driver.get("left_taksimo_at") or driver.get("arrived_taksimo_at"):
            _clear_taksimo_leg(driver)
        return True
    return False


def _rumex_awaiting_active(rec: dict) -> bool:
    awaiting = rec.get("awaiting_factory_docs_at") or rec.get("left_taksimo_at")
    if not awaiting:
        return False
    docs = rec.get("documents_at")
    if not docs:
        return True
    return not _label_after(docs, awaiting)


def _rumex_on_factory_needs_docs(rec: dict) -> bool:
    """Первый заезд: водитель на заводе, документы ещё не выданы."""
    if not rec.get("arrived_factory_at"):
        return False
    if rec.get("left_taksimo_at"):
        return False
    if rec.get("departed_at"):
        return False
    docs = rec.get("documents_at")
    if not docs:
        return True
    return not _label_after(docs, rec.get("arrived_factory_at"))


def _rumex_in_queue(rec: dict) -> bool:
    return _rumex_awaiting_active(rec) or _rumex_on_factory_needs_docs(rec)


def _driver_record(state: dict, user_id: int) -> dict | None:
    rec = state["drivers"].get(str(user_id))
    return rec if isinstance(rec, dict) else None


def _has_factory_arrival(rec: dict) -> bool:
    return bool(rec.get("arrived_factory_at")) or bool(rec.get("departed_at"))


def _has_taksimo_arrival(rec: dict) -> bool:
    return bool(rec.get("arrived_taksimo_at")) or bool((rec.get("window") or "").strip())


def _plate_tail_from_text(plate_text: str) -> str | None:
    text = (plate_text or "").lower()
    for tail in re.findall(r"\d{3}", text):
        return tail
    return None


def _find_driver_by_plate_text(state: dict, plate_text: str) -> tuple[str, dict] | None:
    tail_hint = _plate_tail_from_text(plate_text)
    if not tail_hint:
        return None
    for key, rec in state.get("drivers", {}).items():
        if not isinstance(rec, dict):
            continue
        tail = str(rec.get("plate_tail") or "").strip()
        if tail and tail == tail_hint:
            return key, rec
    return None


def _vehicle_plate_from_session(session: dict) -> str:
    vehicle = session.get("vehicle") or {}
    if isinstance(vehicle, dict):
        return str(vehicle.get("plate") or "").strip()
    return ""


def record_taksimo_yard_arrival(
    session: dict,
    *,
    operator_event: bool = False,
) -> DriverActionResult | None:
    """Оператор: кран + первая плита → прибыл в Таксимо (mutex: кто первый — тот в чате)."""
    plate = _vehicle_plate_from_session(session)
    if not plate:
        return None
    state = _load_state()
    found = _find_driver_by_plate_text(state, plate)
    if not found:
        logger.info("Таксимо прибытие: нет в реестре для %s", plate)
        return None
    key, driver = found
    tail = str(driver.get("plate_tail") or "")
    if operator_event and _has_taksimo_arrival(driver):
        logger.info(
            "Таксимо прибытие: …%s уже отмечен водителем — без повторной публикации",
            tail,
        )
        return None
    if not _prepare_taksimo_leg(driver):
        if operator_event:
            logger.info(
                "Таксимо прибытие: сброс зависшего рейса …%s (left=%s)",
                tail,
                driver.get("left_taksimo_at"),
            )
            _clear_taksimo_leg(driver)
        else:
            return None
    if _has_taksimo_arrival(driver):
        return None
    now = _now_label()
    driver["arrived_taksimo_at"] = now
    driver.pop("window", None)
    driver.pop("window_at", None)
    uid = int(driver.get("max_user_id") or 0)
    state["drivers"][key] = driver
    _append_driver_event(
        state,
        user_id=uid,
        driver=driver,
        kind="taksimo_arrival",
        extra="оператор",
    )
    _save_state(state)
    msg = _format_driver_public(driver, event="прибыл в Таксимо", at=now)
    return DriverActionResult(True, "Прибыл в Таксимо", msg)


def record_taksimo_yard_departure(
    session: dict,
    *,
    when_label: str | None = None,
    operator_event: bool = False,
) -> str | None:
    """Завершение приёмки в Таксимо → выехал с площадки (авто, без кнопки водителя)."""
    plate = _vehicle_plate_from_session(session)
    if not plate:
        return None
    state = _load_state()
    found = _find_driver_by_plate_text(state, plate)
    if not found:
        logger.info("Таксимо выезд: нет в реестре для %s", plate)
        return None
    key, driver = found
    tail = str(driver.get("plate_tail") or "")
    if driver.get("left_taksimo_at") and not _prepare_taksimo_leg(driver):
        if operator_event:
            logger.info(
                "Таксимо выезд: сброс зависшего рейса …%s (left=%s)",
                tail,
                driver.get("left_taksimo_at"),
            )
            _clear_taksimo_leg(driver)
        else:
            logger.info(
                "Таксимо выезд: пропуск …%s — активный рейс left=%s",
                tail,
                driver.get("left_taksimo_at"),
            )
            return None
    when = (when_label or _now_label()).strip()
    driver["left_taksimo_at"] = when
    driver["awaiting_factory_docs_at"] = when
    driver["awaiting_factory_eta"] = _eta_factory_label(when)
    driver.pop("factory_depart_self", None)
    uid = int(driver.get("max_user_id") or 0)
    if not _has_taksimo_arrival(driver):
        driver["arrived_taksimo_at"] = when
    state["drivers"][key] = driver
    _append_driver_event(
        state,
        user_id=uid,
        driver=driver,
        kind="taksimo_yard_depart",
        extra="таксимо",
    )
    _save_state(state)
    return _format_driver_public(
        driver,
        event="выехал с площадки Таксимо",
        at=when,
    )


async def bridge_taksimo_yard_arrival(session: dict) -> None:
    result = record_taksimo_yard_arrival(session, operator_event=True)
    if result and result.ok and result.public_message:
        await publish_driver_action(result)


async def bridge_taksimo_yard_departure(session: dict, *, when_label: str | None = None) -> None:
    line = record_taksimo_yard_departure(
        session,
        when_label=when_label,
        operator_event=True,
    )
    if not line:
        plate = _vehicle_plate_from_session(session)
        logger.warning("Таксимо→водители: выезд не опубликован plate=%s", plate or "—")
        return
    bot = _bot
    chat_id = drivers_chat_id()
    if bot is None or chat_id is None:
        return
    try:
        await bot.send_message(chat_id=chat_id, text=line)
        await refresh_drivers_menu(bot, chat_id=chat_id)
    except Exception:
        logger.exception("Мост Таксимо→водители: выезд chat_id=%s", chat_id)
    await _notify_admins(bot, line)


def _event_text(kind: str, extra: str = "") -> str:
    if kind == "factory_arrival":
        return "прибыл на завод"
    if kind == "factory":
        return "выехал с завода"
    if kind == "loaded":
        return "загрузила"
    if kind == "documents":
        return "документы"
    if kind == "rumex_undo_load":
        return "отмена загрузки"
    if kind == "rumex_stayed":
        return "остался на заводе"
    if kind == "rumex_documents":
        return "выдала документы"
    if kind == "rumex_reset":
        return "сброс рейса"
    if kind in {"window", "taksimo_arrival"}:
        return "прибыл в Таксимо"
    if kind in {"taksimo", "taksimo_yard_depart"}:
        return "выехал с площадки Таксимо"
    return kind


def _append_driver_event(
    state: dict,
    *,
    user_id: int,
    driver: dict,
    kind: str,
    extra: str = "",
) -> dict:
    entry = {
        "id": f"{int(datetime.now(_tz()).timestamp())}-{user_id}",
        "at": _now_label(),
        "at_iso": datetime.now(_tz()).isoformat(timespec="minutes"),
        "max_user_id": user_id,
        "name": driver.get("name") or "",
        "plate_tail": driver.get("plate_tail") or "",
        "kind": kind,
        "extra": extra or None,
    }
    events = state.setdefault("events", [])
    events.insert(0, entry)
    del events[_MAX_EVENTS:]
    return entry


def _driver_status_summary(rec: dict) -> dict:
    if rec.get("left_taksimo_at"):
        return {
            "phase": "done",
            "label": "Уехал с площадки",
            "at": rec.get("left_taksimo_at"),
        }
    if _has_taksimo_arrival(rec):
        return {
            "phase": "at_yard",
            "label": "В Таксимо",
            "at": rec.get("arrived_taksimo_at") or rec.get("window_at"),
        }
    if rec.get("departed_at"):
        return {
            "phase": "en_route",
            "label": "С завода",
            "at": rec.get("departed_at"),
        }
    if rec.get("loaded_at") and not rec.get("departed_at"):
        return {
            "phase": "at_factory",
            "label": "Загрузка",
            "at": rec.get("loaded_at"),
        }
    if rec.get("arrived_factory_at"):
        return {
            "phase": "at_factory",
            "label": "На заводе",
            "at": rec.get("arrived_factory_at"),
        }
    if not _driver_connected(rec):
        return {"phase": "offline", "label": "не в MAX", "at": None}
    return {"phase": "idle", "label": "Не на линии", "at": None}


_RUMEX_JOURNAL_KINDS = frozenset(
    {
        "loaded",
        "factory",
        "documents",
        "rumex_undo_load",
        "rumex_stayed",
        "rumex_reset",
    }
)


def _event_at_datetime(raw: dict) -> datetime | None:
    iso = raw.get("at_iso")
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_tz())
    return dt


def _event_belongs_to_driver(raw: dict, viewer_id: int, plate_tail: str) -> bool:
    tail = str(raw.get("plate_tail") or "").strip()
    uid = int(raw.get("max_user_id") or 0)
    if plate_tail and tail == plate_tail:
        return True
    return uid > 0 and uid == viewer_id


def driver_journal_payload(viewer_id: int, *, days: int = 7) -> dict:
    state = _load_state()
    driver = _driver_record(state, viewer_id)
    plate_tail = str(driver.get("plate_tail") or "").strip() if driver else ""
    name = str(driver.get("name") or "").strip() if driver else ""
    cutoff = datetime.now(_tz()) - timedelta(days=max(1, days))

    events: list[dict] = []
    for raw in state.get("events", []):
        if not isinstance(raw, dict):
            continue
        if not _event_belongs_to_driver(raw, viewer_id, plate_tail):
            continue
        at_dt = _event_at_datetime(raw)
        if at_dt is not None and at_dt < cutoff:
            continue

        kind = str(raw.get("kind") or "")
        extra = str(raw.get("extra") or "")
        text = _event_text(kind, extra)
        icon = _EVENT_ICONS.get(kind, "🚛")
        line = _format_feed_line(raw, action=text, at=str(raw.get("at") or ""))
        if kind in _RUMEX_JOURNAL_KINDS:
            source, source_label = "rumex", "Румекс"
        elif kind == "taksimo_yard_depart":
            source, source_label = "taksimo", "Таксимо"
        elif kind == "taksimo_arrival" and extra == "оператор":
            source, source_label = "operator", "Оператор"
        else:
            source, source_label = "self", "Вы"
        events.append(
            {
                **raw,
                "icon": icon,
                "text": text,
                "line": line,
                "source": source,
                "source_label": source_label,
            }
        )

    return {
        "days": days,
        "plate_tail": plate_tail,
        "name": name,
        "registered": bool(driver),
        "events": events,
        "count": len(events),
        "tz_label": _tz_label(),
    }


def drivers_registry_payload(viewer_id: int, *, limit: int = 50) -> dict:
    state = _load_state()
    phase_order = {
        "at_factory": 0,
        "en_route": 1,
        "at_yard": 2,
        "done": 3,
        "idle": 4,
        "offline": 5,
    }
    roster: list[dict] = []
    for rec in state.get("drivers", {}).values():
        if not isinstance(rec, dict):
            continue
        if rec.get("fleet_active") is False:
            continue
        uid = rec.get("max_user_id")
        summary = _driver_status_summary(rec)
        roster.append(
            {
                "max_user_id": uid,
                "name": rec.get("name") or "",
                "plate_tail": rec.get("plate_tail") or "",
                "vehicle": rec.get("vehicle") or "",
                "connected": _driver_connected(rec),
                "is_me": uid == viewer_id and viewer_id > 0,
                **summary,
            }
        )
    roster.sort(
        key=lambda item: (
            phase_order.get(item.get("phase"), 9),
            item.get("name") or "",
        )
    )

    events: list[dict] = []
    for raw in state.get("events", [])[:limit]:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind") or "")
        extra = str(raw.get("extra") or "")
        text = _event_text(kind, extra)
        icon = _EVENT_ICONS.get(kind, "🚛")
        line = _format_feed_line(
            raw,
            action=text,
            at=str(raw.get("at") or ""),
        )
        events.append(
            {
                **raw,
                "is_mine": raw.get("max_user_id") == viewer_id,
                "icon": icon,
                "text": text,
                "line": line,
            }
        )

    active = sum(
        1
        for item in roster
        if item.get("phase") in {"at_factory", "en_route", "at_yard"}
    )
    fleet_total = len(roster)
    fleet_connected = sum(1 for item in roster if item.get("connected"))
    return {
        "roster": roster,
        "events": events,
        "active_count": active,
        "fleet_total": fleet_total,
        "fleet_connected": fleet_connected,
        "tz_label": _tz_label(),
    }


def _format_feed_line(
    rec: dict,
    *,
    action: str,
    at: str | None = None,
    icon: str = "🚛",
) -> str:
    tail = (rec.get("plate_tail") or "—").strip()
    name = (rec.get("name") or "—").strip()
    when = (at or _now_label()).strip()
    return f"{icon} …{tail} · {name} · {action} · {when}"


def _format_driver_public(
    rec: dict,
    *,
    event: str,
    extra: str = "",
    at: str | None = None,
) -> str:
    action = event if not extra else f"{event} · {extra}"
    return _format_feed_line(rec, action=action, at=at)


def drivers_open_app_attachments() -> list:
    kb = InlineKeyboardBuilder()
    kb.row(
        OpenAppButton(
            text="📱 Открыть панель",
            web_app=_MAX_BOT_USERNAME,
            payload=_MINIAPP_PAYLOAD,
        )
    )
    return [kb.as_markup()]


def drivers_menu_attachments() -> list:
    return drivers_open_app_attachments()


def _trip_steps(rec: dict) -> dict:
    left = bool(rec.get("left_taksimo_at"))
    has_yard = _has_taksimo_arrival(rec)
    has_departed = bool(rec.get("departed_at"))
    has_arrived = bool(rec.get("arrived_factory_at"))
    if left:
        step = 4
        next_action = "factory_arrival"
        hint = "Рейс завершён. Следующий заезд — снова «Прибыл на завод»."
    elif has_yard:
        step = 3
        next_action = "yard_wait"
        hint = "На площадке Таксимо. Выезд появится в чате сам после приёмки."
    elif has_departed:
        step = 2
        next_action = "taksimo_arrival"
        hint = (
            f"В пути в Таксimo. «Прибыл» — не раньше чем через {_leg_min_hours()} ч "
            "после выезда; оператор может отметить при приёмке."
        )
    elif has_arrived:
        step = 1
        next_action = "factory"
        hint = "На заводе. Выезд — сами или после документов от Румекс."
    else:
        step = 0
        next_action = "factory_arrival"
        hint = "Нажмите «Прибыл на завод», когда заехали."

    action_blocked = False
    action_blocked_until = None
    action_blocked_reason = None
    if next_action == "taksimo_arrival":
        block_reason, block_until = _leg_block_taksimo_arrival(rec)
        if block_reason:
            action_blocked = True
            action_blocked_until = block_until
            action_blocked_reason = block_reason
            hint = block_reason
    elif next_action == "factory_arrival" and left:
        block_reason, block_until = _leg_block_factory_arrival_after_yard(rec)
        if block_reason:
            action_blocked = True
            action_blocked_until = block_until
            action_blocked_reason = block_reason
            hint = block_reason

    return {
        "step": step,
        "next_action": next_action,
        "hint": hint,
        "factory_arrival_done": (has_arrived or has_departed) and not left,
        "factory_depart_done": has_departed and not left,
        "taksimo_arrival_done": has_yard and not left,
        "yard_depart_done": left,
        "window_done": has_yard,
        "left_done": left,
        "action_blocked": action_blocked,
        "action_blocked_until": action_blocked_until,
        "action_blocked_reason": action_blocked_reason,
    }


def driver_status_payload(user_id: int) -> dict:
    state = _load_state()
    rec = _driver_record(state, user_id)
    if not rec:
        pending = state.get("pending", {}).get(str(user_id))
        slots_left = _drivers_max_slots() - len(
            [r for r in _load_registry_records() if _registry_active(r) and str(r.get("plate_tail") or "").strip()]
        )
        base = {
            "registered": False,
            "registration_pending": isinstance(pending, dict),
            "max_user_id": user_id,
            "slots_left": max(0, slots_left),
            "drivers_max": _drivers_max_slots(),
        }
        if pending:
            base["notification"] = (
                "Заявка отправлена диспетчеру — ждите добавления в реестр."
            )
        elif slots_left <= 0:
            base["notification"] = (
                f"Реестр полон ({_drivers_max_slots()} водителей). "
                "Напишите диспетчеру в чат."
            )
        else:
            base["notification"] = (
                "Нажмите «Зарегистрироваться» — диспетчеру в личку уйдёт ваш MAX id."
            )
        return base
    steps = _trip_steps(rec)
    return {
        "registered": True,
        "name": rec.get("name") or "",
        "plate_tail": rec.get("plate_tail") or "",
        "arrived_factory_at": rec.get("arrived_factory_at"),
        "departed_at": rec.get("departed_at"),
        "arrived_taksimo_at": rec.get("arrived_taksimo_at") or rec.get("window_at"),
        "left_taksimo_at": rec.get("left_taksimo_at"),
        "tz_label": _tz_label(),
        "trip": steps,
    }


def apply_driver_action(user_id: int, payload: str) -> DriverActionResult:
    state = _load_state()
    key = str(user_id)
    driver = _driver_record(state, user_id)
    if not driver:
        return DriverActionResult(False, "Вас нет в реестре — сообщите диспетчеру MAX id")
    if not _driver_connected(driver):
        return DriverActionResult(
            False,
            "Машина в реестре, MAX id не привязан — сообщите диспетчеру",
        )

    if payload == CB_DRV_FACTORY_ARRIVAL:
        if driver.get("left_taksimo_at"):
            block_reason, _ = _leg_block_factory_arrival_after_yard(driver)
            if block_reason:
                return DriverActionResult(False, block_reason)
            driver.pop("departed_at", None)
            driver.pop("departed_iso", None)
            driver.pop("arrived_taksimo_at", None)
            driver.pop("window", None)
            driver.pop("window_at", None)
            driver.pop("left_taksimo_at", None)
            driver.pop("loaded_at", None)
            driver.pop("documents_at", None)
            driver["arrived_factory_at"] = _now_label()
            state["drivers"][key] = driver
            _append_driver_event(
                state, user_id=user_id, driver=driver, kind="factory_arrival"
            )
            _save_state(state)
            msg = _format_driver_public(driver, event="прибыл на завод")
            return DriverActionResult(True, "На заводе", msg)
        if driver.get("departed_at"):
            return DriverActionResult(False, "Рейс в пути — завершите или сбросьте")
        if driver.get("arrived_factory_at"):
            return DriverActionResult(False, "Уже на заводе — отметьте выезд")
        driver["arrived_factory_at"] = _now_label()
        state["drivers"][key] = driver
        _append_driver_event(
            state, user_id=user_id, driver=driver, kind="factory_arrival"
        )
        _save_state(state)
        msg = _format_driver_public(driver, event="прибыл на завод")
        return DriverActionResult(True, "На заводе", msg)

    if payload == CB_DRV_FACTORY:
        if not _has_factory_arrival(driver):
            return DriverActionResult(False, "Сначала: прибыл на завод")
        if driver.get("documents_at"):
            return DriverActionResult(False, "Румекс уже отметила выезд с завода")
        if driver.get("departed_at") and not driver.get("left_taksimo_at"):
            return DriverActionResult(False, "Уже выехали с завода")
        if driver.get("left_taksimo_at"):
            return DriverActionResult(False, "Сначала отметьте прибытие на завод")
        driver["departed_at"] = _now_label()
        driver["departed_iso"] = datetime.now(_tz()).isoformat(timespec="minutes")
        driver["factory_depart_self"] = True
        state["drivers"][key] = driver
        _append_driver_event(state, user_id=user_id, driver=driver, kind="factory")
        _save_state(state)
        msg = _format_driver_public(driver, event="выехал с завода")
        return DriverActionResult(True, "С завода", msg)

    if payload == CB_DRV_TAKSIMO_ARRIVAL:
        if driver.get("left_taksimo_at"):
            return DriverActionResult(False, "Рейс завершён — начните новый заезд на завод")
        if not driver.get("departed_at"):
            return DriverActionResult(False, "Сначала отметьте выезд с завода")
        if _has_taksimo_arrival(driver):
            return DriverActionResult(False, "Уже отмечено в Таксimo")
        block_reason, _ = _leg_block_taksimo_arrival(driver)
        if block_reason:
            return DriverActionResult(False, block_reason)
        now = _now_label()
        driver["arrived_taksimo_at"] = now
        driver.pop("window", None)
        driver.pop("window_at", None)
        state["drivers"][key] = driver
        _append_driver_event(
            state, user_id=user_id, driver=driver, kind="taksimo_arrival"
        )
        _save_state(state)
        msg = _format_driver_public(driver, event="прибыл в Таксimo", at=now)
        return DriverActionResult(True, "В Таксimo", msg)

    return DriverActionResult(False, "Неизвестное действие")


def submit_driver_registration(user_id: int, display_name: str) -> tuple[bool, str]:
    """Разовая заявка нового водителя (без повторной отправки диспетчеру)."""
    state = _load_state()
    if _driver_record(state, user_id):
        return False, "Вы уже в реестре"
    if state.get("pending", {}).get(str(user_id)):
        return False, "Заявка уже отправлена — ждите диспетчера"
    if len(state.get("drivers", {})) >= _drivers_max_slots():
        return (
            False,
            f"Реестр полон ({_drivers_max_slots()} водителей) — напишите диспетчеру",
        )
    name = (display_name or "Водитель").strip()[:120]
    state["pending"][str(user_id)] = {
        "max_user_id": user_id,
        "display_name": name,
        "at": _now_label(),
    }
    _save_state(state)
    return True, "Заявка отправлена диспетчеру"


async def notify_dispatchers_registration(user_id: int, display_name: str) -> None:
    bot = _bot
    ids = _dispatcher_notify_ids()
    if bot is None or not ids:
        logger.warning("Регистрация водителя %s: нет бота или DRIVERS_DISPATCHER_MAX_IDS", user_id)
        return
    name = (display_name or "—").strip()
    text = (
        "🚛 Новый водитель (заявка из панели)\n\n"
        f"Имя в MAX: {name}\n"
        f"MAX id: {user_id}\n\n"
        "Добавьте в data/drivers_registry.json:\n"
        f'  "max_user_id": {user_id}, "plate_tail": "350", "name": "..."\n'
        "Затем /drv_reload в чате водителей."
    )
    for uid in ids:
        try:
            await bot.send_message(user_id=uid, text=text)
        except Exception:
            logger.exception("Не удалось отправить заявку диспетчеру user_id=%s", uid)


async def publish_driver_action(result: DriverActionResult) -> None:
    if not result.ok or not result.public_message:
        return
    bot = _bot
    chat_id = drivers_chat_id()
    if bot is None or chat_id is None:
        return
    try:
        await bot.send_message(chat_id=chat_id, text=result.public_message)
        await refresh_drivers_menu(bot, chat_id=chat_id)
    except Exception:
        logger.exception("Не удалось опубликовать событие водителя chat_id=%s", chat_id)
    await _notify_admins(bot, result.public_message)


async def publish_drivers_announcement(text: str) -> bool:
    """Служебное объявление в чат водителей + обновление меню."""
    bot = _bot
    chat_id = drivers_chat_id()
    body = (text or "").strip()
    if bot is None or chat_id is None or not body:
        logger.warning(
            "publish_drivers_announcement: bot=%s chat_id=%s text=%s",
            bot is not None,
            chat_id,
            bool(body),
        )
        return False
    try:
        await bot.send_message(chat_id=chat_id, text=body)
        await refresh_drivers_menu(bot, chat_id=chat_id)
        logger.info("Объявление в чат водителей chat_id=%s", chat_id)
        return True
    except Exception:
        logger.exception("Не удалось опубликовать объявление chat_id=%s", chat_id)
        return False


def _load_menu_state() -> dict:
    if not _MENU_STATE_PATH.is_file():
        return {}
    try:
        return json.loads(_MENU_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_menu_state(state: dict) -> None:
    _MENU_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _MENU_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


async def _delete_old_drivers_menu(bot: Bot, *, chat_id: int, message_id: str | None) -> None:
    if not message_id:
        return
    try:
        await bot.delete_message(message_id=str(message_id))
    except Exception:
        logger.debug("delete меню водителей не удался mid=%s", message_id, exc_info=True)
    try:
        await bot.delete_pin_message(chat_id)
    except Exception:
        logger.debug("unpin меню водителей не удался chat_id=%s", chat_id, exc_info=True)


async def refresh_drivers_menu(bot: Bot, *, chat_id: int) -> None:
    """Одно меню внизу чата: удалить старое сообщение с кнопками и отправить новое последним."""
    caption = BUTTONS_CAPTION
    attachments = drivers_menu_attachments()
    menu_state = _load_menu_state()
    await _delete_old_drivers_menu(bot, chat_id=chat_id, message_id=menu_state.get("message_id"))

    try:
        msg = await bot.send_message(
            chat_id=chat_id,
            text=caption,
            attachments=attachments,
        )
    except Exception:
        logger.exception("Не удалось отправить меню водителей chat_id=%s", chat_id)
        return

    new_mid = None
    if msg is not None and getattr(msg, "message", None) is not None:
        body = getattr(msg.message, "body", None)
        new_mid = getattr(body, "mid", None) if body else None
    if new_mid:
        _save_menu_state({"message_id": str(new_mid)})
        logger.info("Меню водителей внизу чата message_id=%s", new_mid)


async def send_drivers_buttons(bot: Bot, *, chat_id: int) -> None:
    await refresh_drivers_menu(bot, chat_id=chat_id)


async def handle_drivers_callback(event: MessageCallback, bot: Bot) -> bool:
    payload = event.callback.payload if event.callback else None
    if not payload:
        return False
    known = {CB_DRV_FACTORY_ARRIVAL, CB_DRV_FACTORY, CB_DRV_TAKSIMO_ARRIVAL}
    if payload not in known:
        return False

    chat_id = event.message.recipient.chat_id if event.message else None
    if not is_drivers_chat(chat_id):
        return False

    user = event.callback.user
    user_id = user.user_id if user else None
    if user_id is None:
        await event.answer(notification="Ошибка пользователя")
        return True

    result = apply_driver_action(user_id, payload)
    note = result.notification
    if result.ok:
        note = f"{note} · дальше в панели ↓"
    else:
        note = f"{note} · откройте панель ↓"
    await event.answer(notification=note)
    if result.ok:
        await publish_driver_action(result)
    return True


async def _delete_chat_message(bot: Bot, *, mid: str | None) -> None:
    if not mid:
        return
    try:
        await bot.delete_message(message_id=str(mid))
    except Exception:
        logger.debug("delete сообщения не удался mid=%s", mid, exc_info=True)


async def handle_drivers_message(event: MessageCreated, bot: Bot) -> None:
    body = event.message.body
    text = body.text.strip() if body and body.text else ""
    chat_id = event.message.recipient.chat_id
    sender = event.message.sender
    sender_id = sender.user_id if sender else None
    message_mid = body.mid if body else None
    is_bot = bool(sender and sender.is_bot)

    if text.startswith("/drivers_chat"):
        if chat_id is not None:
            await event.message.answer(
                "Чат водителей:\n\n"
                f"DRIVERS_CHAT_ID={chat_id}\n\n"
                "Добавьте в .env и перезапустите бота."
            )
        return

    if not text.startswith("/"):
        keywords = {"меню", "menu", "кнопки", "водители", "drivers", "панель", "panel"}
        if text.lower() in keywords:
            if chat_id is not None:
                await send_drivers_buttons(bot, chat_id=chat_id)
            if _drivers_chat_readonly() and not is_bot:
                await _delete_chat_message(bot, mid=message_mid)
            return
        if _drivers_chat_readonly() and not is_bot and text:
            await _delete_chat_message(bot, mid=message_mid)
        return

    if not _is_dispatcher(sender_id):
        await event.message.answer("Команда только для диспетчера.")
        return

    if text.startswith("/drv_link"):
        m = re.match(r"/drv_link\s+(\d+)\s+(\S+)\s+(.+)", text, re.I)
        if not m:
            await event.message.answer(
                "Формат: /drv_link MAX_id хвост ФИО\n"
                "хвост — последние цифры госномера (350, 827…)\n"
                "Пример: /drv_link 122515011 350 Алексей"
            )
            return
        uid = int(m.group(1))
        tail = m.group(2).strip()
        name = m.group(3).strip()
        state = _load_state()
        state["drivers"][str(uid)] = {
            "max_user_id": uid,
            "name": name,
            "plate_tail": tail,
            "linked_at": _now_label(),
        }
        state["pending"].pop(str(uid), None)
        _save_state(state)
        await event.message.answer(
            f"✅ …{tail} · {name} (id {uid}). Добавьте в data/drivers_registry.json и /drv_reload"
        )
        return

    if text.startswith("/drv_reload"):
        n = sync_drivers_registry()
        await event.message.answer(f"Реестр обновлён: {n} водителей.")
        return

    if text.startswith("/drv_myid"):
        if sender_id is not None:
            await event.message.answer(f"Ваш MAX id: {sender_id} (для реестра на сервере)")
        return

    if text.startswith("/drv_help"):
        await event.message.answer(
            "Диспетчер чата водителей:\n"
            "· /drv_list — статус рейсов\n"
            "· /drv_ask — напомнить «прибыл в Таксимо»\n"
            "· /drv_reset 350 — сброс рейса по хвосту номера\n"
            "· /drv_panel — кнопка «Открыть панель»\n"
            "· /drv_myid — MAX id водителя\n"
            "· /drv_reload — обновить реестр с сервера\n\n"
            "Новый водитель: «Зарегистрироваться» в панели → id в личку → в реестр.\n"
            "Рейс: завод → выезд → Таксимо (кнопка или оператор) → выезд с площадки (авто).\n"
            "Румекс на заводе — отдельная панель. Админу копия: DRIVERS_ADMIN_MAX_IDS."
        )
        return

    if text.startswith("/кнопки") or text.startswith("/buttons"):
        if chat_id is not None:
            await send_drivers_buttons(bot, chat_id=chat_id)
        return

    if text.startswith("/drv_start"):
        m = re.match(r"/drv_start\s+(\d{1,2}:\d{2})", text, re.I)
        if not m:
            await event.message.answer("Формат: /drv_start 10:00")
            return
        when = m.group(1)
        if chat_id is not None:
            await bot.send_message(
                chat_id=chat_id,
                text=f"{_now_label()} ({_tz_label()}) · Диспетчер · Начнём в Таксимо с {when}",
            )
        return

    if text.startswith("/drv_ask"):
        state = _load_state()
        need = []
        for key, rec in state["drivers"].items():
            if rec.get("departed_at") and not _has_taksimo_arrival(rec):
                need.append(f"…{rec.get('plate_tail', '?')} {rec.get('name', '')}")
        if not need:
            await event.message.answer(
                "Все с выездом уже отметили прибытие в Таксимо (или никто не выехал с завода)."
            )
            return
        if chat_id is not None:
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    "Прибыл в Таксимо — панель или оператор на площадке:\n"
                    + "\n".join(f"· {line}" for line in need)
                ),
            )
            await refresh_drivers_menu(bot, chat_id=chat_id)
        return

    if text.startswith("/drv_reset"):
        tail = text.split(maxsplit=1)[1].strip() if len(text.split()) > 1 else ""
        if not tail:
            await event.message.answer("Формат: /drv_reset 350 — сброс рейса водителя")
            return
        state = _load_state()
        found = False
        for key, rec in state["drivers"].items():
            if str(rec.get("plate_tail", "")) == tail:
                rec.pop("arrived_factory_at", None)
                rec.pop("departed_at", None)
                rec.pop("departed_iso", None)
                rec.pop("arrived_taksimo_at", None)
                rec.pop("window", None)
                rec.pop("window_at", None)
                rec.pop("left_taksimo_at", None)
                rec.pop("loaded_at", None)
                rec.pop("documents_at", None)
                rec.pop("awaiting_factory_docs_at", None)
                rec.pop("awaiting_factory_eta", None)
                rec.pop("factory_depart_self", None)
                state["drivers"][key] = rec
                found = True
                break
        if not found:
            await event.message.answer(f"Водитель …{tail} не найден")
            return
        _save_state(state)
        await event.message.answer(f"Рейс сброшен для …{tail}. Можно снова с завода.")
        return

    if text.startswith("/drv_list"):
        state = _load_state()
        lines = ["Водители:"]
        for rec in state["drivers"].values():
            a = rec.get("arrived_factory_at") or "на завод —"
            d = rec.get("departed_at") or "выезд —"
            w = rec.get("arrived_taksimo_at") or rec.get("window") or "таксимо —"
            t = rec.get("left_taksimo_at") or "площадка —"
            lines.append(
                f"· …{rec.get('plate_tail', '?')} {rec.get('name', '')}: "
                f"прибыл {a}; выезд {d}; в Таксимо {w}; с площадки {t}"
            )
        await event.message.answer("\n".join(lines) if len(lines) > 1 else "Список пуст. /drv_link …")
        return

    if text.startswith("/drv_remind") or text.startswith("/drv_panel"):
        if chat_id is not None:
            await bot.send_message(
                chat_id=chat_id,
                text=REMIND_TEXT,
                attachments=drivers_open_app_attachments(),
            )
            await refresh_drivers_menu(bot, chat_id=chat_id)
        return


def drivers_reminder_enabled() -> bool:
    return _remind_hour_minute()[0] is not None


def _remind_hour_minute() -> tuple[int | None, int]:
    raw_h = (os.getenv("DRIVERS_REMIND_HOUR") or "").strip()
    if not raw_h:
        return None, 0
    try:
        hour = int(raw_h)
    except ValueError:
        return None, 0
    raw_m = (os.getenv("DRIVERS_REMIND_MINUTE") or "0").strip()
    try:
        minute = int(raw_m)
    except ValueError:
        minute = 0
    return hour, minute


async def drivers_reminder_loop() -> None:
    """Утреннее напоминание открыть панель (DRIVERS_REMIND_HOUR в часовом поясе водителей)."""
    hour, minute = _remind_hour_minute()
    if hour is None:
        return
    chat_id = drivers_chat_id()
    if chat_id is None:
        return

    last_sent = ""
    while True:
        await asyncio.sleep(45)
        now = datetime.now(_tz())
        today_key = now.strftime("%Y-%m-%d")
        if now.hour != hour or now.minute != minute or last_sent == today_key:
            continue
        try:
            await _bot.send_message(
                chat_id=chat_id,
                text=REMIND_TEXT,
                attachments=drivers_open_app_attachments(),
            )
            await refresh_drivers_menu(_bot, chat_id=chat_id)
            last_sent = today_key
            logger.info("Утреннее напоминание водителям chat_id=%s", chat_id)
        except Exception:
            logger.exception("Не удалось отправить напоминание водителям")
