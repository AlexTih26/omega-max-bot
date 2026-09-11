"""Румекс: загрузка с завода (блоки → бухгалтер → выезд) + реестр рейсов."""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from drivers_chat import (
    _append_driver_event,
    _load_state,
    _now_label,
    _rumex_awaiting_active,
    _rumex_on_factory_needs_docs,
    _save_state,
    _tz,
)

logger = logging.getLogger(__name__)

_REGISTRY_PATH = Path(__file__).resolve().parent.parent / "data" / "drivers_registry.json"

BLOCK_LETTERS = frozenset({"A", "B", "C", "D", "E", "F", "K"})
BLOCK_COUNTS = frozenset({3, 4, 5, 6})

STATUS_AWAITING = "awaiting_receipt"
STATUS_KONTUR = "in_kontur"
STATUS_RELEASED = "released"
STATUS_ACCEPTED = "accepted_taksimo"
STATUS_CANCELLED = "cancelled"

ACTIVE_STATUSES = frozenset({STATUS_AWAITING, STATUS_KONTUR, STATUS_RELEASED})
TERMINAL_STATUSES = frozenset({STATUS_ACCEPTED, STATUS_CANCELLED})

RUMEX_LOADING_EVENT_KINDS = frozenset(
    {
        "rumex_loaded",
        "rumex_kontur",
        "rumex_kontur_undo",
        "rumex_release",
        "rumex_load_update",
        "rumex_accepted_taksimo",
    }
)


@dataclass
class RumexLoadingResult:
    ok: bool
    notification: str
    public_messages: list[str] = field(default_factory=list)
    dm_messages: list[tuple[int, str]] = field(default_factory=list)
    loading: dict | None = None


def _parse_ids_env(name: str, default: str = "") -> set[int]:
    raw = (os.getenv(name) or default).strip()
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


def rumex_accountant_ids() -> set[int]:
    return _parse_ids_env("RUMEX_ACCOUNTANT_MAX_IDS", "73966464")


def is_rumex_accountant(user_id: int | None) -> bool:
    if user_id is None:
        return False
    ids = rumex_accountant_ids()
    return bool(ids) and user_id in ids


def rumex_dispatcher_ids_for_dm() -> set[int]:
    return _parse_ids_env("RUMEX_DISPATCHER_MAX_IDS")


def _load_registry_raw() -> list[dict]:
    if not _REGISTRY_PATH.is_file():
        return []
    try:
        data = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Не удалось прочитать %s", _REGISTRY_PATH)
        return []
    out: list[dict] = []
    for item in data.get("drivers") or []:
        if not isinstance(item, dict):
            continue
        tail = str(item.get("plate_tail") or "").strip()
        name = str(item.get("name") or "").strip()
        if not tail or not name:
            continue
        active = item.get("active")
        if active is not None and str(active).strip().lower() in ("0", "false", "no"):
            continue
        out.append(item)
    return out


def lookup_registry_by_tail(plate_tail: str) -> dict | None:
    tail = (plate_tail or "").strip()
    if not tail:
        return None
    for item in _load_registry_raw():
        if str(item.get("plate_tail") or "").strip() == tail:
            plate = str(item.get("taksimo_plate") or item.get("vehicle") or "").strip()
            vehicle = str(item.get("vehicle") or "").strip()
            return {
                "max_user_id": int(item.get("max_user_id") or 0),
                "name": str(item.get("name") or "").strip(),
                "plate_tail": tail,
                "plate": plate,
                "vehicle": vehicle,
            }
    state = _load_state()
    for rec in state.get("drivers", {}).values():
        if not isinstance(rec, dict):
            continue
        if str(rec.get("plate_tail") or "").strip() != tail:
            continue
        plate = str(rec.get("vehicle") or "").strip()
        return {
            "max_user_id": int(rec.get("max_user_id") or 0),
            "name": str(rec.get("name") or "").strip(),
            "plate_tail": tail,
            "plate": plate,
            "vehicle": plate,
        }
    return None


def _driver_by_plate(state: dict, plate_tail: str) -> tuple[str, dict] | None:
    tail = (plate_tail or "").strip()
    if not tail:
        return None
    for key, rec in state.get("drivers", {}).items():
        if not isinstance(rec, dict):
            continue
        if str(rec.get("plate_tail") or "").strip() == tail:
            return key, rec
    return None


def _loadings_list(state: dict) -> list[dict]:
    raw = state.setdefault("rumex_loadings", [])
    if not isinstance(raw, list):
        raw = []
        state["rumex_loadings"] = raw
    return [item for item in raw if isinstance(item, dict)]


def _normalize_block(raw: dict) -> dict | None:
    letter = str(raw.get("letter") or "").strip().upper()
    number = str(raw.get("number") or "").strip()
    if letter not in BLOCK_LETTERS or not number:
        return None
    return {"letter": letter, "number": number}


def _blocks_short(blocks: list[dict]) -> str:
    return " · ".join(f"{b['letter']}-{b['number']}" for b in blocks)


def _blocks_multiline(blocks: list[dict]) -> str:
    return "\n".join(f"{b['letter']}-{b['number']}" for b in blocks)


def _status_label(status: str) -> str:
    return {
        STATUS_AWAITING: "Ждёт расписку",
        STATUS_KONTUR: "В Контуре",
        STATUS_RELEASED: "Выехала с завода",
        STATUS_ACCEPTED: "Принята в Таксimo",
        STATUS_CANCELLED: "Отменена",
    }.get(status, status)


def _loading_public(row: dict) -> dict:
    blocks = row.get("blocks") or []
    return {
        "id": row.get("id"),
        "plate_tail": row.get("plate_tail"),
        "name": row.get("name"),
        "plate": row.get("plate"),
        "vehicle": row.get("vehicle"),
        "block_count": row.get("block_count"),
        "blocks": blocks,
        "blocks_short": _blocks_short(blocks) if blocks else "",
        "status": row.get("status"),
        "status_label": _status_label(str(row.get("status") or "")),
        "loaded_at": row.get("loaded_at"),
        "kontur_at": row.get("kontur_at"),
        "released_at": row.get("released_at"),
        "accepted_at": row.get("accepted_at"),
    }


def _active_loading_for_tail(state: dict, tail: str) -> dict | None:
    for row in _loadings_list(state):
        if str(row.get("plate_tail") or "").strip() != tail:
            continue
        if str(row.get("status") or "") in ACTIVE_STATUSES:
            return row
    return None


def suggest_rumex_mode(plate_tail: str) -> str:
    """load = загрузка с завода; docs = документы после Таксimo."""
    tail = (plate_tail or "").strip()
    if not tail:
        return "load"
    state = _load_state()
    if _active_loading_for_tail(state, tail):
        return "load"
    found = _driver_by_plate(state, tail)
    if not found:
        return "load"
    _, driver = found
    if _rumex_awaiting_active(driver):
        return "docs"
    if driver.get("left_taksimo_at") and not driver.get("documents_at"):
        return "docs"
    if _rumex_on_factory_needs_docs(driver):
        return "load"
    return "load"


def kontur_copy_text(row: dict) -> str:
    blocks = row.get("blocks") or []
    lines = [
        "Экспедиторская расписка",
        f"Водитель: {row.get('name') or '—'}",
        f"Гос. номер: {row.get('plate') or '—'}",
        f"ТС: {row.get('vehicle') or '—'}",
        f"Блоков: {row.get('block_count') or len(blocks)}",
        _blocks_multiline(blocks),
        f"Румекс · …{row.get('plate_tail')} · {row.get('loaded_at') or '—'}",
    ]
    return "\n".join(lines)


def accountant_dm_loaded(row: dict) -> str:
    blocks = row.get("blocks") or []
    return (
        "📦 Румекс · ждёт экспедиторскую расписку\n\n"
        f"Хвост: …{row.get('plate_tail')}\n"
        f"Водитель: {row.get('name')}\n"
        f"Гос. номер: {row.get('plate')}\n\n"
        f"Блоков: {row.get('block_count')}\n"
        f"{_blocks_multiline(blocks)}\n\n"
        f"Загрузка: {row.get('loaded_at')}\n\n"
        "Откройте кабинет бухгалтера → «Ждут расписку».\n"
        "После оформления в Контуре нажмите «Отправила в Контур»."
    )


def accountant_dm_updated(row: dict) -> str:
    blocks = row.get("blocks") or []
    return (
        "🔄 Румекс · обновлён состав\n\n"
        f"Хвост: …{row.get('plate_tail')}\n"
        f"{row.get('name')} · {row.get('plate')}\n\n"
        f"Новый состав ({row.get('block_count')}):\n"
        f"{_blocks_multiline(blocks)}\n\n"
        f"Загрузка: {row.get('loaded_at')}\n"
        "Предыдущая отметка «В Контуре» снята — нужна новая расписка."
    )


def dispatcher_dm_kontur(row: dict) -> str:
    return (
        f"📄 …{row.get('plate_tail')} · документы в Контуре\n\n"
        f"{row.get('name')} · {row.get('plate')}\n"
        f"{_blocks_short(row.get('blocks') or [])}\n\n"
        "Проверьте Контур и разрешите выезд."
    )


def dispatcher_dm_kontur_undo(row: dict) -> str:
    return (
        f"⚠️ …{row.get('plate_tail')} · бухгалтер сняла отметку «В Контуре».\n"
        "Проверьте состав и статус."
    )


def chat_loaded(row: dict) -> str:
    return (
        f"🏭 Румекс · …{row.get('plate_tail')} · {_blocks_short(row.get('blocks') or [])}\n"
        "Загружена · ждёт расписку"
    )


def chat_released(row: dict) -> str:
    return (
        f"🚛 …{row.get('plate_tail')} · {row.get('name')}\n"
        f"Выехала с завода Румекс · {_blocks_short(row.get('blocks') or [])}"
    )


def chat_accepted(row: dict) -> str:
    return f"✅ Таксimo · …{row.get('plate_tail')} · принята"


def loading_registry_payload() -> dict:
    state = _load_state()
    rows = _loadings_list(state)
    active = [_loading_public(r) for r in rows if str(r.get("status") or "") in ACTIVE_STATUSES]
    awaiting = [r for r in active if r["status"] == STATUS_AWAITING]
    kontur = [r for r in active if r["status"] == STATUS_KONTUR]
    released = [r for r in active if r["status"] == STATUS_RELEASED]
    history = [
        _loading_public(r)
        for r in rows
        if str(r.get("status") or "") in {STATUS_ACCEPTED, STATUS_RELEASED}
    ]
    history.sort(key=lambda item: item.get("released_at") or item.get("loaded_at") or "", reverse=True)
    return {
        "awaiting_receipt": awaiting,
        "in_kontur": kontur,
        "released": released,
        "active": active,
        "history": history[:40],
        "accountant_name": "Наталья Тихомирова",
    }


def _validate_blocks(block_count: int, blocks: list[dict]) -> tuple[list[dict] | None, str | None]:
    if block_count not in BLOCK_COUNTS:
        return None, "Выберите 3, 4, 5 или 6 блоков"
    if len(blocks) != block_count:
        return None, f"Укажите номера для всех {block_count} блоков"
    normalized: list[dict] = []
    for raw in blocks:
        block = _normalize_block(raw if isinstance(raw, dict) else {})
        if block is None:
            return None, "Проверьте буквы (A–F, K) и номера блоков"
        normalized.append(block)
    return normalized, None


def apply_loading_loaded(
    user_id: int,
    plate_tail: str,
    *,
    block_count: int,
    blocks: list[dict],
    is_dispatcher: bool,
) -> RumexLoadingResult:
    if not is_dispatcher:
        return RumexLoadingResult(False, "Нет доступа к панели Румекс")
    tail = (plate_tail or "").strip()
    info = lookup_registry_by_tail(tail)
    if info is None:
        return RumexLoadingResult(False, f"Машина …{tail} не найдена в реестре")
    normalized, err = _validate_blocks(block_count, blocks)
    if err:
        return RumexLoadingResult(False, err)

    state = _load_state()
    now = _now_label()
    existing = _active_loading_for_tail(state, tail)
    rows = _loadings_list(state)
    updated = existing is not None and str(existing.get("status") or "") in {
        STATUS_AWAITING,
        STATUS_KONTUR,
    }

    if existing and str(existing.get("status") or "") == STATUS_RELEASED:
        return RumexLoadingResult(False, "Машина уже отпущена — начните новый рейс позже")

    if existing:
        existing.update(
            {
                "name": info["name"],
                "plate": info["plate"],
                "vehicle": info["vehicle"],
                "max_user_id": info["max_user_id"],
                "block_count": block_count,
                "blocks": normalized,
                "loaded_at": now,
                "status": STATUS_AWAITING,
                "kontur_at": None,
                "updated_at": now,
            }
        )
        row = existing
        event_kind = "rumex_load_update"
        toast = "Состав обновлён. Бухгалтеру отправлен новый пакет."
        dm_accountant = accountant_dm_updated(row)
    else:
        row = {
            "id": uuid.uuid4().hex[:12],
            "plate_tail": tail,
            "name": info["name"],
            "plate": info["plate"],
            "vehicle": info["vehicle"],
            "max_user_id": info["max_user_id"],
            "block_count": block_count,
            "blocks": normalized,
            "status": STATUS_AWAITING,
            "loaded_at": now,
            "kontur_at": None,
            "released_at": None,
            "accepted_at": None,
            "created_at": now,
            "updated_at": now,
        }
        rows.insert(0, row)
        state["rumex_loadings"] = rows
        event_kind = "rumex_loaded"
        toast = f"…{tail} загружена. Бухгалтеру отправлено."

    found = _driver_by_plate(state, tail)
    if found:
        key, driver = found
        _append_driver_event(
            state,
            user_id=int(driver.get("max_user_id") or 0),
            driver=driver,
            kind=event_kind,
            extra=_blocks_short(normalized),
        )
        driver["loaded_at"] = now
        state["drivers"][key] = driver

    _save_state(state)

    if not updated:
        dm_accountant = accountant_dm_loaded(row)
    return RumexLoadingResult(
        True,
        toast,
        public_messages=[chat_loaded(row)],
        dm_messages=[(uid, dm_accountant) for uid in rumex_accountant_ids()],
        loading=_loading_public(row),
    )


def apply_loading_kontur(user_id: int, *, loading_id: str = "", plate_tail: str = "") -> RumexLoadingResult:
    if not is_rumex_accountant(user_id):
        return RumexLoadingResult(False, "Нет доступа к кабинету бухгалтера")
    state = _load_state()
    row = _find_loading(state, loading_id=loading_id, plate_tail=plate_tail)
    if row is None:
        return RumexLoadingResult(False, "Рейс не найден")
    if str(row.get("status") or "") != STATUS_AWAITING:
        return RumexLoadingResult(False, "Рейс не ждёт расписку")
    now = _now_label()
    row["status"] = STATUS_KONTUR
    row["kontur_at"] = now
    row["updated_at"] = now
    _save_state(state)
    tail = str(row.get("plate_tail") or "")
    return RumexLoadingResult(
        True,
        f"…{tail} · отметка отправлена диспетчеру.",
        dm_messages=[(uid, dispatcher_dm_kontur(row)) for uid in rumex_dispatcher_ids_for_dm()],
        loading=_loading_public(row),
    )


def apply_loading_kontur_undo(user_id: int, *, loading_id: str = "") -> RumexLoadingResult:
    if not is_rumex_accountant(user_id):
        return RumexLoadingResult(False, "Нет доступа")
    state = _load_state()
    row = _find_loading(state, loading_id=loading_id)
    if row is None:
        return RumexLoadingResult(False, "Рейс не найден")
    if str(row.get("status") or "") != STATUS_KONTUR:
        return RumexLoadingResult(False, "Нет отметки «В Контуре»")
    row["status"] = STATUS_AWAITING
    row["kontur_at"] = None
    row["updated_at"] = _now_label()
    _save_state(state)
    tail = str(row.get("plate_tail") or "")
    return RumexLoadingResult(
        True,
        "Отметка снята. Диспетчер уведомлён.",
        dm_messages=[(uid, dispatcher_dm_kontur_undo(row)) for uid in rumex_dispatcher_ids_for_dm()],
        loading=_loading_public(row),
    )


def apply_loading_release(user_id: int, plate_tail: str, *, is_dispatcher: bool) -> RumexLoadingResult:
    if not is_dispatcher:
        return RumexLoadingResult(False, "Нет доступа к панели Румекс")
    tail = (plate_tail or "").strip()
    state = _load_state()
    row = _active_loading_for_tail(state, tail)
    if row is None:
        return RumexLoadingResult(False, f"Нет активной загрузки …{tail}")
    if str(row.get("status") or "") != STATUS_KONTUR:
        return RumexLoadingResult(False, "Сначала нужна отметка «В Контуре» от бухгалтера")

    now = _now_label()
    row["status"] = STATUS_RELEASED
    row["released_at"] = now
    row["updated_at"] = now

    found = _driver_by_plate(state, tail)
    if found:
        key, driver = found
        uid = int(driver.get("max_user_id") or 0)
        driver["departed_at"] = now
        driver["departed_iso"] = datetime.now(_tz()).isoformat(timespec="minutes")
        if not driver.get("documents_at"):
            driver["documents_at"] = now
        driver.pop("factory_depart_self", None)
        state["drivers"][key] = driver
        _append_driver_event(
            state,
            user_id=uid,
            driver=driver,
            kind="rumex_release",
            extra=_blocks_short(row.get("blocks") or []),
        )

    _save_state(state)
    return RumexLoadingResult(
        True,
        f"…{tail} · выезд разрешён.",
        public_messages=[chat_released(row)],
        loading=_loading_public(row),
    )


def mark_loading_accepted_taksimo(plate_text: str) -> str | None:
    from drivers_chat import _plate_tail_from_text

    tail_hint = _plate_tail_from_text(plate_text)
    if not tail_hint:
        return None
    state = _load_state()
    row = _active_loading_for_tail(state, tail_hint)
    if row is None:
        for item in _loadings_list(state):
            if str(item.get("plate_tail") or "") != tail_hint:
                continue
            if str(item.get("status") or "") == STATUS_RELEASED:
                row = item
                break
    if row is None:
        return None
    if str(row.get("status") or "") == STATUS_ACCEPTED:
        return None
    if str(row.get("status") or "") not in {STATUS_RELEASED, STATUS_KONTUR}:
        return None
    now = _now_label()
    row["status"] = STATUS_ACCEPTED
    row["accepted_at"] = now
    row["updated_at"] = now
    _save_state(state)
    return chat_accepted(row)


def _find_loading(state: dict, *, loading_id: str = "", plate_tail: str = "") -> dict | None:
    lid = (loading_id or "").strip()
    tail = (plate_tail or "").strip()
    for row in _loadings_list(state):
        if lid and str(row.get("id") or "") == lid:
            return row
        if tail and str(row.get("plate_tail") or "").strip() == tail:
            if str(row.get("status") or "") in ACTIVE_STATUSES:
                return row
    if tail:
        for row in _loadings_list(state):
            if str(row.get("plate_tail") or "").strip() == tail:
                return row
    return None


def cancel_loadings_for_tail(plate_tail: str, *, state: dict | None = None) -> int:
    tail = (plate_tail or "").strip()
    if not tail:
        return 0
    own_state = state is None
    if own_state:
        state = _load_state()
    count = 0
    for row in _loadings_list(state):
        if str(row.get("plate_tail") or "").strip() != tail:
            continue
        if str(row.get("status") or "") in ACTIVE_STATUSES:
            row["status"] = STATUS_CANCELLED
            row["updated_at"] = _now_label()
            count += 1
    if own_state and count:
        _save_state(state)
    return count


def is_tail_in_rumex_queue(plate_tail: str, *, state: dict | None = None) -> bool:
    from drivers_chat import _rumex_in_queue

    tail = (plate_tail or "").strip()
    if not tail:
        return False
    if state is None:
        state = _load_state()
    for rec in state.get("drivers", {}).values():
        if not isinstance(rec, dict):
            continue
        if str(rec.get("plate_tail") or "").strip() != tail:
            continue
        if _rumex_in_queue(rec):
            return True
    for row in _loadings_list(state):
        if str(row.get("plate_tail") or "").strip() != tail:
            continue
        if str(row.get("status") or "") in ACTIVE_STATUSES:
            return True
    return False


def find_loading(*, loading_id: str = "", plate_tail: str = "") -> dict | None:
    return _find_loading(_load_state(), loading_id=loading_id, plate_tail=plate_tail)


def kontur_copy_for_loading(loading_id: str) -> str | None:
    row = find_loading(loading_id=loading_id)
    if row is None:
        return None
    return kontur_copy_text(row)


async def publish_rumex_loading_result(result: RumexLoadingResult) -> None:
    from drivers_chat import _bot, notify_admin_plain, publish_drivers_announcement

    if not result.ok:
        return
    for text in result.public_messages:
        line = text.strip()
        if line:
            await publish_drivers_announcement(line)
    bot = _bot
    if bot is None:
        for _uid, text in result.dm_messages:
            try:
                await notify_admin_plain(text)
            except Exception:
                pass
        return
    for uid, text in result.dm_messages:
        try:
            from keyboards import accountant_open_app_attachments

            attachments = (
                accountant_open_app_attachments()
                if uid in rumex_accountant_ids()
                else None
            )
            await bot.send_message(
                user_id=uid,
                text=text,
                attachments=attachments,
            )
        except Exception:
            logger.exception("rumex DM failed user_id=%s", uid)
