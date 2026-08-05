"""Учёт выгрузки ЖБИ на площадке Таксимо."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from datetime import date, datetime, time as dt_time
from pathlib import Path

from taksimo_time import report_tz

logger = logging.getLogger(__name__)

_CYR_TO_LAT = str.maketrans(
    {
        "А": "A",
        "В": "B",
        "С": "C",
        "Е": "E",
        "К": "K",
        "а": "A",
        "в": "B",
        "с": "C",
        "е": "E",
        "к": "K",
    }
)

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "taksimo.db"


class TaksimoConflictError(Exception):
    """Другой пользователь изменил запись."""


class KodarBlockedError(ValueError):
    """Отправка в Кодар запрещена — кольцо не целое."""

    def __init__(
        self,
        message: str,
        *,
        slot_id: int,
        zone: str,
        slot_index: int,
        wagon_number: str,
        missing_letters: list[str],
        hints: list[str],
    ) -> None:
        super().__init__(message)
        self.slot_id = slot_id
        self.zone = zone
        self.slot_index = slot_index
        self.wagon_number = wagon_number
        self.missing_letters = missing_letters
        self.hints = hints

    def as_notify_payload(self) -> dict:
        return {
            "slot_id": self.slot_id,
            "zone": self.zone,
            "slot_index": self.slot_index,
            "wagon_number": self.wagon_number,
            "missing_letters": self.missing_letters,
            "hints": self.hints,
            "message": str(self),
        }

SLAB_LETTERS = ("A", "B", "C", "D", "E", "F", "K")
SUFFIXES = ("", "к", "а", "тк", "скол")
DEFAULT_CUSTOMER = "БТС Восток"
KODAR_TRANSIT_ZONE = "В КОДАР"
CUSTOMER_DELIVERED_ZONE = "БТС ВОСТОК"
PLATFORM_ZONES_OPERATOR = ("ХРАНЕНИЯ", "ГРУЗОВОЙ", "ТУРАН", "В ПУТИ")
PLATFORM_ZONES = PLATFORM_ZONES_OPERATOR + (KODAR_TRANSIT_ZONE, CUSTOMER_DELIVERED_ZONE)
KODAR_SYSTEM_ZONES = frozenset({KODAR_TRANSIT_ZONE, CUSTOMER_DELIVERED_ZONE})
WAGON_ZONES = frozenset({"ГРУЗОВОЙ", "ТУРАН"})
MAX_WAGON_SLABS = 9
MAX_WAGONS_PER_DEAD_END = 10
WAGON_DEAD_ENDS = ("ГРУЗОВОЙ", "ТУРАН")
UNPLACED_POS = 0
DEFAULT_GRID_X = 13
DEFAULT_GRID_Y = 25
MAX_SLABS_PER_CELL = 4
SESSION_DRAFT = "draft"
SESSION_COMPLETED = "completed"

SEED_VEHICLES = (
    ("в348му 124", "", "Андриянов А.С."),
    ("O 827 MX 68", "", "Кудрук В.В."),
    ("Е 673ТТ 124", "", "Индюков А.В."),
    ("Е 350 ТМ 124", "", "Шаповалов В.А."),
    ("С 938 НО 03", "", "Патели Николай"),
    ("у 503 рр 124", "", "Панов А.В."),
    ("— резерв —", "", ""),
)


def _normalize_plate(plate: str) -> str:
    return re.sub(r"\s+", " ", (plate or "").strip().lower())


def _sync_seed_vehicles(conn: sqlite3.Connection) -> None:
    """Дополнить справочник машин из SEED_VEHICLES; порядок и резерв сохраняются."""
    rows = conn.execute("SELECT id, plate FROM vehicles").fetchall()
    by_norm = {_normalize_plate(row["plate"]): int(row["id"]) for row in rows}

    for i, (plate, brand, driver) in enumerate(SEED_VEHICLES):
        norm = _normalize_plate(plate)
        vid = by_norm.get(norm)
        if vid is not None:
            conn.execute("UPDATE vehicles SET sort_order = ? WHERE id = ?", (i, vid))
            continue
        cur = conn.execute(
            "INSERT INTO vehicles (plate, brand, driver, sort_order, active) "
            "VALUES (?, ?, ?, ?, 1)",
            (plate, brand, driver, i),
        )
        by_norm[norm] = int(cur.lastrowid)


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def is_placed(pos_x: int, pos_y: int) -> bool:
    return 1 <= int(pos_x) <= DEFAULT_GRID_X and 1 <= int(pos_y) <= DEFAULT_GRID_Y


def _normalize_platform(zone: str | None) -> str:
    z = (zone or "ХРАНЕНИЯ").strip().upper()
    if z not in PLATFORM_ZONES:
        raise ValueError(f"Недопустимый статус: {z}")
    return z


def _loading_date_now() -> str:
    return datetime.now(report_tz()).strftime("%d.%m.%Y %H:%M")


def _parse_loading_datetime(raw: str) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    for fmt, size in (("%d.%m.%Y %H:%M", 16), ("%d.%m.%Y", 10)):
        try:
            dt = datetime.strptime(text[:size], fmt)
            return dt.replace(tzinfo=report_tz())
        except ValueError:
            continue
    return None


def _slab_label(letter: str, number: str, suffix: str = "") -> str:
    label = f"{(letter or '').strip().upper()}{(number or '').strip()}"
    suf = (suffix or "").strip()
    if suf:
        label += suf
    return label


def _resolve_loading_date(
    old: dict | None,
    zone: str,
    wagon: str,
    incoming: str,
) -> str:
    if zone not in WAGON_ZONES or not wagon:
        return ""
    incoming = (incoming or "").strip()
    if old:
        old_zone = (old.get("platform_zone") or "").strip()
        old_wagon = (old.get("wagon_number") or "").strip()
        old_loading = (old.get("loading_date") or "").strip()
        if old_zone == zone and old_wagon == wagon and old_loading:
            return old_loading
    if incoming:
        return incoming
    return _loading_date_now()


def _resolve_slab_placement(s: dict, *, old: dict | None = None) -> dict:
    """Нормализация полей плиты по статусу площадки."""
    zone = _normalize_platform(s.get("platform_zone"))
    wagon = (s.get("wagon_number") or "").strip()
    loading = _resolve_loading_date(old, zone, wagon, (s.get("loading_date") or "").strip())
    suffix = (s.get("suffix") or "").strip()
    if suffix not in SUFFIXES:
        suffix = suffix[:8]

    if zone == "ХРАНЕНИЯ":
        try:
            pos_x = int(s["pos_x"])
            pos_y = int(s["pos_y"])
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError("Для ХРАНЕНИЯ укажите координаты X/Y") from e
        _validate_pos(pos_x, pos_y)
        on_yard = 1
        wagon = ""
        loading = ""
    elif zone == "В ПУТИ":
        pos_x = pos_y = UNPLACED_POS
        on_yard = 0
        wagon = ""
        loading = ""
    elif zone == KODAR_TRANSIT_ZONE:
        if not wagon:
            raise ValueError("Для «В КОДАР» нужен номер вагона")
        pos_x = pos_y = UNPLACED_POS
        on_yard = 0
    elif zone == CUSTOMER_DELIVERED_ZONE:
        pos_x = pos_y = UNPLACED_POS
        on_yard = 0
    else:
        if not wagon:
            raise ValueError(f"Для {zone} укажите номер вагона")
        pos_x = pos_y = UNPLACED_POS
        on_yard = 0

    return {
        "platform_zone": zone,
        "wagon_number": wagon,
        "loading_date": loading,
        "pos_x": pos_x,
        "pos_y": pos_y,
        "on_yard": on_yard,
        "suffix": suffix,
        "weight": (s.get("weight") or "").strip(),
        "notes": (s.get("notes") or "").strip(),
    }


def _count_wagon_slabs(
    conn: sqlite3.Connection,
    wagon_number: str,
    *,
    exclude_slab_ids: set[int] | None = None,
) -> int:
    exclude_slab_ids = exclude_slab_ids or set()
    wagon_number = wagon_number.strip()
    if not wagon_number:
        return 0
    if exclude_slab_ids:
        placeholders = ",".join("?" * len(exclude_slab_ids))
        sql = f"""
            SELECT COUNT(*) FROM slabs
            WHERE wagon_number = ? AND platform_zone IN ('ГРУЗОВОЙ', 'ТУРАН')
              AND id NOT IN ({placeholders})
        """
        params: list = [wagon_number, *exclude_slab_ids]
    else:
        sql = """
            SELECT COUNT(*) FROM slabs
            WHERE wagon_number = ? AND platform_zone IN ('ГРУЗОВОЙ', 'ТУРАН')
        """
        params = [wagon_number]
    return int(conn.execute(sql, params).fetchone()[0])


def yard_stats() -> dict:
    with _connect() as conn:
        on_yard = conn.execute(
            """
            SELECT COUNT(*) FROM slabs
            WHERE platform_zone = 'ХРАНЕНИЯ' AND on_yard = 1
              AND pos_x > 0 AND pos_y > 0
            """
        ).fetchone()[0]
        on_wagon = conn.execute(
            """
            SELECT COUNT(*) FROM slabs
            WHERE platform_zone IN ('ГРУЗОВОЙ', 'ТУРАН')
            """
        ).fetchone()[0]
        in_transit = conn.execute(
            "SELECT COUNT(*) FROM slabs WHERE platform_zone = 'В ПУТИ'"
        ).fetchone()[0]
        on_kodar = conn.execute(
            "SELECT COUNT(*) FROM slabs WHERE platform_zone = ?",
            (KODAR_TRANSIT_ZONE,),
        ).fetchone()[0]
        at_bts = conn.execute(
            "SELECT COUNT(*) FROM slabs WHERE platform_zone = ?",
            (CUSTOMER_DELIVERED_ZONE,),
        ).fetchone()[0]
        wagons_in_transit = conn.execute(
            "SELECT COUNT(*) FROM wagon_dispatches WHERE status = 'in_transit'"
        ).fetchone()[0]
    return {
        "on_yard": int(on_yard),
        "on_wagon": int(on_wagon),
        "in_transit": int(in_transit),
        "on_kodar": int(on_kodar),
        "at_bts_vostok": int(at_bts),
        "wagons_in_transit": int(wagons_in_transit),
    }


def db_status() -> dict:
    """Время последнего изменения данных и бэкапа."""
    sess_ts = slab_ts = 0.0
    with _connect() as conn:
        row = conn.execute(
            "SELECT MAX(COALESCE(updated_at, created_at)) FROM unload_sessions"
        ).fetchone()
        if row and row[0]:
            sess_ts = float(row[0])
        row2 = conn.execute("SELECT MAX(created_at) FROM slabs").fetchone()
        if row2 and row2[0]:
            slab_ts = float(row2[0])
    file_ts = DB_PATH.stat().st_mtime if DB_PATH.is_file() else 0.0
    last_change = max(sess_ts, slab_ts, file_ts)
    return {
        "last_change_ts": last_change,
        "sessions_ts": sess_ts,
        "slabs_ts": slab_ts,
        "file_ts": file_ts,
    }


def _migrate_sessions(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(unload_sessions)")}
    if "operator" not in cols:
        conn.execute("ALTER TABLE unload_sessions ADD COLUMN operator TEXT NOT NULL DEFAULT ''")
    if "revision" not in cols:
        conn.execute("ALTER TABLE unload_sessions ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
    if "updated_at" not in cols:
        conn.execute("ALTER TABLE unload_sessions ADD COLUMN updated_at REAL")
    if "unload_datetime" not in cols:
        conn.execute("ALTER TABLE unload_sessions ADD COLUMN unload_datetime TEXT NOT NULL DEFAULT ''")
    if "status" not in cols:
        conn.execute(
            "ALTER TABLE unload_sessions ADD COLUMN status TEXT NOT NULL DEFAULT 'completed'"
        )
    if "started_notified" not in cols:
        conn.execute(
            "ALTER TABLE unload_sessions ADD COLUMN started_notified INTEGER NOT NULL DEFAULT 0"
        )
    if "completed_notified" not in cols:
        conn.execute(
            "ALTER TABLE unload_sessions ADD COLUMN completed_notified INTEGER NOT NULL DEFAULT 0"
        )
    if "departure_notified" not in cols:
        conn.execute(
            "ALTER TABLE unload_sessions ADD COLUMN departure_notified INTEGER NOT NULL DEFAULT 0"
        )
    if "crane_end_recorded_at" not in cols:
        conn.execute("ALTER TABLE unload_sessions ADD COLUMN crane_end_recorded_at REAL")


def _migrate_slabs(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(slabs)")}
    if "platform_zone" not in cols:
        conn.execute(
            "ALTER TABLE slabs ADD COLUMN platform_zone TEXT NOT NULL DEFAULT 'ХРАНЕНИЯ'"
        )
    if "wagon_number" not in cols:
        conn.execute("ALTER TABLE slabs ADD COLUMN wagon_number TEXT NOT NULL DEFAULT ''")
    if "loading_date" not in cols:
        conn.execute("ALTER TABLE slabs ADD COLUMN loading_date TEXT NOT NULL DEFAULT ''")
    if "customer" not in cols:
        conn.execute("ALTER TABLE slabs ADD COLUMN customer TEXT NOT NULL DEFAULT ''")
    if "wagon_dispatch_id" not in cols:
        conn.execute("ALTER TABLE slabs ADD COLUMN wagon_dispatch_id INTEGER")


def _migrate_wagon_dispatches(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS wagon_dispatches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wagon_number TEXT NOT NULL,
            slot_zone TEXT NOT NULL DEFAULT '',
            slot_index INTEGER NOT NULL DEFAULT 0,
            slab_count INTEGER NOT NULL DEFAULT 0,
            blocks_json TEXT NOT NULL DEFAULT '[]',
            scheme_template_id INTEGER NOT NULL DEFAULT 0,
            scheme_name TEXT NOT NULL DEFAULT '',
            scheme_code TEXT NOT NULL DEFAULT '',
            has_box INTEGER NOT NULL DEFAULT 0,
            returns_materials INTEGER NOT NULL DEFAULT 0,
            extra_units INTEGER NOT NULL DEFAULT 0,
            k_goal INTEGER NOT NULL DEFAULT 0,
            origin_zone TEXT NOT NULL DEFAULT '',
            return_status TEXT NOT NULL DEFAULT '',
            return_target_zone TEXT NOT NULL DEFAULT '',
            return_actual_zone TEXT NOT NULL DEFAULT '',
            dispatched_at REAL NOT NULL,
            dispatched_by TEXT NOT NULL DEFAULT '',
            received_at REAL,
            received_by TEXT NOT NULL DEFAULT '',
            customer TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'in_transit'
        );
        CREATE INDEX IF NOT EXISTS idx_wagon_dispatches_wagon
            ON wagon_dispatches(wagon_number, status);
        """
    )
    cols = {row[1] for row in conn.execute("PRAGMA table_info(wagon_dispatches)")}
    additions = {
        "scheme_template_id": "INTEGER NOT NULL DEFAULT 0",
        "scheme_name": "TEXT NOT NULL DEFAULT ''",
        "scheme_code": "TEXT NOT NULL DEFAULT ''",
        "has_box": "INTEGER NOT NULL DEFAULT 0",
        "returns_materials": "INTEGER NOT NULL DEFAULT 0",
        "extra_units": "INTEGER NOT NULL DEFAULT 0",
        "k_goal": "INTEGER NOT NULL DEFAULT 0",
        "origin_zone": "TEXT NOT NULL DEFAULT ''",
        "return_status": "TEXT NOT NULL DEFAULT ''",
        "return_target_zone": "TEXT NOT NULL DEFAULT ''",
        "return_actual_zone": "TEXT NOT NULL DEFAULT ''",
    }
    for name, ddl in additions.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE wagon_dispatches ADD COLUMN {name} {ddl}")


def init_taksimo_db() -> None:
    with _connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS vehicles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plate TEXT NOT NULL UNIQUE,
                brand TEXT NOT NULL DEFAULT '',
                driver TEXT NOT NULL DEFAULT '',
                sort_order INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS unload_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                unload_date TEXT NOT NULL,
                trn TEXT NOT NULL DEFAULT '',
                vehicle_id INTEGER,
                driver TEXT NOT NULL DEFAULT '',
                crane_start TEXT NOT NULL DEFAULT '',
                crane_end TEXT NOT NULL DEFAULT '',
                crane_minutes INTEGER,
                riggers_count INTEGER NOT NULL DEFAULT 2,
                riggers_pay INTEGER NOT NULL DEFAULT 6000,
                taxi_pay INTEGER NOT NULL DEFAULT 0,
                notes TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                FOREIGN KEY (vehicle_id) REFERENCES vehicles(id)
            );

            CREATE TABLE IF NOT EXISTS slabs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                letter TEXT NOT NULL,
                number TEXT NOT NULL,
                pos_x INTEGER NOT NULL,
                pos_y INTEGER NOT NULL,
                suffix TEXT NOT NULL DEFAULT '',
                weight TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                on_yard INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL,
                FOREIGN KEY (session_id) REFERENCES unload_sessions(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_slabs_xy ON slabs(pos_x, pos_y);
            CREATE INDEX IF NOT EXISTS idx_slabs_letter_num ON slabs(letter, number);
            CREATE INDEX IF NOT EXISTS idx_slabs_session ON slabs(session_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_date ON unload_sessions(unload_date);
            """
        )
        count = conn.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0]
        if count == 0:
            for i, (plate, brand, driver) in enumerate(SEED_VEHICLES):
                conn.execute(
                    "INSERT INTO vehicles (plate, brand, driver, sort_order) VALUES (?, ?, ?, ?)",
                    (plate, brand, driver, i),
                )
        _sync_seed_vehicles(conn)
        _migrate_sessions(conn)
        _migrate_slabs(conn)
        _migrate_wagon_dispatches(conn)
        from taksimo_store_materials import migrate_materials

        migrate_materials(conn)
        from taksimo_time import migrate_legacy_completion_labels

        migrated = migrate_legacy_completion_labels(conn)
        if migrated:
            logger.info(
                "Таксимо: конвертировано unload_datetime (МСК → площадка): %s",
                migrated,
            )
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS wagon_slots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                zone TEXT NOT NULL,
                slot_index INTEGER NOT NULL,
                wagon_number TEXT NOT NULL DEFAULT '',
                expected_blocks TEXT NOT NULL DEFAULT '[]',
                updated_at REAL,
                UNIQUE(zone, slot_index)
            );
            CREATE TABLE IF NOT EXISTS wagon_pool (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                number TEXT NOT NULL UNIQUE,
                active INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        _seed_wagon_slots(conn)
        from taksimo_store_fleet import migrate_wagon_pool_fleet

        migrate_wagon_pool_fleet(conn)


def _row_vehicle(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "plate": row["plate"],
        "brand": row["brand"],
        "driver": row["driver"],
        "sort_order": row["sort_order"],
        "active": bool(row["active"]),
    }


def _row_session(row: sqlite3.Row) -> dict:
    keys = row.keys()
    return {
        "id": row["id"],
        "unload_date": row["unload_date"],
        "trn": row["trn"],
        "vehicle_id": row["vehicle_id"],
        "driver": row["driver"],
        "crane_start": row["crane_start"],
        "crane_end": row["crane_end"],
        "crane_minutes": row["crane_minutes"],
        "riggers_count": row["riggers_count"],
        "riggers_pay": row["riggers_pay"],
        "taxi_pay": row["taxi_pay"],
        "notes": row["notes"],
        "created_at": row["created_at"],
        "operator": row["operator"] if "operator" in keys else "",
        "revision": row["revision"] if "revision" in keys else 1,
        "updated_at": row["updated_at"] if "updated_at" in keys else row["created_at"],
        "unload_datetime": row["unload_datetime"] if "unload_datetime" in keys else "",
        "status": row["status"] if "status" in keys else SESSION_COMPLETED,
        "started_notified": bool(row["started_notified"]) if "started_notified" in keys else True,
        "completed_notified": bool(row["completed_notified"]) if "completed_notified" in keys else True,
        "departure_notified": bool(row["departure_notified"]) if "departure_notified" in keys else False,
        "crane_end_recorded_at": row["crane_end_recorded_at"] if "crane_end_recorded_at" in keys else None,
    }


def _slab_location_label(slab: dict) -> str:
    zone = slab.get("platform_zone") or "ХРАНЕНИЯ"
    wagon = (slab.get("wagon_number") or "").strip()
    customer = (slab.get("customer") or "").strip()
    if zone == CUSTOMER_DELIVERED_ZONE:
        return f"Выгружена у {customer or DEFAULT_CUSTOMER}"
    if zone == KODAR_TRANSIT_ZONE:
        return f"Вагон {wagon} → Кодар" if wagon else "→ Кодар"
    if zone in WAGON_ZONES and wagon:
        return f"Вагон {wagon} · {zone}"
    if zone == "В ПУТИ":
        return "В пути"
    if zone == "ХРАНЕНИЯ" and slab.get("placed"):
        return str(slab.get("place") or "—")
    return zone


def _row_slab(row: sqlite3.Row) -> dict:
    keys = row.keys()
    pos_x = row["pos_x"]
    pos_y = row["pos_y"]
    placed = is_placed(pos_x, pos_y)
    slab = {
        "id": row["id"],
        "session_id": row["session_id"],
        "letter": row["letter"],
        "number": row["number"],
        "label": f"{row['letter']} {row['number']}".strip(),
        "pos_x": pos_x,
        "pos_y": pos_y,
        "place": f"{pos_x}/{pos_y}" if placed else "—",
        "placed": placed,
        "suffix": row["suffix"],
        "weight": row["weight"],
        "notes": row["notes"],
        "on_yard": bool(row["on_yard"]),
        "created_at": row["created_at"],
        "platform_zone": row["platform_zone"] if "platform_zone" in keys else "ХРАНЕНИЯ",
        "wagon_number": row["wagon_number"] if "wagon_number" in keys else "",
        "loading_date": row["loading_date"] if "loading_date" in keys else "",
        "customer": row["customer"] if "customer" in keys else "",
        "wagon_dispatch_id": row["wagon_dispatch_id"] if "wagon_dispatch_id" in keys else None,
    }
    slab["location"] = _slab_location_label(slab)
    return slab


def list_vehicles() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM vehicles WHERE active = 1 ORDER BY sort_order, id"
        ).fetchall()
    return [_row_vehicle(r) for r in rows]


def get_vehicle(vehicle_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM vehicles WHERE id = ?", (vehicle_id,)).fetchone()
    return _row_vehicle(row) if row else None


def _seed_wagon_slots(conn: sqlite3.Connection) -> None:
    for zone in WAGON_DEAD_ENDS:
        for slot_index in range(1, MAX_WAGONS_PER_DEAD_END + 1):
            conn.execute(
                """
                INSERT OR IGNORE INTO wagon_slots (zone, slot_index, wagon_number, expected_blocks)
                VALUES (?, ?, '', '[]')
                """,
                (zone, slot_index),
            )


def _normalize_expected_blocks(raw: str | list | None) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        items = raw
    else:
        text = (raw or "").strip()
        if not text:
            return []
        try:
            items = json.loads(text)
        except json.JSONDecodeError:
            items = [p.strip() for p in text.replace(";", ",").split(",") if p.strip()]
    out: list[str] = []
    for item in items:
        label = re.sub(r"\s+", "", str(item).upper().translate(_CYR_TO_LAT))
        if label and label not in out:
            out.append(label)
    return out


def _slab_label_compact(letter: str, number: str) -> str:
    return f"{(letter or '').strip().upper()}{(number or '').strip()}"


def _count_wagons_in_zone(conn: sqlite3.Connection, zone: str, wagon_number: str) -> int:
    return int(
        conn.execute(
            """
            SELECT COUNT(*) FROM slabs
            WHERE platform_zone = ? AND wagon_number = ?
            """,
            (zone, wagon_number),
        ).fetchone()[0]
    )


def _ensure_zone_wagon_slot(
    conn: sqlite3.Connection,
    zone: str,
    wagon_number: str,
    *,
    require_existing_slot: bool = False,
) -> None:
    zone = _normalize_platform(zone)
    if zone not in WAGON_ZONES:
        return
    wagon_number = wagon_number.strip()
    if not wagon_number:
        return
    active = conn.execute(
        """
        SELECT COUNT(DISTINCT wagon_number) FROM slabs
        WHERE platform_zone = ? AND wagon_number != '' AND wagon_number != ?
        """,
        (zone, wagon_number),
    ).fetchone()[0]
    in_slot = conn.execute(
        "SELECT 1 FROM wagon_slots WHERE zone = ? AND wagon_number = ? LIMIT 1",
        (zone, wagon_number),
    ).fetchone()
    if require_existing_slot and not in_slot:
        raise ValueError(f"{zone}: выберите вагон из слота этого тупика")
    if not in_slot and int(active) >= MAX_WAGONS_PER_DEAD_END:
        raise ValueError(
            f"{zone}: в тупике уже {MAX_WAGONS_PER_DEAD_END} вагонов, освободите слот"
        )


def _ensure_wagon_capacity(
    conn: sqlite3.Connection,
    wagon_number: str,
    *,
    zone: str | None = None,
    exclude_slab_ids: set[int] | None = None,
    adding: int = 1,
) -> None:
    wagon_number = wagon_number.strip()
    if not wagon_number:
        return
    exclude_slab_ids = exclude_slab_ids or set()
    if zone:
        if exclude_slab_ids:
            placeholders = ",".join("?" * len(exclude_slab_ids))
            sql = f"""
                SELECT COUNT(*) FROM slabs
                WHERE wagon_number = ? AND platform_zone = ?
                  AND id NOT IN ({placeholders})
            """
            params: list = [wagon_number, zone, *exclude_slab_ids]
        else:
            sql = """
                SELECT COUNT(*) FROM slabs
                WHERE wagon_number = ? AND platform_zone = ?
            """
            params = [wagon_number, zone]
        current = int(conn.execute(sql, params).fetchone()[0])
    else:
        current = _count_wagon_slabs(conn, wagon_number, exclude_slab_ids=exclude_slab_ids)
    if current + adding > MAX_WAGON_SLABS:
        where = f" ({zone})" if zone else ""
        raise ValueError(
            f"Вагон {wagon_number}{where}: максимум {MAX_WAGON_SLABS} блоков "
            f"(сейчас {current}, добавляете {adding})"
        )


def _session_crane_end_ts(crane_end: str, *, previous: str | None = None) -> float | None:
    crane_end = (crane_end or "").strip()
    if not crane_end:
        return None
    if previous is not None and crane_end == (previous or "").strip():
        return None
    return time.time()


def _persist_session_slabs(
    conn: sqlite3.Connection,
    session_id: int,
    slabs: list[dict],
    *,
    now: float,
) -> list[dict]:
    saved_slabs: list[dict] = []
    batch_at_cell: dict[tuple[int, int], int] = {}
    wagon_batch: dict[tuple[str, str], int] = {}
    kept_ids: set[int] = set()
    form_slab_ids: set[int] = set()
    for s in slabs:
        letter = (s.get("letter") or "").strip().upper()
        number = (s.get("number") or "").strip()
        if not letter or not number or number == "0":
            continue
        slab_id = s.get("id")
        if not slab_id:
            existing_id = _find_session_slab_id(conn, session_id, letter, number)
            if existing_id:
                slab_id = existing_id
        if slab_id:
            form_slab_ids.add(int(slab_id))

    for s in slabs:
        letter = (s.get("letter") or "").strip().upper()
        number = (s.get("number") or "").strip()
        if not letter or not number or number == "0":
            continue
        if letter not in SLAB_LETTERS:
            raise ValueError(f"Недопустимая буква: {letter}")
        slab_id = s.get("id")
        if not slab_id:
            existing_id = _find_session_slab_id(conn, session_id, letter, number)
            if existing_id:
                slab_id = existing_id
                s["id"] = existing_id
        old_slab = None
        if slab_id:
            old_row = conn.execute(
                "SELECT * FROM slabs WHERE id = ? AND session_id = ?",
                (int(slab_id), session_id),
            ).fetchone()
            if old_row:
                old_slab = _row_slab(old_row)
        placement = _resolve_slab_placement(s, old=old_slab)
        pos_x = placement["pos_x"]
        pos_y = placement["pos_y"]
        zone = placement["platform_zone"]
        if is_placed(pos_x, pos_y):
            cell_key = (pos_x, pos_y)
            old_pos = None
            if slab_id:
                old_row = conn.execute(
                    "SELECT pos_x, pos_y FROM slabs WHERE id = ? AND session_id = ?",
                    (int(slab_id), session_id),
                ).fetchone()
                if old_row:
                    old_pos = (int(old_row[0]), int(old_row[1]))
            if old_pos == cell_key:
                pass
            else:
                batch_at_cell[cell_key] = batch_at_cell.get(cell_key, 0) + 1
                _ensure_cell_capacity(
                    conn,
                    pos_x,
                    pos_y,
                    exclude_session_id=session_id,
                    adding=batch_at_cell[cell_key],
                )
        if placement["wagon_number"]:
            w = placement["wagon_number"]
            _ensure_zone_wagon_slot(conn, zone, w)
            key = (zone, w)
            wagon_batch[key] = wagon_batch.get(key, 0) + 1
            _ensure_wagon_capacity(
                conn,
                w,
                zone=zone,
                exclude_slab_ids=form_slab_ids,
                adding=wagon_batch[key],
            )
        if slab_id:
            locked = conn.execute(
                "SELECT platform_zone FROM slabs WHERE id = ? AND session_id = ?",
                (int(slab_id), session_id),
            ).fetchone()
            if locked and str(locked["platform_zone"]) in KODAR_SYSTEM_ZONES:
                kept_ids.add(int(slab_id))
                full = conn.execute("SELECT * FROM slabs WHERE id = ?", (int(slab_id),)).fetchone()
                if full:
                    saved_slabs.append(_row_slab(full))
                continue
            conn.execute(
                """
                UPDATE slabs SET letter = ?, number = ?, pos_x = ?, pos_y = ?,
                    suffix = ?, weight = ?, notes = ?, on_yard = ?,
                    platform_zone = ?, wagon_number = ?, loading_date = ?
                WHERE id = ? AND session_id = ?
                """,
                (
                    letter,
                    number,
                    pos_x,
                    pos_y,
                    placement["suffix"],
                    placement["weight"],
                    placement["notes"],
                    placement["on_yard"],
                    placement["platform_zone"],
                    placement["wagon_number"],
                    placement["loading_date"],
                    int(slab_id),
                    session_id,
                ),
            )
            kept_ids.add(int(slab_id))
            new_id = int(slab_id)
        else:
            slab_cur = conn.execute(
                """
                INSERT INTO slabs
                    (session_id, letter, number, pos_x, pos_y, suffix, weight, notes,
                     on_yard, created_at, platform_zone, wagon_number, loading_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    letter,
                    number,
                    pos_x,
                    pos_y,
                    placement["suffix"],
                    placement["weight"],
                    placement["notes"],
                    placement["on_yard"],
                    now,
                    placement["platform_zone"],
                    placement["wagon_number"],
                    placement["loading_date"],
                ),
            )
            new_id = int(slab_cur.lastrowid)
            kept_ids.add(new_id)

        row = conn.execute("SELECT * FROM slabs WHERE id = ?", (new_id,)).fetchone()
        if row:
            saved_slabs.append(_row_slab(row))

    if kept_ids:
        placeholders = ",".join("?" * len(kept_ids))
        conn.execute(
            f"DELETE FROM slabs WHERE session_id = ? AND id NOT IN ({placeholders})",
            (session_id, *kept_ids),
        )
    else:
        raise ValueError("Нужна хотя бы одна плита")
    return saved_slabs


def create_session(
    *,
    unload_date: str,
    trn: str,
    vehicle_id: int | None,
    driver: str,
    crane_start: str,
    crane_end: str,
    crane_minutes: int | None,
    riggers_count: int,
    riggers_pay: int,
    taxi_pay: int,
    notes: str,
    slabs: list[dict],
    operator: str = "",
    status: str = SESSION_COMPLETED,
    unload_datetime: str = "",
) -> dict:
    if not slabs:
        raise ValueError("Нужна хотя бы одна плита")
    if status not in (SESSION_DRAFT, SESSION_COMPLETED):
        raise ValueError(f"Недопустимый статус: {status}")
    now = time.time()
    crane_end_ts = _session_crane_end_ts(crane_end)
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO unload_sessions
                (unload_date, trn, vehicle_id, driver, crane_start, crane_end,
                 crane_minutes, riggers_count, riggers_pay, taxi_pay, notes,
                 created_at, operator, revision, updated_at, status, unload_datetime,
                 crane_end_recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
            """,
            (
                unload_date,
                trn,
                vehicle_id,
                driver,
                crane_start,
                crane_end,
                crane_minutes,
                riggers_count,
                riggers_pay,
                taxi_pay,
                notes,
                now,
                (operator or "").strip(),
                now,
                status,
                unload_datetime,
                crane_end_ts,
            ),
        )
        session_id = cur.lastrowid
        _validate_session_placements(conn, slabs, session_id=session_id)
        saved_slabs = _persist_session_slabs(conn, session_id, slabs, now=now)

        session_row = conn.execute(
            "SELECT * FROM unload_sessions WHERE id = ?", (session_id,)
        ).fetchone()

    result = _row_session(session_row)
    result["slabs"] = saved_slabs
    if vehicle_id:
        veh = get_vehicle(vehicle_id)
        if veh:
            result["vehicle"] = veh
    return result


def list_sessions_for_date(unload_date: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT s.*, v.plate AS vehicle_plate, v.brand AS vehicle_brand,
                   v.driver AS vehicle_driver
            FROM unload_sessions s
            LEFT JOIN vehicles v ON v.id = s.vehicle_id
            WHERE s.unload_date = ?
            ORDER BY s.id ASC
            """,
            (unload_date,),
        ).fetchall()
        out: list[dict] = []
        for row in rows:
            item = _row_session(row)
            if row["vehicle_plate"]:
                item["vehicle"] = {
                    "plate": row["vehicle_plate"],
                    "brand": row["vehicle_brand"] or "",
                    "driver": row["vehicle_driver"] or "",
                }
            slabs = conn.execute(
                "SELECT * FROM slabs WHERE session_id = ? ORDER BY letter, id",
                (row["id"],),
            ).fetchall()
            item["slabs"] = [_row_slab(s) for s in slabs]
            out.append(item)
    return out


def stats_for_date(unload_date: str) -> dict:
    with _connect() as conn:
        sessions = conn.execute(
            "SELECT COUNT(*) FROM unload_sessions WHERE unload_date = ?",
            (unload_date,),
        ).fetchone()[0]
        slabs = conn.execute(
            """
            SELECT COUNT(*) FROM slabs
            JOIN unload_sessions ON unload_sessions.id = slabs.session_id
            WHERE unload_sessions.unload_date = ?
            """,
            (unload_date,),
        ).fetchone()[0]
    return {"date": unload_date, "sessions": int(sessions), "slabs": int(slabs)}


def wagon_zone_counts(*, conn: sqlite3.Connection | None = None) -> dict[tuple[str, str], int]:
    sql = """
        SELECT platform_zone, wagon_number, COUNT(*) AS cnt
        FROM slabs
        WHERE platform_zone IN ('ГРУЗОВОЙ', 'ТУРАН') AND wagon_number != ''
        GROUP BY platform_zone, wagon_number
    """
    if conn is not None:
        rows = conn.execute(sql).fetchall()
    else:
        with _connect() as c:
            rows = c.execute(sql).fetchall()
    return {(str(r[0]), str(r[1])): int(r[2]) for r in rows}


def get_wagon_load_info(wagon_number: str, zone: str) -> dict:
    wagon_number = (wagon_number or "").strip()
    zone = (zone or "").strip().upper()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT letter, number, suffix, loading_date
            FROM slabs
            WHERE wagon_number = ? AND platform_zone = ?
            ORDER BY letter, CAST(number AS INTEGER), number, id
            """,
            (wagon_number, zone),
        ).fetchall()
    labels: list[str] = []
    last_loading = ""
    last_dt: datetime | None = None
    for row in rows:
        labels.append(_slab_label(row["letter"], row["number"], row["suffix"]))
        loading_raw = (row["loading_date"] or "").strip()
        loading_dt = _parse_loading_datetime(loading_raw)
        if loading_dt and (last_dt is None or loading_dt > last_dt):
            last_dt = loading_dt
            last_loading = loading_raw
    return {
        "wagon_number": wagon_number,
        "zone": zone,
        "count": len(labels),
        "max": MAX_WAGON_SLABS,
        "labels": labels,
        "last_loading": last_loading,
    }


def list_wagon_loads_for_daily_report(
    report_date: str,
    *,
    end_hour: int = 16,
    end_minute: int = 0,
) -> list[dict]:
    """Вагоны с погрузкой за календарный день до end_hour:end_minute (МСК)."""
    try:
        day = date.fromisoformat(report_date)
    except ValueError:
        return []
    tz = report_tz()
    window_end = datetime.combine(day, dt_time(end_hour % 24, end_minute % 60), tzinfo=tz)

    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT slabs.platform_zone, slabs.wagon_number, slabs.letter, slabs.number,
                   slabs.suffix, slabs.loading_date, vehicles.plate AS vehicle_plate
            FROM slabs
            JOIN unload_sessions ON unload_sessions.id = slabs.session_id
            LEFT JOIN vehicles ON vehicles.id = unload_sessions.vehicle_id
            WHERE slabs.platform_zone IN ('ГРУЗОВОЙ', 'ТУРАН') AND slabs.wagon_number != ''
            """
        ).fetchall()

    wagons: dict[tuple[str, str], dict] = {}
    activity_keys: set[tuple[str, str]] = set()

    for row in rows:
        key = (str(row["platform_zone"]), str(row["wagon_number"]))
        if key not in wagons:
            wagons[key] = {
                "zone": key[0],
                "wagon_number": key[1],
                "labels": [],
                "blocks": [],
                "last_loading": "",
                "last_dt": None,
            }
        label = _slab_label(row["letter"], row["number"], row["suffix"])
        wagons[key]["labels"].append(label)
        wagons[key]["blocks"].append(
            {
                "label": label,
                "vehicle_plate": (row["vehicle_plate"] or "").strip(),
            }
        )
        loading_raw = (row["loading_date"] or "").strip()
        loading_dt = _parse_loading_datetime(loading_raw)
        if loading_dt and loading_dt.date() == day and loading_dt <= window_end:
            activity_keys.add(key)
            info = wagons[key]
            if info["last_dt"] is None or loading_dt > info["last_dt"]:
                info["last_dt"] = loading_dt
                info["last_loading"] = loading_raw

    out: list[dict] = []
    for key in sorted(activity_keys, key=lambda k: (k[0], k[1])):
        info = wagons[key]
        out.append(
            {
                "wagon_number": info["wagon_number"],
                "zone": info["zone"],
                "count": len(info["labels"]),
                "max": MAX_WAGON_SLABS,
                "labels": info["labels"],
                "blocks": info["blocks"],
                "last_loading": info["last_loading"],
            }
        )
    return out


def wagon_numbers_for_session(
    session_id: int,
    *,
    conn: sqlite3.Connection | None = None,
) -> str:
    sql = """
        SELECT DISTINCT wagon_number FROM slabs
        WHERE session_id = ? AND wagon_number != ''
        ORDER BY wagon_number
    """
    if conn is not None:
        rows = conn.execute(sql, (session_id,)).fetchall()
    else:
        with _connect() as c:
            rows = c.execute(sql, (session_id,)).fetchall()
    return ", ".join(str(r[0]).strip() for r in rows if str(r[0]).strip())


def count_sessions() -> int:
    with _connect() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM unload_sessions").fetchone()[0])


def list_sessions(*, limit: int = 30, offset: int = 0) -> list[dict]:
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT s.*, v.plate AS vehicle_plate
            FROM unload_sessions s
            LEFT JOIN vehicles v ON v.id = s.vehicle_id
            ORDER BY s.unload_date DESC, s.id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        out = []
        for row in rows:
            item = _row_session(row)
            item["vehicle_plate"] = row["vehicle_plate"]
            cnt = conn.execute(
                "SELECT COUNT(*) FROM slabs WHERE session_id = ?", (row["id"],)
            ).fetchone()[0]
            item["slab_count"] = cnt
            item["wagon_numbers"] = wagon_numbers_for_session(row["id"], conn=conn)
            out.append(item)
    return out


def get_session(session_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM unload_sessions WHERE id = ?", (session_id,)).fetchone()
        if not row:
            return None
        slabs = conn.execute(
            "SELECT * FROM slabs WHERE session_id = ? ORDER BY letter, id", (session_id,)
        ).fetchall()
        result = _row_session(row)
        if row["vehicle_id"]:
            veh = get_vehicle(row["vehicle_id"])
            if veh:
                result["vehicle"] = veh
        result["slabs"] = [_row_slab(s) for s in slabs]
        result["wagon_numbers"] = wagon_numbers_for_session(session_id, conn=conn)
        return result


def _validate_pos(pos_x: int, pos_y: int) -> None:
    if not (1 <= pos_x <= DEFAULT_GRID_X and 1 <= pos_y <= DEFAULT_GRID_Y):
        raise ValueError(
            f"Координаты: X от 1 до {DEFAULT_GRID_X}, Y от 1 до {DEFAULT_GRID_Y}"
        )


def _count_at_cell(
    conn: sqlite3.Connection,
    pos_x: int,
    pos_y: int,
    *,
    exclude_slab_ids: set[int] | None = None,
    exclude_session_id: int | None = None,
) -> int:
    exclude_slab_ids = exclude_slab_ids or set()
    clauses = ["on_yard = 1", "pos_x = ?", "pos_y = ?"]
    params: list = [pos_x, pos_y]
    if exclude_session_id is not None:
        clauses.append("session_id != ?")
        params.append(exclude_session_id)
    if exclude_slab_ids:
        placeholders = ",".join("?" * len(exclude_slab_ids))
        clauses.append(f"id NOT IN ({placeholders})")
        params.extend(exclude_slab_ids)
    sql = f"SELECT COUNT(*) FROM slabs WHERE {' AND '.join(clauses)}"
    return int(conn.execute(sql, params).fetchone()[0])


def _ensure_cell_capacity(
    conn: sqlite3.Connection,
    pos_x: int,
    pos_y: int,
    *,
    exclude_slab_ids: set[int] | None = None,
    exclude_session_id: int | None = None,
    adding: int = 1,
) -> None:
    current = _count_at_cell(
        conn,
        pos_x,
        pos_y,
        exclude_slab_ids=exclude_slab_ids,
        exclude_session_id=exclude_session_id,
    )
    if current + adding > MAX_SLABS_PER_CELL:
        raise ValueError(
            f"Ячейка {pos_x}/{pos_y}: максимум {MAX_SLABS_PER_CELL} плиты "
            f"(сейчас {current}, добавляете {adding})"
        )


def _find_session_slab_id(
    conn: sqlite3.Connection,
    session_id: int,
    letter: str,
    number: str,
) -> int | None:
    row = conn.execute(
        """
        SELECT id FROM slabs
        WHERE session_id = ? AND letter = ? AND number = ?
        ORDER BY id ASC LIMIT 1
        """,
        (session_id, letter, number),
    ).fetchone()
    return int(row[0]) if row else None


def _validate_session_placements(
    conn: sqlite3.Connection,
    slabs: list[dict],
    *,
    session_id: int,
) -> None:
    """Проверка лимита 4 плиты/ячейку с учётом других выгрузок."""
    kept_ids = {int(s["id"]) for s in slabs if s.get("id")}
    by_cell: dict[tuple[int, int], int] = {}

    for s in slabs:
        number = (s.get("number") or "").strip()
        if not number or number == "0":
            continue
        try:
            placement = _resolve_slab_placement(s)
        except ValueError:
            continue
        if not is_placed(placement["pos_x"], placement["pos_y"]):
            continue
        pos_x = placement["pos_x"]
        pos_y = placement["pos_y"]
        key = (pos_x, pos_y)
        by_cell[key] = by_cell.get(key, 0) + 1
        if by_cell[key] > MAX_SLABS_PER_CELL:
            raise ValueError(
                f"Ячейка {pos_x}/{pos_y}: максимум {MAX_SLABS_PER_CELL} плиты в одной выгрузке"
            )

    for (pos_x, pos_y), batch_count in by_cell.items():
        external = _count_at_cell(
            conn, pos_x, pos_y, exclude_session_id=session_id
        )
        if external + batch_count > MAX_SLABS_PER_CELL:
            raise ValueError(
                f"Ячейка {pos_x}/{pos_y}: максимум {MAX_SLABS_PER_CELL} плиты "
                f"(другие выгрузки: {external}, в этой машине: {batch_count})"
            )


def wipe_operational_data() -> None:
    """Очистить выгрузки и плиты (машины остаются)."""
    with _connect() as conn:
        conn.execute("DELETE FROM slabs")
        conn.execute("DELETE FROM unload_sessions")


def find_or_create_vehicle(conn: sqlite3.Connection, plate: str, driver: str = "") -> int:
    norm = re.sub(r"\s+", " ", (plate or "").strip().lower())
    rows = conn.execute("SELECT id, plate FROM vehicles").fetchall()
    for row in rows:
        p = re.sub(r"\s+", " ", row["plate"].strip().lower())
        if norm in p or p in norm or norm == p:
            return int(row["id"])
    cur = conn.execute(
        "INSERT INTO vehicles (plate, brand, driver, sort_order) VALUES (?, '', ?, 99)",
        (plate.strip(), (driver or "").strip()),
    )
    return int(cur.lastrowid)


def yard_map(*, only_on_yard: bool = True) -> dict:
    clause = "WHERE on_yard = 1" if only_on_yard else ""
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT slabs.*, unload_sessions.unload_date, unload_sessions.driver,
                   vehicles.plate AS vehicle_plate
            FROM slabs
            JOIN unload_sessions ON unload_sessions.id = slabs.session_id
            LEFT JOIN vehicles ON vehicles.id = unload_sessions.vehicle_id
            {clause}
            ORDER BY slabs.pos_y, slabs.pos_x, slabs.id
            """
        ).fetchall()

    cells: dict[str, list[dict]] = {}
    for row in rows:
        slab = _row_slab(row)
        if not is_placed(slab["pos_x"], slab["pos_y"]):
            continue
        slab["unload_date"] = row["unload_date"]
        slab["driver"] = row["driver"]
        slab["vehicle_plate"] = row["vehicle_plate"]
        key = f"{slab['pos_x']}/{slab['pos_y']}"
        cells.setdefault(key, []).append(slab)

    return {"grid_x": DEFAULT_GRID_X, "grid_y": DEFAULT_GRID_Y, "cells": cells}


def list_registry_rows(*, date_from: str | None = None, date_to: str | None = None) -> list[dict]:
    """Строки для Excel-реестра (как в вашем файле)."""
    clauses: list[str] = []
    params: list = []
    if date_from:
        clauses.append("unload_sessions.unload_date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("unload_sessions.unload_date <= ?")
        params.append(date_to)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT slabs.*,
                   unload_sessions.id AS session_id,
                   unload_sessions.unload_date, unload_sessions.unload_datetime,
                   unload_sessions.trn,
                   unload_sessions.driver, unload_sessions.crane_start,
                   unload_sessions.crane_end, unload_sessions.crane_minutes,
                   unload_sessions.riggers_pay, unload_sessions.taxi_pay,
                   unload_sessions.notes AS session_notes,
                   vehicles.plate AS vehicle_plate
            FROM slabs
            JOIN unload_sessions ON unload_sessions.id = slabs.session_id
            LEFT JOIN vehicles ON vehicles.id = unload_sessions.vehicle_id
            {where}
            ORDER BY unload_sessions.unload_date DESC, unload_sessions.id DESC, slabs.letter, slabs.id
            """,
            params,
        ).fetchall()
    out = []
    for row in rows:
        slab = _row_slab(row)
        out.append(
            {
                **slab,
                "unload_date": row["unload_date"],
                "unload_datetime": row["unload_datetime"] if "unload_datetime" in row.keys() else "",
                "session_id": row["session_id"],
                "trn": row["trn"],
                "driver": row["driver"],
                "vehicle_plate": row["vehicle_plate"],
                "crane_start": row["crane_start"],
                "crane_end": row["crane_end"],
                "crane_minutes": row["crane_minutes"],
                "riggers_pay": row["riggers_pay"],
                "taxi_pay": row["taxi_pay"],
                "session_notes": row["session_notes"],
            }
        )
    return out


def get_slab(slab_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM slabs WHERE id = ?", (slab_id,)).fetchone()
    if not row:
        return None
    slab = _row_slab(row)
    slab["session_id"] = row["session_id"]
    return slab


def update_session(
    session_id: int,
    *,
    unload_date: str,
    trn: str,
    vehicle_id: int | None,
    driver: str,
    crane_start: str,
    crane_end: str,
    crane_minutes: int | None,
    riggers_count: int,
    riggers_pay: int,
    taxi_pay: int,
    notes: str,
    slabs: list[dict],
    operator: str = "",
    expected_revision: int | None = None,
    status: str | None = None,
    unload_datetime: str | None = None,
) -> dict:
    if not slabs:
        raise ValueError("Нужна хотя бы одна плита")
    now = time.time()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM unload_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not row:
            raise KeyError("session not found")
        keys = row.keys()
        current_rev = int(row["revision"]) if "revision" in keys else 1
        if expected_revision is not None and expected_revision != current_rev:
            raise TaksimoConflictError(
                f"Выгрузку уже изменили (версия {current_rev}). Откройте журнал заново."
            )
        new_rev = current_rev + 1
        prev_crane_end = row["crane_end"] if "crane_end" in keys else ""
        next_status = status or (row["status"] if "status" in keys else SESSION_COMPLETED)
        if next_status not in (SESSION_DRAFT, SESSION_COMPLETED):
            raise ValueError(f"Недопустимый статус: {next_status}")
        crane_end_ts = _session_crane_end_ts(crane_end, previous=prev_crane_end)
        if crane_end_ts is None and (crane_end or "").strip():
            crane_end_ts = row["crane_end_recorded_at"] if "crane_end_recorded_at" in keys else None
        unload_dt = unload_datetime
        if unload_dt is None and "unload_datetime" in keys:
            unload_dt = row["unload_datetime"]
        conn.execute(
            """
            UPDATE unload_sessions SET
                unload_date = ?, trn = ?, vehicle_id = ?, driver = ?,
                crane_start = ?, crane_end = ?, crane_minutes = ?,
                riggers_count = ?, riggers_pay = ?, taxi_pay = ?, notes = ?,
                operator = ?, revision = ?, updated_at = ?, status = ?,
                unload_datetime = ?, crane_end_recorded_at = COALESCE(?, crane_end_recorded_at)
            WHERE id = ?
            """,
            (
                unload_date,
                trn,
                vehicle_id,
                driver,
                crane_start,
                crane_end,
                crane_minutes,
                riggers_count,
                riggers_pay,
                taxi_pay,
                notes,
                (operator or "").strip(),
                new_rev,
                now,
                next_status,
                unload_dt or "",
                crane_end_ts,
                session_id,
            ),
        )
        _validate_session_placements(conn, slabs, session_id=session_id)
        _persist_session_slabs(conn, session_id, slabs, now=now)

    result = get_session(session_id)
    if result is None:
        raise KeyError("session not found")
    return result


def delete_session(session_id: int) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT id FROM unload_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM slabs WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM unload_sessions WHERE id = ?", (session_id,))
    return True


def update_slab(slab_id: int, *, data: dict) -> dict:
    letter = (data.get("letter") or "").strip().upper()
    number = (data.get("number") or "").strip()
    if not letter or not number:
        raise ValueError("буква и номер обязательны")
    if letter not in SLAB_LETTERS:
        raise ValueError(f"Недопустимая буква: {letter}")

    old = get_slab(slab_id)
    if old is None:
        raise KeyError("slab not found")

    merged = {**old, **data, "letter": letter, "number": number}
    if data.get("on_yard") is False and not data.get("platform_zone"):
        merged["platform_zone"] = old.get("platform_zone") or "ХРАНЕНИЯ"
    new_zone = _normalize_platform(merged.get("platform_zone"))
    old_zone = old.get("platform_zone") or "ХРАНЕНИЯ"
    if new_zone in KODAR_SYSTEM_ZONES and new_zone != old_zone:
        raise ValueError("Статус Кодар — только через кнопки оператора 1")
    if old_zone in KODAR_SYSTEM_ZONES and new_zone != old_zone:
        raise ValueError("Плита в цикле Кодар — статус меняет только оператор 1")
    placement = _resolve_slab_placement(merged, old=old)

    with _connect() as conn:
        row = conn.execute("SELECT id FROM slabs WHERE id = ?", (slab_id,)).fetchone()
        if not row:
            raise KeyError("slab not found")
        if is_placed(placement["pos_x"], placement["pos_y"]):
            _ensure_cell_capacity(
                conn,
                placement["pos_x"],
                placement["pos_y"],
                exclude_slab_ids={slab_id},
                adding=1,
            )
        if placement["wagon_number"]:
            _ensure_zone_wagon_slot(
                conn,
                placement["platform_zone"],
                placement["wagon_number"],
                require_existing_slot=(
                    old_zone == "ХРАНЕНИЯ" and placement["platform_zone"] in WAGON_ZONES
                ),
            )
            _ensure_wagon_capacity(
                conn,
                placement["wagon_number"],
                zone=placement["platform_zone"],
                exclude_slab_ids={slab_id},
                adding=1,
            )
        conn.execute(
            """
            UPDATE slabs SET letter = ?, number = ?, pos_x = ?, pos_y = ?,
                suffix = ?, weight = ?, notes = ?, on_yard = ?,
                platform_zone = ?, wagon_number = ?, loading_date = ?
            WHERE id = ?
            """,
            (
                letter,
                number,
                placement["pos_x"],
                placement["pos_y"],
                placement["suffix"],
                placement["weight"],
                placement["notes"],
                placement["on_yard"],
                placement["platform_zone"],
                placement["wagon_number"],
                placement["loading_date"],
                slab_id,
            ),
        )
    result = get_slab(slab_id)
    if result is None:
        raise KeyError("slab not found")
    result["_old_platform_zone"] = old.get("platform_zone")
    return result


def delete_slab(slab_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM slabs WHERE id = ?", (slab_id,))
        return cur.rowcount > 0


def parse_slab_query(query: str) -> tuple[str | None, str | None]:
    """Разбор: B4415, B 4415, A, 4415, 2563, где плита K4082."""
    raw = (query or "").strip().upper().translate(_CYR_TO_LAT)
    raw = re.sub(r"^(?:ГДЕ\s+)?(?:ПЛИТА\s+)?", "", raw)
    raw = re.sub(r"[^A-Z0-9\s]", "", raw).strip()

    m = re.match(r"^([A-FK])\s*(\d+)$", raw)
    if m:
        return m.group(1), m.group(2)

    m = re.search(r"\b([A-FK])\s*(\d+)\b", raw)
    if m:
        return m.group(1), m.group(2)

    if re.fullmatch(r"[A-FK]", raw):
        return raw, None

    if re.fullmatch(r"\d+", raw):
        return None, raw

    return None, None


def parse_wagon_query(query: str) -> str | None:
    raw = (query or "").strip()
    if not raw:
        return None
    m = re.match(r"(?:вагон|vagon|wagon)\s*[:#]?\s*(\S+)", raw, re.I)
    if m:
        return m.group(1).strip()
    return None


def search_wagons(wagon_number: str, *, limit: int = 40) -> list[dict]:
    wagon_number = (wagon_number or "").strip()
    if not wagon_number:
        return []
    sql = """
        SELECT slabs.*, unload_sessions.unload_date, vehicles.plate AS vehicle_plate
        FROM slabs
        JOIN unload_sessions ON unload_sessions.id = slabs.session_id
        LEFT JOIN vehicles ON vehicles.id = unload_sessions.vehicle_id
        WHERE slabs.wagon_number LIKE ?
        ORDER BY slabs.loading_date DESC, slabs.id DESC
        LIMIT ?
    """
    with _connect() as conn:
        rows = conn.execute(sql, (f"%{wagon_number}%", limit)).fetchall()
    out = []
    for row in rows:
        slab = _row_slab(row)
        slab["unload_date"] = row["unload_date"]
        slab["vehicle_plate"] = row["vehicle_plate"]
        out.append(slab)
    return out


def unified_search(query: str, *, limit: int = 40) -> dict:
    wagon = parse_wagon_query(query)
    if wagon:
        results = search_wagons(wagon, limit=limit)
        return {"type": "wagon", "wagon": wagon, "results": results}
    results = search_slabs(query, limit=limit, yard_only=False)
    if results:
        return {"type": "slab", "results": results}
    wagon_guess = (query or "").strip()
    if wagon_guess.isdigit() and len(wagon_guess) >= 5:
        results = search_wagons(wagon_guess, limit=limit)
        if results:
            return {"type": "wagon", "wagon": wagon_guess, "results": results}
    return {"type": "slab", "results": []}


def search_slabs(query: str, *, limit: int = 40, yard_only: bool = True) -> list[dict]:
    letter, number = parse_slab_query(query)
    if not letter and not number:
        return []

    clauses: list[str] = []
    params: list = []
    if yard_only:
        clauses.append("slabs.on_yard = 1")

    if letter and number:
        clauses.append("slabs.letter = ? AND slabs.number LIKE ?")
        params.extend([letter, f"%{number}%"])
    elif number:
        clauses.append("slabs.number LIKE ?")
        params.append(f"%{number}%")
    else:
        clauses.append("slabs.letter = ?")
        params.append(letter)

    where = " AND ".join(clauses)
    sql = f"""
        SELECT slabs.*, unload_sessions.unload_date, vehicles.plate AS vehicle_plate
        FROM slabs
        JOIN unload_sessions ON unload_sessions.id = slabs.session_id
        LEFT JOIN vehicles ON vehicles.id = unload_sessions.vehicle_id
        WHERE {where}
        ORDER BY slabs.id DESC LIMIT ?
    """
    params.append(limit)

    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()

    out = []
    for row in rows:
        slab = _row_slab(row)
        slab["unload_date"] = row["unload_date"]
        slab["vehicle_plate"] = row["vehicle_plate"]
        out.append(slab)
    return out


def find_slabs(query: str, *, limit: int = 8) -> list[dict]:
    """Сначала на площадке, затем в журнале (снятые)."""
    on_yard = search_slabs(query, limit=limit, yard_only=True)
    if on_yard:
        return on_yard
    return search_slabs(query, limit=limit, yard_only=False)


def validate_session_complete(crane_start: str, crane_end: str) -> None:
    if not (crane_start or "").strip():
        raise ValueError("Укажите время «кран с» (*)")
    if not (crane_end or "").strip():
        raise ValueError("Укажите время «кран до» (*)")


def mark_session_started_notified(session_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE unload_sessions SET started_notified = 1 WHERE id = ?",
            (session_id,),
        )


def mark_session_completed_notified(session_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE unload_sessions SET completed_notified = 1 WHERE id = ?",
            (session_id,),
        )


def mark_session_departure_notified(session_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE unload_sessions SET departure_notified = 1 WHERE id = ?",
            (session_id,),
        )


def list_wagon_pool() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, number, active, sort_order FROM wagon_pool
            WHERE active = 1
            ORDER BY sort_order, number
            """
        ).fetchall()
    return [
        {
            "id": row["id"],
            "number": row["number"],
            "active": bool(row["active"]),
            "sort_order": row["sort_order"],
        }
        for row in rows
    ]


def add_wagon_numbers(numbers: list[str]) -> int:
    added = 0
    with _connect() as conn:
        for raw in numbers:
            number = (raw or "").strip()
            if not number:
                continue
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO wagon_pool (number, sort_order)
                VALUES (?, (SELECT COALESCE(MAX(sort_order), 0) + 1 FROM wagon_pool))
                """,
                (number,),
            )
            if cur.rowcount:
                added += 1
    return added


def _slot_slabs(conn: sqlite3.Connection, zone: str, wagon_number: str) -> list[dict]:
    if not wagon_number:
        return []
    rows = conn.execute(
        """
        SELECT slabs.*, unload_sessions.unload_date, vehicles.plate AS vehicle_plate
        FROM slabs
        JOIN unload_sessions ON unload_sessions.id = slabs.session_id
        LEFT JOIN vehicles ON vehicles.id = unload_sessions.vehicle_id
        WHERE slabs.platform_zone = ? AND slabs.wagon_number = ?
        ORDER BY slabs.letter, slabs.number, slabs.id
        """,
        (zone, wagon_number),
    ).fetchall()
    out = []
    for row in rows:
        slab = _row_slab(row)
        slab["unload_date"] = row["unload_date"]
        slab["vehicle_plate"] = row["vehicle_plate"]
        out.append(slab)
    return out


def _slot_is_complete(expected: list[str], actual: list[dict]) -> bool:
    if not expected:
        return False
    actual_labels = {
        _slab_label_compact(s["letter"], s["number"]) for s in actual
    }
    expected_set = set(expected)
    return actual_labels == expected_set and len(actual_labels) == len(expected_set)


def wagon_plan() -> dict:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, zone, slot_index, wagon_number, expected_blocks, updated_at
            FROM wagon_slots
            ORDER BY zone, slot_index
            """
        ).fetchall()
        dead_ends: dict[str, list[dict]] = {z: [] for z in WAGON_DEAD_ENDS}
        for row in rows:
            expected = _normalize_expected_blocks(row["expected_blocks"])
            wagon_number = (row["wagon_number"] or "").strip()
            slabs = _slot_slabs(conn, row["zone"], wagon_number)
            dead_ends[row["zone"]].append(
                {
                    "id": row["id"],
                    "slot_index": row["slot_index"],
                    "wagon_number": wagon_number,
                    "expected_blocks": expected,
                    "slabs": slabs,
                    "slab_count": len(slabs),
                    "is_complete": _slot_is_complete(expected, slabs),
                    "updated_at": row["updated_at"],
                }
            )
    return {
        "max_wagons_per_dead_end": MAX_WAGONS_PER_DEAD_END,
        "max_slabs_per_wagon": MAX_WAGON_SLABS,
        "dead_ends": dead_ends,
        "wagon_pool": list_wagon_pool(),
    }


def update_wagon_slot(
    slot_id: int,
    *,
    wagon_number: str | None = None,
    expected_blocks: list[str] | str | None = None,
) -> dict:
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, zone, slot_index FROM wagon_slots WHERE id = ?", (slot_id,)
        ).fetchone()
        if not row:
            raise KeyError("slot not found")


def upsert_vehicle_by_tail(
    *,
    plate: str,
    driver: str = "",
    tail: str = "",
    active: bool = True,
) -> None:
    plate = (plate or "").strip()
    if not plate:
        return
    with _connect() as conn:
        find_or_create_vehicle(conn, plate, driver=(driver or "").strip())


def sync_vehicles_from_drivers_registry() -> None:
    import json
    from pathlib import Path

    reg_path = Path(__file__).resolve().parent.parent / "data" / "drivers_registry.json"
    if not reg_path.is_file():
        return
    try:
        data = json.loads(reg_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    with _connect() as conn:
        for item in data.get("drivers") or []:
            if not isinstance(item, dict):
                continue
            plate = str(item.get("taksimo_plate") or "").strip()
            if not plate:
                continue
            driver = str(item.get("name") or "").strip()
            find_or_create_vehicle(conn, plate, driver=driver)


from taksimo_store_fleet import (  # noqa: E402
    CYCLE_DESTINATION,
    MAX_FLEET_WAGONS,
    WAGON_STAGE_LABELS,
    add_fleet_wagons,
    confirm_kodar_received,
    dispatch_wagon_to_kodar,
    get_wagon_card,
    get_wagon_dispatch,
    list_kodar_in_transit,
    list_wagon_cards,
    list_wagon_dispatch_history,
    list_wagon_fleet,
    update_wagon_planned_zone,
    wagon_plan as _fleet_wagon_plan,
    update_wagon_slot as _fleet_update_wagon_slot,
)

wagon_plan = _fleet_wagon_plan
update_wagon_slot = _fleet_update_wagon_slot

from taksimo_store_materials import (  # noqa: E402
    MATERIAL_UNITS,
    add_material_receipt,
    add_template_item,
    adjust_material_stock,
    auto_writeoff_for_dispatch,
    assign_template_to_wagon,
    create_material_item,
    create_material_template,
    finalize_wagon_materials,
    get_material_item,
    get_wagon_materials,
    list_material_items,
    list_material_templates,
    materials_dashboard,
    reserve_material_for_wagon,
    update_material_item,
)
