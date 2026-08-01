"""Часовые пояса Таксимо: отчёты в МСК, кран и завершение — на площадке (MSK+5)."""

from __future__ import annotations

import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

_OLD_COMPLETION_RE = re.compile(r"^(\d{2}\.\d{2}\.\d{4}) (\d{2}:\d{2})$")


def report_tz() -> ZoneInfo:
    name = (os.getenv("TAKSIMO_TIMEZONE") or "Europe/Moscow").strip()
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("Europe/Moscow")


def site_tz() -> ZoneInfo:
    name = (os.getenv("DRIVERS_TIMEZONE") or "Asia/Irkutsk").strip()
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("Asia/Irkutsk")


def site_tz_label() -> str:
    return (os.getenv("DRIVERS_TIMEZONE_LABEL") or "MSK+5").strip()


def complete_datetime_label(*, when: datetime | None = None) -> str:
    """Метка завершения приёмки — время площадки, как у крана."""
    dt = when or datetime.now(site_tz())
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=site_tz())
    else:
        dt = dt.astimezone(site_tz())
    label = site_tz_label()
    return f"{dt.strftime('%d.%m.%Y %H:%M')} ({label})"


def _parse_legacy_msk_completion(raw: str) -> datetime | None:
    """Старые записи: «28.06.2026 07:58» без пояса — это было Europe/Moscow."""
    text = (raw or "").strip()
    if not text or "(" in text:
        return None
    m = _OLD_COMPLETION_RE.match(text)
    if not m:
        return None
    try:
        naive = datetime.strptime(text, "%d.%m.%Y %H:%M")
    except ValueError:
        return None
    return naive.replace(tzinfo=report_tz())


def format_session_completion(session: dict) -> str | None:
    """Строка «Завершено» для отчётов и уведомлений."""
    raw = (session.get("unload_datetime") or "").strip()
    if raw:
        if f"({site_tz_label()})" in raw or "(" in raw:
            return raw
        legacy = _parse_legacy_msk_completion(raw)
        if legacy is not None:
            return complete_datetime_label(when=legacy)
        return raw

    updated = session.get("updated_at")
    if updated:
        try:
            return complete_datetime_label(
                when=datetime.fromtimestamp(float(updated), tz=site_tz())
            )
        except (TypeError, ValueError, OSError):
            pass
    return None


def migrate_legacy_completion_labels(conn) -> int:
    """Конвертировать unload_datetime из МСК в MSK+5 (один раз)."""
    rows = conn.execute(
        "SELECT id, unload_datetime FROM unload_sessions WHERE unload_datetime != ''"
    ).fetchall()
    label = site_tz_label()
    changed = 0
    for row in rows:
        raw = (row["unload_datetime"] or "").strip()
        if not raw or f"({label})" in raw or "(" in raw:
            continue
        legacy = _parse_legacy_msk_completion(raw)
        if legacy is None:
            continue
        new_val = complete_datetime_label(when=legacy)
        if new_val != raw:
            conn.execute(
                "UPDATE unload_sessions SET unload_datetime = ? WHERE id = ?",
                (new_val, row["id"]),
            )
            changed += 1
    return changed
