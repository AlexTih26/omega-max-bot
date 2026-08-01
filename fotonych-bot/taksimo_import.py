"""Разовый импорт реестра Таксимо из Excel."""

from __future__ import annotations

import re
import time
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

from taksimo_store import (
    DB_PATH,
    PLATFORM_ZONES,
    UNPLACED_POS,
    _connect,
    find_or_create_vehicle,
    init_taksimo_db,
    wipe_operational_data,
)

_DEFAULT_XLSX = Path(__file__).resolve().parent.parent / "docs" / "registry" / "Реестр (1).xlsx"
_CYR = str.maketrans({"А": "A", "В": "B", "С": "C", "Е": "E", "К": "K", "а": "A", "в": "B", "с": "C", "е": "E", "к": "K"})


def _normalize_letter(raw: str) -> str:
    ch = (raw or "").strip().upper().translate(_CYR)
    return ch[:1] if ch else ""


def _parse_number(block_col: str, letter: str) -> str:
    text = (block_col or "").strip().translate(_CYR)
    m = re.search(r"(\d{3,6})", text)
    if m:
        return m.group(1)
    m2 = re.search(rf"{letter}\s*(\d+)", text, re.I)
    return m2.group(1) if m2 else ""


def _parse_excel_datetime(raw: str) -> tuple[str, str]:
    """ISO date + display datetime DD.MM.YYYY H:MM."""
    raw = (raw or "").strip()
    if not raw:
        return "", ""
    try:
        n = float(raw)
        dt = datetime(1899, 12, 30) + timedelta(days=n)
        return dt.date().isoformat(), dt.strftime("%d.%m.%Y %H:%M")
    except ValueError:
        pass
    m = re.match(
        r"(\d{1,2})\.(\d{1,2})\.(\d{4})\s*(\d{1,2})[-:.](\d{2})?",
        raw,
    )
    if m:
        d, mo, y, h, mi = m.groups()
        mi = mi or "00"
        dt = datetime(int(y), int(mo), int(d), int(h), int(mi))
        return dt.date().isoformat(), dt.strftime("%d.%m.%Y %H:%M")
    m2 = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", raw)
    if m2:
        d, mo, y = m2.groups()
        dt = datetime(int(y), int(mo), int(d))
        return dt.date().isoformat(), dt.strftime("%d.%m.%Y")
    return "", raw


def _read_xlsx_rows(path: Path) -> list[list[str]]:
    with zipfile.ZipFile(path) as z:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            for si in root.findall("m:si", ns):
                texts = [t.text or "" for t in si.findall(".//m:t", ns)]
                shared.append("".join(texts))
        root = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
        ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        rows: list[list[str]] = []
        for row in root.findall("m:sheetData/m:row", ns):
            vals: list[str] = []
            for c in row.findall("m:c", ns):
                t = c.get("t")
                v = c.find("m:v", ns)
                if v is None:
                    vals.append("")
                elif t == "s":
                    vals.append(shared[int(v.text)])
                else:
                    vals.append(v.text or "")
            rows.append(vals)
    return rows


def _group_sessions(data_rows: list[list[str]]) -> list[dict]:
    sessions: list[dict] = []
    current: dict | None = None
    for r in data_rows:
        letter_raw = (r[5] if len(r) > 5 else "").strip()
        block_raw = (r[6] if len(r) > 6 else "").strip()
        if not letter_raw and not block_raw:
            continue
        num = (r[0] if len(r) > 0 else "").strip()
        if num:
            current = {"registry_no": num, "rows": []}
            sessions.append(current)
        if current is None:
            continue
        current["rows"].append(r)
    return sessions


def import_registry_xlsx(
    path: Path | None = None,
    *,
    wipe_first: bool = True,
) -> dict:
    path = path or _DEFAULT_XLSX
    if not path.is_file():
        raise FileNotFoundError(path)

    init_taksimo_db()
    if wipe_first:
        wipe_operational_data()

    rows = _read_xlsx_rows(path)
    if len(rows) < 2:
        raise ValueError("файл пустой")
    groups = _group_sessions(rows[1:])
    now = time.time()
    stats = {
        "sessions": 0,
        "slabs": 0,
        "unplaced": 0,
        "in_transit": 0,
        "file": str(path),
    }

    with _connect() as conn:
        for group in groups:
            data_rows = group["rows"]
            if not data_rows:
                continue
            head = data_rows[0]
            unload_date, unload_dt = _parse_excel_datetime(str(head[1] if len(head) > 1 else ""))
            plate = (head[2] if len(head) > 2 else "").strip()
            driver = (head[3] if len(head) > 3 else "").strip()
            trn = (head[4] if len(head) > 4 else "").strip()
            vehicle_id = find_or_create_vehicle(conn, plate, driver) if plate else None

            cur = conn.execute(
                """
                INSERT INTO unload_sessions
                    (unload_date, trn, vehicle_id, driver, crane_start, crane_end,
                     crane_minutes, riggers_count, riggers_pay, taxi_pay, notes,
                     created_at, operator, revision, updated_at, unload_datetime)
                VALUES (?, ?, ?, ?, '', '', NULL, 2, 6000, 0, ?, ?, 'import', 1, ?, ?)
                """,
                (
                    unload_date or datetime.now().date().isoformat(),
                    trn,
                    vehicle_id,
                    driver,
                    f"Импорт реестр №{group['registry_no']}",
                    now,
                    now,
                    unload_dt,
                ),
            )
            session_id = int(cur.lastrowid)
            stats["sessions"] += 1

            for r in data_rows:
                letter = _normalize_letter(r[5] if len(r) > 5 else "")
                number = _parse_number(r[6] if len(r) > 6 else "", letter)
                if not letter or not number:
                    continue
                platform = (r[9] if len(r) > 9 else "ХРАНЕНИЯ").strip().upper()
                if platform not in PLATFORM_ZONES:
                    platform = "ХРАНЕНИЯ"
                wagon = (r[7] if len(r) > 7 else "").strip()
                loading = (r[8] if len(r) > 8 else "").strip()
                try:
                    pos_x = int(float(r[10])) if len(r) > 10 and str(r[10]).strip() else UNPLACED_POS
                    pos_y = int(float(r[11])) if len(r) > 11 and str(r[11]).strip() else UNPLACED_POS
                except (TypeError, ValueError):
                    pos_x = pos_y = UNPLACED_POS

                on_yard = 0 if platform in ("В ПУТИ", "ГРУЗОВОЙ", "ТУРАН") else 1
                if pos_x == UNPLACED_POS or pos_y == UNPLACED_POS:
                    pos_x = pos_y = UNPLACED_POS
                    stats["unplaced"] += 1
                if platform == "В ПУТИ":
                    stats["in_transit"] += 1

                conn.execute(
                    """
                    INSERT INTO slabs
                        (session_id, letter, number, pos_x, pos_y, suffix, weight, notes,
                         on_yard, created_at, platform_zone, wagon_number, loading_date)
                    VALUES (?, ?, ?, ?, ?, '', '', '', ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        letter,
                        number,
                        pos_x,
                        pos_y,
                        on_yard,
                        now,
                        platform,
                        wagon,
                        loading,
                    ),
                )
                stats["slabs"] += 1

    return stats
