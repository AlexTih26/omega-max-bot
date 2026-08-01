"""Админ: парк водителей (реестр + рейсы)."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from drivers_chat import (
    _clear_driver_trip,
    _driver_status_summary,
    _load_registry_records,
    _load_state,
    _registry_active,
    _save_state,
    sync_drivers_registry,
)
from taksimo_store import sync_vehicles_from_drivers_registry, upsert_vehicle_by_tail

logger = logging.getLogger(__name__)

_REGISTRY_PATH = Path(__file__).resolve().parent.parent / "data" / "drivers_registry.json"


def _load_registry_file() -> dict:
    if not _REGISTRY_PATH.is_file():
        return {"drivers": []}
    with _REGISTRY_PATH.open(encoding="utf-8") as fh:
        data = json.load(fh)
    return data if isinstance(data, dict) else {"drivers": []}


def _save_registry_file(data: dict) -> None:
    _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _REGISTRY_PATH.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    tmp.replace(_REGISTRY_PATH)


def _trip_row(state: dict, rec: dict) -> dict:
    tail = str(rec.get("plate_tail") or "").strip()
    uid = int(rec.get("max_user_id") or 0)
    live = None
    if uid > 0:
        live = state.get("drivers", {}).get(str(uid))
    if not isinstance(live, dict) and tail:
        live = state.get("drivers", {}).get(f"plate:{tail}")
    summary = _driver_status_summary(live) if isinstance(live, dict) else {}
    return {
        "phase": summary.get("phase") or "offline",
        "phase_label": summary.get("phase_label") or "—",
        "detail": summary.get("detail") or "",
        "has_trip": bool(
            live
            and (
                live.get("arrived_factory_at")
                or live.get("departed_at")
                or live.get("arrived_taksimo_at")
                or live.get("left_taksimo_at")
            )
        ),
    }


def fleet_list_payload() -> dict:
    state = _load_state()
    items: list[dict] = []
    for rec in _load_registry_records():
        tail = str(rec.get("plate_tail") or "").strip()
        active = _registry_active(rec)
        trip = _trip_row(state, rec) if tail else {"phase": "offline", "phase_label": "резерв", "detail": "", "has_trip": False}
        items.append(
            {
                "max_user_id": int(rec.get("max_user_id") or 0),
                "name": rec.get("name") or "",
                "plate_tail": tail,
                "vehicle": rec.get("vehicle") or "",
                "taksimo_plate": rec.get("taksimo_plate") or "",
                "active": active,
                "reserve": bool(rec.get("reserve")),
                **trip,
            }
        )
    active_count = sum(1 for i in items if i.get("active") and i.get("plate_tail"))
    return {"items": items, "active_count": active_count, "total": len(items)}


def _find_registry_entry(data: dict, *, plate_tail: str = "", max_user_id: int = 0) -> dict | None:
    tail = (plate_tail or "").strip()
    for item in data.get("drivers") or []:
        if not isinstance(item, dict):
            continue
        try:
            uid = int(item.get("max_user_id") or 0)
        except (TypeError, ValueError):
            uid = 0
        if tail and str(item.get("plate_tail") or "").strip() == tail:
            return item
        if max_user_id > 0 and uid == max_user_id:
            return item
    return None


def _normalize_tail(tail: str) -> str:
    return re.sub(r"\D", "", (tail or "").strip())


def _release_driver_to_reserve(data: dict, uid: int, name: str, vehicle: str = "") -> None:
    if uid <= 0:
        return
    entry = _find_registry_entry(data, max_user_id=uid)
    if entry is None:
        data.setdefault("drivers", []).append(
            {
                "max_user_id": uid,
                "plate_tail": "",
                "name": name.strip() or f"id {uid}",
                "vehicle": vehicle,
                "active": False,
                "reserve": True,
            }
        )
        return
    entry["plate_tail"] = ""
    entry["active"] = False
    entry["reserve"] = True
    if name.strip():
        entry["name"] = name.strip()
    if vehicle.strip():
        entry["vehicle"] = vehicle.strip()


def _uid_on_other_plate(data: dict, uid: int, *, except_tail: str = "") -> str | None:
    if uid <= 0:
        return None
    for item in data.get("drivers") or []:
        if not isinstance(item, dict):
            continue
        try:
            item_uid = int(item.get("max_user_id") or 0)
        except (TypeError, ValueError):
            continue
        tail = str(item.get("plate_tail") or "").strip()
        if item_uid == uid and tail and tail != except_tail and _registry_active(item):
            return tail
    return None


def _fleet_sync_entry(entry: dict) -> None:
    tail = str(entry.get("plate_tail") or "").strip()
    if not tail:
        sync_drivers_registry()
        return
    name = str(entry.get("name") or "").strip()
    plate = str(entry.get("taksimo_plate") or "").strip()
    active = _registry_active(entry)
    sync_drivers_registry()
    if plate and name:
        upsert_vehicle_by_tail(plate=plate, driver=name, tail=tail, active=active)
    sync_vehicles_from_drivers_registry()


def change_fleet_driver(
    *,
    plate_tail: str,
    name: str,
    max_user_id: int | None = None,
    vehicle: str | None = None,
    taksimo_plate: str | None = None,
    old_to_reserve: bool = True,
    reset_trip: bool = True,
) -> tuple[bool, str]:
    tail = _normalize_tail(plate_tail)
    if not tail:
        return False, "Укажите хвост номера (348)"

    new_name = (name or "").strip()
    if not new_name:
        return False, "Укажите имя водителя"

    data = _load_registry_file()
    entry = _find_registry_entry(data, plate_tail=tail)
    if entry is None:
        return False, f"…{tail} нет в реестре — добавьте машину"

    try:
        old_uid = int(entry.get("max_user_id") or 0)
    except (TypeError, ValueError):
        old_uid = 0
    old_name = str(entry.get("name") or "").strip()

    if max_user_id is None:
        new_uid = old_uid
    else:
        new_uid = int(max_user_id)
        conflict = _uid_on_other_plate(data, new_uid, except_tail=tail)
        if conflict:
            return False, f"MAX id {new_uid} уже на …{conflict}"

    if old_to_reserve and old_uid > 0 and old_uid != new_uid:
        _release_driver_to_reserve(
            data,
            old_uid,
            old_name,
            str(entry.get("vehicle") or ""),
        )

    entry["max_user_id"] = new_uid
    entry["name"] = new_name
    entry["plate_tail"] = tail
    entry["active"] = True
    entry.pop("reserve", None)
    if vehicle is not None:
        entry["vehicle"] = vehicle.strip()
    if taksimo_plate is not None and taksimo_plate.strip():
        entry["taksimo_plate"] = taksimo_plate.strip()

    _save_registry_file(data)
    if reset_trip:
        reset_fleet_trip(plate_tail=tail)
    _fleet_sync_entry(entry)

    uid_note = f" (id {new_uid})" if new_uid > 0 else " · без MAX id — только Таксimo"
    logger.info("Админ: смена …%s → %s%s", tail, new_name, uid_note)
    return True, f"…{tail} → {new_name}{uid_note}"


def add_fleet_vehicle(
    *,
    plate_tail: str,
    name: str,
    vehicle: str = "",
    max_user_id: int = 0,
    taksimo_plate: str = "",
    active: bool = True,
) -> tuple[bool, str]:
    tail = _normalize_tail(plate_tail)
    if not tail:
        return False, "Укажите хвост номера"
    new_name = (name or "").strip()
    if not new_name:
        return False, "Укажите имя водителя"
    tplate = (taksimo_plate or "").strip()
    if not tplate:
        return False, "Укажите номер для Таксimo (как в списке оператора)"

    data = _load_registry_file()
    if _find_registry_entry(data, plate_tail=tail):
        return False, f"…{tail} уже есть в реестре"

    try:
        uid = int(max_user_id or 0)
    except (TypeError, ValueError):
        uid = 0
    conflict = _uid_on_other_plate(data, uid)
    if conflict:
        return False, f"MAX id {uid} уже на …{conflict}"

    entry = {
        "max_user_id": uid,
        "plate_tail": tail,
        "name": new_name,
        "vehicle": vehicle.strip(),
        "taksimo_plate": tplate,
        "active": bool(active),
    }
    data.setdefault("drivers", []).append(entry)
    _save_registry_file(data)
    _fleet_sync_entry(entry)

    logger.info("Админ: добавлена …%s · %s", tail, new_name)
    note = "" if uid > 0 else " · MAX id можно привязать позже"
    return True, f"Добавлено …{tail} · {new_name}{note}"


def assign_reserve_to_plate(
    *,
    max_user_id: int,
    plate_tail: str,
    vehicle: str | None = None,
    taksimo_plate: str | None = None,
    reset_trip: bool = True,
) -> tuple[bool, str]:
    try:
        uid = int(max_user_id or 0)
    except (TypeError, ValueError):
        uid = 0
    if uid <= 0:
        return False, "Нужен MAX id резервиста"

    tail = _normalize_tail(plate_tail)
    if not tail:
        return False, "Укажите хвост машины"

    data = _load_registry_file()
    reserve = _find_registry_entry(data, max_user_id=uid)
    if reserve is None or not reserve.get("reserve"):
        return False, "Водитель не в резерве"

    plate_entry = _find_registry_entry(data, plate_tail=tail)
    if plate_entry is not None and plate_entry is not reserve:
        return change_fleet_driver(
            plate_tail=tail,
            name=str(reserve.get("name") or ""),
            max_user_id=uid,
            vehicle=vehicle if vehicle is not None else str(plate_entry.get("vehicle") or ""),
            taksimo_plate=taksimo_plate,
            old_to_reserve=True,
            reset_trip=reset_trip,
        )

    reserve["plate_tail"] = tail
    reserve["active"] = True
    reserve.pop("reserve", None)
    if vehicle is not None and vehicle.strip():
        reserve["vehicle"] = vehicle.strip()
    if taksimo_plate is not None and taksimo_plate.strip():
        reserve["taksimo_plate"] = taksimo_plate.strip()

    _save_registry_file(data)
    if reset_trip:
        reset_fleet_trip(plate_tail=tail)
    _fleet_sync_entry(reserve)
    return True, f"{reserve.get('name')} → …{tail}"


def reset_fleet_trip(*, plate_tail: str) -> tuple[bool, str]:
    tail = (plate_tail or "").strip()
    if not tail or not tail.isdigit():
        return False, "Укажите хвост номера (например 348)"

    state = _load_state()
    found = False
    for key, rec in list(state.get("drivers", {}).items()):
        if not isinstance(rec, dict):
            continue
        if str(rec.get("plate_tail") or "").strip() == tail:
            _clear_driver_trip(rec)
            state["drivers"][key] = rec
            found = True
    if not found:
        return False, f"…{tail} не найден в активном состоянии"
    _save_state(state)
    logger.info("Админ: сброс рейса …%s", tail)
    return True, f"Рейс сброшен для …{tail}"


def set_fleet_active(*, plate_tail: str, active: bool) -> tuple[bool, str]:
    tail = (plate_tail or "").strip()
    if not tail:
        return False, "Нужен хвост номера"

    data = _load_registry_file()
    entry = _find_registry_entry(data, plate_tail=tail)
    if entry is None:
        return False, f"…{tail} нет в реестре"

    entry["active"] = bool(active)
    _save_registry_file(data)
    if active:
        _fleet_sync_entry(entry)
    else:
        sync_drivers_registry()
        sync_vehicles_from_drivers_registry()
        reset_fleet_trip(plate_tail=tail)

    if active:
        return True, f"…{tail} · {entry.get('name')} снова в парке"
    return True, f"…{tail} · {entry.get('name')} снят с рейса (в реестре остался)"


def assign_fleet_driver(
    *,
    plate_tail: str,
    max_user_id: int,
    name: str,
) -> tuple[bool, str]:
    return change_fleet_driver(
        plate_tail=plate_tail,
        name=name,
        max_user_id=max_user_id,
        old_to_reserve=True,
        reset_trip=True,
    )


def apply_fleet_action(body: dict) -> tuple[bool, str]:
    action = str(body.get("action") or "").strip().lower()
    if action == "reset_trip":
        return reset_fleet_trip(plate_tail=str(body.get("plate_tail") or ""))
    if action == "set_active":
        return set_fleet_active(
            plate_tail=str(body.get("plate_tail") or ""),
            active=bool(body.get("active", True)),
        )
    if action == "change":
        try:
            uid_raw = body.get("max_user_id")
            uid = int(uid_raw) if uid_raw is not None and str(uid_raw).strip() != "" else None
        except (TypeError, ValueError):
            return False, "Некорректный MAX id"
        return change_fleet_driver(
            plate_tail=str(body.get("plate_tail") or ""),
            name=str(body.get("name") or ""),
            max_user_id=uid,
            vehicle=str(body.get("vehicle") or "") if "vehicle" in body else None,
            taksimo_plate=str(body.get("taksimo_plate") or "") if "taksimo_plate" in body else None,
            old_to_reserve=bool(body.get("old_to_reserve", True)),
            reset_trip=bool(body.get("reset_trip", True)),
        )
    if action == "add":
        try:
            uid = int(body.get("max_user_id") or 0)
        except (TypeError, ValueError):
            uid = 0
        return add_fleet_vehicle(
            plate_tail=str(body.get("plate_tail") or ""),
            name=str(body.get("name") or ""),
            vehicle=str(body.get("vehicle") or ""),
            max_user_id=uid,
            taksimo_plate=str(body.get("taksimo_plate") or ""),
            active=bool(body.get("active", True)),
        )
    if action == "assign_reserve":
        try:
            uid = int(body.get("max_user_id") or 0)
        except (TypeError, ValueError):
            uid = 0
        return assign_reserve_to_plate(
            max_user_id=uid,
            plate_tail=str(body.get("plate_tail") or ""),
            vehicle=str(body.get("vehicle") or "") if "vehicle" in body else None,
            taksimo_plate=str(body.get("taksimo_plate") or "") if "taksimo_plate" in body else None,
            reset_trip=bool(body.get("reset_trip", True)),
        )
    if action == "assign":
        try:
            uid = int(body.get("max_user_id") or 0)
        except (TypeError, ValueError):
            uid = 0
        return assign_fleet_driver(
            plate_tail=str(body.get("plate_tail") or ""),
            max_user_id=uid,
            name=str(body.get("name") or ""),
        )
    if action == "sync":
        sync_drivers_registry()
        sync_vehicles_from_drivers_registry()
        return True, "Реестр и Таксimo синхронизированы"
    return False, "Неизвестное действие"
