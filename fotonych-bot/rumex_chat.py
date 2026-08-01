"""Панель диспетчера Румекс (завод, Северомуйск)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime

from drivers_chat import (
    DriverActionResult,
    _append_driver_event,
    _driver_connected,
    _driver_record,
    _format_driver_public,
    _label_after,
    _load_state,
    _now_label,
    _rumex_awaiting_active,
    _rumex_in_queue,
    _rumex_on_factory_needs_docs,
    _save_state,
    _tz,
    _tz_label,
    publish_driver_action,
)

logger = logging.getLogger(__name__)


@dataclass
class RumexActionResult:
    ok: bool
    notification: str
    public_messages: list[str]


def _parse_ids_env(name: str) -> set[int]:
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


def rumex_dispatcher_ids() -> set[int]:
    return _parse_ids_env("RUMEX_DISPATCHER_MAX_IDS")


def is_rumex_dispatcher(user_id: int | None) -> bool:
    if user_id is None:
        return False
    ids = rumex_dispatcher_ids()
    return bool(ids) and user_id in ids


def panel_role(user_id: int) -> str:
    if is_rumex_dispatcher(user_id):
        return "rumex"
    if _driver_record(_load_state(), user_id):
        return "driver"
    return "guest"


def panel_redirect(role: str) -> str:
    if role == "rumex":
        return "/rumex.html"
    if role == "driver":
        return "/drivers.html"
    return "/drivers.html"


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


RUMEX_JOURNAL_KINDS = frozenset({"factory", "rumex_documents", "rumex_reset"})


def _rumex_clear_factory_trip(driver: dict) -> None:
    for key in (
        "arrived_factory_at",
        "loaded_at",
        "documents_at",
        "departed_at",
        "departed_iso",
        "arrived_taksimo_at",
        "window",
        "window_at",
        "left_taksimo_at",
        "awaiting_factory_docs_at",
        "awaiting_factory_eta",
        "factory_depart_self",
    ):
        driver.pop(key, None)


def _rumex_at_factory(rec: dict) -> bool:
    awaiting = rec.get("awaiting_factory_docs_at")
    arrived = rec.get("arrived_factory_at")
    if not awaiting or not arrived:
        return False
    return _label_after(arrived, awaiting)


def _rumex_queue_row(rec: dict) -> dict:
    tail = str(rec.get("plate_tail") or "")
    name = str(rec.get("name") or "")
    awaiting = rec.get("awaiting_factory_docs_at") or ""
    left = rec.get("left_taksimo_at") or awaiting
    at_factory = _rumex_at_factory(rec) or _rumex_on_factory_needs_docs(rec)
    if at_factory:
        phase = "at_factory"
        status_label = "На заводе"
        detail = f"Отметился {rec.get('arrived_factory_at') or '—'}"
        eta = None
    else:
        phase = "en_route"
        status_label = "Ожидаем на заводе"
        detail = f"Выгрузка завершена · {left}"
        eta = rec.get("awaiting_factory_eta") or ""
    return {
        "max_user_id": rec.get("max_user_id"),
        "name": name,
        "plate_tail": tail,
        "vehicle": rec.get("vehicle") or "",
        "connected": _driver_connected(rec),
        "phase": phase,
        "status_label": status_label,
        "detail": detail,
        "eta_label": eta,
        "left_taksimo_at": left,
        "arrived_factory_at": rec.get("arrived_factory_at"),
        "can_documents": True,
    }


def _rumex_journal(state: dict, *, limit: int = 40) -> list[dict]:
    from drivers_chat import _event_text, _format_feed_line

    journal: list[dict] = []
    for raw in state.get("events", [])[:limit]:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind") or "")
        if kind not in RUMEX_JOURNAL_KINDS:
            continue
        extra = str(raw.get("extra") or "")
        text = _event_text(kind, extra)
        journal.append(
            {
                "at": raw.get("at") or "",
                "plate_tail": raw.get("plate_tail") or "",
                "name": raw.get("name") or "",
                "kind": kind,
                "text": text,
                "line": _format_feed_line(raw, action=text, at=str(raw.get("at") or "")),
            }
        )
    return journal


def _rumex_shift_summary(queue: list[dict], done: list[dict]) -> dict:
    queue_tails = [str(r.get("plate_tail") or "").strip() for r in queue if r.get("plate_tail")]
    done_tails = [str(r.get("plate_tail") or "").strip() for r in done if r.get("plate_tail")]
    return {
        "queue_tails": queue_tails,
        "done_tails": done_tails,
        "queue_count": len(queue_tails),
        "done_count": len(done_tails),
    }


def rumex_shift_export_text() -> str:
    data = rumex_registry_payload()
    lines: list[str] = []
    site = str(data.get("site_label") or "Румекс")
    tz = str(data.get("tz_label") or "").strip()
    now = _now_label()

    lines.append(site)
    lines.append("Отчёт смены · " + now + (" · " + tz if tz else ""))
    lines.append("")

    summary = data.get("shift_summary") or {}
    if summary.get("done_count"):
        lines.append(
            "Выдано документов: "
            + str(summary["done_count"])
            + " · "
            + ", ".join(str(t) for t in summary.get("done_tails") or [])
        )
    else:
        lines.append("Выдано документов: нет")
    if summary.get("queue_count"):
        lines.append(
            "В очереди: "
            + ", ".join(str(t) for t in summary.get("queue_tails") or [])
        )
    lines.append("")

    lines.append("ОЧЕРЕДЬ")
    queue = data.get("queue") or []
    if queue:
        for row in queue:
            parts = [f"…{row.get('plate_tail')}", row.get("name") or "", row.get("status_label") or ""]
            if row.get("eta_label") and row.get("phase") == "en_route":
                parts.append(f"~{row['eta_label']}")
            lines.append(" · ".join(p for p in parts if p))
    else:
        lines.append("—")
    lines.append("")

    lines.append("ВЫДАНО")
    done = data.get("done") or []
    if done:
        for row in done:
            when = row.get("documents_at") or ""
            lines.append(f"…{row.get('plate_tail')} · {row.get('name')} · {when}")
    else:
        lines.append("—")
    lines.append("")

    lines.append("ЖУРНАЛ")
    journal = data.get("journal") or []
    if journal:
        for item in reversed(journal):
            line = str(item.get("line") or "").strip()
            if line:
                lines.append(line)
    else:
        lines.append("—")

    return "\n".join(lines)


def rumex_registry_payload() -> dict:
    state = _load_state()
    queue: list[dict] = []
    done: list[dict] = []

    for rec in state.get("drivers", {}).values():
        if not isinstance(rec, dict):
            continue
        if not _rumex_in_queue(rec):
            continue
        queue.append(_rumex_queue_row(rec))
        docs = rec.get("documents_at")
        awaiting = rec.get("awaiting_factory_docs_at")
        if docs and awaiting and _label_after(docs, awaiting):
            done.append(
                {
                    "max_user_id": rec.get("max_user_id"),
                    "name": rec.get("name") or "",
                    "plate_tail": rec.get("plate_tail") or "",
                    "documents_at": docs,
                }
            )

    phase_order = {"at_factory": 0, "en_route": 1}
    queue.sort(
        key=lambda item: (
            phase_order.get(item.get("phase"), 9),
            item.get("name") or "",
        )
    )
    done.sort(key=lambda item: item.get("documents_at") or "", reverse=True)

    fleet_total = len(queue) + len(done)
    fleet_connected = sum(1 for row in queue if row.get("connected"))
    return {
        "queue": queue,
        "done": done,
        "on_yard": queue,
        "shipped": done,
        "fleet_total": fleet_total,
        "fleet_connected": fleet_connected,
        "shift_summary": _rumex_shift_summary(queue, done),
        "journal": _rumex_journal(state),
        "tz_label": _tz_label(),
        "site_label": "Румекс · Северомуйск",
    }


def apply_rumex_action(user_id: int, plate_tail: str, action: str) -> RumexActionResult:
    if not is_rumex_dispatcher(user_id):
        return RumexActionResult(False, "Нет доступа к панели Румекс", [])

    state = _load_state()
    found = _driver_by_plate(state, plate_tail)
    if not found:
        return RumexActionResult(False, f"Машина …{plate_tail} не в реестре", [])

    key, driver = found
    uid = int(driver.get("max_user_id") or 0)
    tail = str(driver.get("plate_tail") or plate_tail)
    action = (action or "").strip().lower()

    if action in {"documents", "issued", "issue"}:
        if not _rumex_in_queue(driver):
            return RumexActionResult(False, "Нет активной очереди по этой машине", [])
        now = _now_label()
        skip_chat = bool(driver.get("factory_depart_self"))
        driver["documents_at"] = now
        if not driver.get("departed_at"):
            driver["departed_at"] = now
            driver["departed_iso"] = datetime.now(_tz()).isoformat(timespec="minutes")
        driver.pop("awaiting_factory_docs_at", None)
        driver.pop("awaiting_factory_eta", None)
        driver.pop("factory_depart_self", None)
        state["drivers"][key] = driver
        _append_driver_event(
            state,
            user_id=uid,
            driver=driver,
            kind="rumex_documents",
            extra="румекс",
        )
        _save_state(state)
        msgs: list[str] = []
        if not skip_chat:
            msgs.append(_format_driver_public(driver, event="выехал с завода", at=now))
        note = f"…{tail} · документы выданы"
        return RumexActionResult(True, note, msgs)

    if action == "reset":
        if not _rumex_awaiting_active(driver) and not driver.get("documents_at"):
            return RumexActionResult(False, "Нечего сбрасывать", [])
        _rumex_clear_factory_trip(driver)
        state["drivers"][key] = driver
        _append_driver_event(state, user_id=uid, driver=driver, kind="rumex_reset")
        _save_state(state)
        return RumexActionResult(True, f"…{tail} · сброс", [])

    return RumexActionResult(False, "Неизвестное действие", [])


async def publish_rumex_action(result: RumexActionResult) -> None:
    if not result.ok or not result.public_messages:
        return
    for text in result.public_messages:
        await publish_driver_action(DriverActionResult(True, result.notification, text))
