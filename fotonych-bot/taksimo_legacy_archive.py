"""Live/read-only и архивный доступ к данным старой Таксимо.

До настроенной даты перехода данные читаются непосредственно из SQLite через
``taksimo_legacy_reader``. После неё первый успешный запрос сохраняет один
неизменяемый снимок в MySQL нового контура. Старый файл при этом открывается
только в ``mode=ro``; при ошибке снимок не создаётся и live-режим не включается
обратно.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from typing import Any, Mapping

from taksimo_new_db import iso_utc, transaction, utc_now
import taksimo_legacy_reader as legacy


ARCHIVE_KEY = "legacy-taksimo"
COLLECTIONS = (
    "vehicles",
    "wagons",
    "slots",
    "slabs",
    "yard_slabs",
    "sessions",
    "dispatch_slabs",
    "wagon_history",
)


def _empty_collections() -> dict[str, list[dict[str, Any]]]:
    return {name: [] for name in COLLECTIONS}


def _cutover_at() -> datetime | None:
    """Прочитать дату X из явной UTC-конфигурации без неявных допущений.

    Принимается только ISO 8601 с часовым поясом, например
    ``2027-01-01T00:00:00+08:00``. Пустое значение означает, что переход ещё
    не назначен и live read-only режим остаётся активным.
    """
    raw = (os.getenv("TAKSIMO_LEGACY_CUTOVER_AT") or "").strip()
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("TAKSIMO_LEGACY_CUTOVER_AT должен быть ISO 8601 с часовым поясом") from None
    if value.tzinfo is None:
        raise ValueError("TAKSIMO_LEGACY_CUTOVER_AT должен содержать часовой пояс")
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _normalised_collections(value: Any) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(value, Mapping):
        return _empty_collections()
    result = _empty_collections()
    for name in COLLECTIONS:
        items = value.get(name)
        if isinstance(items, list):
            result[name] = [item for item in items if isinstance(item, dict)]
    return result


def _archive_row() -> dict[str, Any] | None:
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT payload_json, cutover_at, captured_at
                   FROM tn_legacy_taksimo_snapshots
                   WHERE archive_key = %s""",
                (ARCHIVE_KEY,),
            )
            return cursor.fetchone()


def _archive_view(row: Mapping[str, Any]) -> dict[str, Any]:
    payload = row.get("payload_json")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = None
    return {
        "mode": "archive",
        "available": True,
        "cutover_at": iso_utc(row.get("cutover_at")),
        "captured_at": iso_utc(row.get("captured_at")),
        "collections": _normalised_collections(payload),
    }


def _save_snapshot(*, collections: Mapping[str, Any], cutover_at: datetime) -> dict[str, Any]:
    """Сохранить единственный снимок или вернуть уже созданный конкурентом."""
    now = utc_now()
    payload = json.dumps(_normalised_collections(collections), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    with transaction() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT payload_json, cutover_at, captured_at
                   FROM tn_legacy_taksimo_snapshots
                   WHERE archive_key = %s FOR UPDATE""",
                (ARCHIVE_KEY,),
            )
            existing = cursor.fetchone()
            if existing is not None:
                return _archive_view(existing)
            try:
                cursor.execute(
                    """INSERT INTO tn_legacy_taksimo_snapshots
                       (archive_key, payload_json, cutover_at, captured_at)
                       VALUES (%s, CAST(%s AS JSON), %s, %s)""",
                    (ARCHIVE_KEY, payload, cutover_at, now),
                )
            except Exception:
                # Параллельный первый запрос мог уже создать снимок. Не
                # перезаписываем его; возвращаем его, если он теперь доступен.
                cursor.execute(
                    """SELECT payload_json, cutover_at, captured_at
                       FROM tn_legacy_taksimo_snapshots
                       WHERE archive_key = %s""",
                    (ARCHIVE_KEY,),
                )
                existing = cursor.fetchone()
                if existing is not None:
                    return _archive_view(existing)
                raise
    return {
        "mode": "archive",
        "available": True,
        "cutover_at": iso_utc(cutover_at),
        "captured_at": iso_utc(now),
        "collections": _normalised_collections(collections),
    }


def current_view() -> dict[str, Any]:
    """Вернуть live-данные до даты X или неизменяемый архив после неё."""
    try:
        cutover_at = _cutover_at()
    except ValueError as exc:
        return {
            "mode": "configuration_error",
            "available": False,
            "error": str(exc),
            "cutover_at": None,
            "captured_at": None,
            "collections": _empty_collections(),
        }

    now = utc_now()
    if cutover_at is None or now < cutover_at:
        try:
            collections = legacy.read_legacy_snapshot()
        except legacy.LegacyReadError as exc:
            return {
                "mode": "live",
                "available": False,
                "error": str(exc),
                "cutover_at": iso_utc(cutover_at),
                "captured_at": None,
                "collections": _empty_collections(),
            }
        return {
            "mode": "live",
            "available": True,
            "cutover_at": iso_utc(cutover_at),
            "captured_at": None,
            "collections": _normalised_collections(collections),
        }

    archived = _archive_row()
    if archived is not None:
        return _archive_view(archived)
    try:
        collections = legacy.read_legacy_snapshot()
    except legacy.LegacyReadError as exc:
        return {
            "mode": "snapshot_pending",
            "available": False,
            "error": "Архивный снимок старой Таксимо ещё не создан: " + str(exc),
            "cutover_at": iso_utc(cutover_at),
            "captured_at": None,
            "collections": _empty_collections(),
        }
    return _save_snapshot(collections=collections, cutover_at=cutover_at)
