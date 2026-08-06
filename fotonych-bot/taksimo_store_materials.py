"""Расходники и резервы мастера отгрузки."""

from __future__ import annotations

import sqlite3
import time

import taksimo_store as store

_connect = store._connect

MATERIAL_UNITS = ("шт", "кг", "м", "пачка", "рулон")
MOVE_RECEIPT = "receipt"
MOVE_RESERVE = "reserve"
MOVE_RELEASE = "release"
MOVE_CONSUME = "consume"
MOVE_CONSUME_RESERVED = "consume_reserved"
MOVE_ADJUST = "adjust"
SCHEME_CODE_DEFAULT = "scheme1"
SCHEME_CODES = {"scheme1", "scheme2", "scheme3"}
RETURN_STATUS_PLANNED = "planned_return"
RETURN_STATUS_IN_TRANSIT = "in_transit_back"
RETURN_STATUS_RETURNED = "returned_to_zone"


def migrate_materials(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS material_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            unit TEXT NOT NULL DEFAULT 'шт',
            min_level REAL NOT NULL DEFAULT 0,
            norm_per_wagon REAL NOT NULL DEFAULT 0,
            sort_order INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS material_movements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            material_id INTEGER NOT NULL,
            move_type TEXT NOT NULL,
            quantity REAL NOT NULL DEFAULT 0,
            on_hand_delta REAL NOT NULL DEFAULT 0,
            reserved_delta REAL NOT NULL DEFAULT 0,
            wagon_number TEXT NOT NULL DEFAULT '',
            template_id INTEGER NOT NULL DEFAULT 0,
            zone TEXT NOT NULL DEFAULT '',
            dispatch_id INTEGER,
            operator TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            FOREIGN KEY (material_id) REFERENCES material_items(id)
        );

        CREATE INDEX IF NOT EXISTS idx_material_movements_material
            ON material_movements(material_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_material_movements_wagon
            ON material_movements(wagon_number, created_at);

        CREATE TABLE IF NOT EXISTS material_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL DEFAULT '',
            scheme_code TEXT NOT NULL DEFAULT 'scheme1',
            has_box INTEGER NOT NULL DEFAULT 0,
            returns_materials INTEGER NOT NULL DEFAULT 0,
            extra_ring_mode TEXT NOT NULL DEFAULT '',
            extra_units INTEGER NOT NULL DEFAULT 0,
            k_goal INTEGER NOT NULL DEFAULT 0,
            box_capacity_wagons INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS material_template_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            template_id INTEGER NOT NULL,
            material_id INTEGER NOT NULL,
            norm_qty REAL NOT NULL DEFAULT 0,
            sort_order INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (template_id) REFERENCES material_templates(id),
            FOREIGN KEY (material_id) REFERENCES material_items(id)
        );

        CREATE TABLE IF NOT EXISTS master_wagon_prep (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wagon_number TEXT NOT NULL,
            template_id INTEGER,
            scheme_code TEXT NOT NULL DEFAULT 'scheme1',
            has_box INTEGER NOT NULL DEFAULT 0,
            returns_materials INTEGER NOT NULL DEFAULT 0,
            extra_ring_mode TEXT NOT NULL DEFAULT '',
            extra_units INTEGER NOT NULL DEFAULT 0,
            k_goal INTEGER NOT NULL DEFAULT 0,
            box_capacity_wagons INTEGER NOT NULL DEFAULT 0,
            origin_zone TEXT NOT NULL DEFAULT '',
            prep_status TEXT NOT NULL DEFAULT 'draft',
            return_status TEXT NOT NULL DEFAULT '',
            return_target_zone TEXT NOT NULL DEFAULT '',
            actual_return_zone TEXT NOT NULL DEFAULT '',
            return_updated_at REAL,
            prepared_by TEXT NOT NULL DEFAULT '',
            prepared_at REAL,
            note TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY (template_id) REFERENCES material_templates(id)
        );

        CREATE TABLE IF NOT EXISTS master_wagon_prep_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prep_id INTEGER NOT NULL,
            template_item_id INTEGER,
            material_id INTEGER NOT NULL,
            line_no INTEGER NOT NULL DEFAULT 0,
            qty_norm REAL NOT NULL DEFAULT 0,
            work_type TEXT NOT NULL DEFAULT '',
            feature_text TEXT NOT NULL DEFAULT '',
            tool_text TEXT NOT NULL DEFAULT '',
            norm_minutes REAL NOT NULL DEFAULT 0,
            sort_order INTEGER NOT NULL DEFAULT 0,
            note TEXT NOT NULL DEFAULT '',
            FOREIGN KEY (prep_id) REFERENCES master_wagon_prep(id),
            FOREIGN KEY (template_item_id) REFERENCES material_template_items(id),
            FOREIGN KEY (material_id) REFERENCES material_items(id)
        );

        CREATE TABLE IF NOT EXISTS timing_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            template_id INTEGER,
            step_name TEXT NOT NULL,
            norm_minutes REAL NOT NULL DEFAULT 0,
            sort_order INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (template_id) REFERENCES material_templates(id)
        );
        """
    )
    template_item_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(material_template_items)")
    }
    if "line_no" not in template_item_cols:
        conn.execute(
            "ALTER TABLE material_template_items ADD COLUMN line_no INTEGER NOT NULL DEFAULT 0"
        )
    if "work_type" not in template_item_cols:
        conn.execute(
            "ALTER TABLE material_template_items ADD COLUMN work_type TEXT NOT NULL DEFAULT ''"
        )
    if "feature_text" not in template_item_cols:
        conn.execute(
            "ALTER TABLE material_template_items ADD COLUMN feature_text TEXT NOT NULL DEFAULT ''"
        )
    if "tool_text" not in template_item_cols:
        conn.execute(
            "ALTER TABLE material_template_items ADD COLUMN tool_text TEXT NOT NULL DEFAULT ''"
        )
    if "norm_minutes" not in template_item_cols:
        conn.execute(
            "ALTER TABLE material_template_items ADD COLUMN norm_minutes REAL NOT NULL DEFAULT 0"
        )
    movement_cols = {row[1] for row in conn.execute("PRAGMA table_info(material_movements)")}
    if "template_id" not in movement_cols:
        conn.execute(
            "ALTER TABLE material_movements ADD COLUMN template_id INTEGER NOT NULL DEFAULT 0"
        )
    if "zone" not in movement_cols:
        conn.execute(
            "ALTER TABLE material_movements ADD COLUMN zone TEXT NOT NULL DEFAULT ''"
        )
    template_cols = {row[1] for row in conn.execute("PRAGMA table_info(material_templates)")}
    if "scheme_code" not in template_cols:
        conn.execute(
            "ALTER TABLE material_templates ADD COLUMN scheme_code TEXT NOT NULL DEFAULT 'scheme1'"
        )
    if "has_box" not in template_cols:
        conn.execute(
            "ALTER TABLE material_templates ADD COLUMN has_box INTEGER NOT NULL DEFAULT 0"
        )
    if "returns_materials" not in template_cols:
        conn.execute(
            "ALTER TABLE material_templates ADD COLUMN returns_materials INTEGER NOT NULL DEFAULT 0"
        )
    if "extra_ring_mode" not in template_cols:
        conn.execute(
            "ALTER TABLE material_templates ADD COLUMN extra_ring_mode TEXT NOT NULL DEFAULT ''"
        )
    if "extra_units" not in template_cols:
        conn.execute(
            "ALTER TABLE material_templates ADD COLUMN extra_units INTEGER NOT NULL DEFAULT 0"
        )
    if "k_goal" not in template_cols:
        conn.execute(
            "ALTER TABLE material_templates ADD COLUMN k_goal INTEGER NOT NULL DEFAULT 0"
        )
    if "box_capacity_wagons" not in template_cols:
        conn.execute(
            "ALTER TABLE material_templates ADD COLUMN box_capacity_wagons INTEGER NOT NULL DEFAULT 0"
        )
    prep_cols = {row[1] for row in conn.execute("PRAGMA table_info(master_wagon_prep)")}
    if "scheme_code" not in prep_cols:
        conn.execute(
            "ALTER TABLE master_wagon_prep ADD COLUMN scheme_code TEXT NOT NULL DEFAULT 'scheme1'"
        )
    if "has_box" not in prep_cols:
        conn.execute(
            "ALTER TABLE master_wagon_prep ADD COLUMN has_box INTEGER NOT NULL DEFAULT 0"
        )
    if "returns_materials" not in prep_cols:
        conn.execute(
            "ALTER TABLE master_wagon_prep ADD COLUMN returns_materials INTEGER NOT NULL DEFAULT 0"
        )
    if "extra_ring_mode" not in prep_cols:
        conn.execute(
            "ALTER TABLE master_wagon_prep ADD COLUMN extra_ring_mode TEXT NOT NULL DEFAULT ''"
        )
    if "extra_units" not in prep_cols:
        conn.execute(
            "ALTER TABLE master_wagon_prep ADD COLUMN extra_units INTEGER NOT NULL DEFAULT 0"
        )
    if "k_goal" not in prep_cols:
        conn.execute(
            "ALTER TABLE master_wagon_prep ADD COLUMN k_goal INTEGER NOT NULL DEFAULT 0"
        )
    if "box_capacity_wagons" not in prep_cols:
        conn.execute(
            "ALTER TABLE master_wagon_prep ADD COLUMN box_capacity_wagons INTEGER NOT NULL DEFAULT 0"
        )
    if "origin_zone" not in prep_cols:
        conn.execute(
            "ALTER TABLE master_wagon_prep ADD COLUMN origin_zone TEXT NOT NULL DEFAULT ''"
        )
    if "return_status" not in prep_cols:
        conn.execute(
            "ALTER TABLE master_wagon_prep ADD COLUMN return_status TEXT NOT NULL DEFAULT ''"
        )
    if "return_target_zone" not in prep_cols:
        conn.execute(
            "ALTER TABLE master_wagon_prep ADD COLUMN return_target_zone TEXT NOT NULL DEFAULT ''"
        )
    if "actual_return_zone" not in prep_cols:
        conn.execute(
            "ALTER TABLE master_wagon_prep ADD COLUMN actual_return_zone TEXT NOT NULL DEFAULT ''"
        )
    if "return_updated_at" not in prep_cols:
        conn.execute(
            "ALTER TABLE master_wagon_prep ADD COLUMN return_updated_at REAL"
        )


def _normalize_quantity(value, *, allow_zero: bool = False) -> float:
    try:
        qty = round(float(value), 3)
    except (TypeError, ValueError) as e:
        raise ValueError("Количество должно быть числом") from e
    if qty < 0 or (qty == 0 and not allow_zero):
        raise ValueError("Количество должно быть больше нуля")
    return qty


def _normalize_non_negative(value, *, field_name: str) -> float:
    try:
        qty = round(float(value or 0), 3)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{field_name} должно быть числом") from e
    if qty < 0:
        raise ValueError(f"{field_name} не может быть меньше нуля")
    return qty


def _normalize_scheme_code(value) -> str:
    raw = str(value or "").strip().lower()
    aliases = {
        "1": "scheme1",
        "2": "scheme2",
        "3": "scheme3",
        "scheme1": "scheme1",
        "scheme2": "scheme2",
        "scheme3": "scheme3",
        "схема1": "scheme1",
        "схема2": "scheme2",
        "схема3": "scheme3",
    }
    code = aliases.get(raw, raw or SCHEME_CODE_DEFAULT)
    if code not in SCHEME_CODES:
        raise ValueError("Тип схемы: scheme1, scheme2 или scheme3")
    return code


def _normalize_bool_flag(value) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    text = str(value or "").strip().lower()
    return 1 if text in {"1", "true", "yes", "да", "on"} else 0


def _normalize_non_negative_int(value, *, field_name: str) -> int:
    try:
        num = int(float(value or 0))
    except (TypeError, ValueError) as e:
        raise ValueError(f"{field_name} должно быть числом") from e
    if num < 0:
        raise ValueError(f"{field_name} не может быть меньше нуля")
    return num


def _template_meta(row: sqlite3.Row | dict | None) -> dict:
    row = row or {}
    return {
        "scheme_code": _normalize_scheme_code(row.get("scheme_code") if isinstance(row, dict) else row["scheme_code"] if "scheme_code" in row.keys() else SCHEME_CODE_DEFAULT),
        "has_box": bool((row.get("has_box") if isinstance(row, dict) else row["has_box"]) or 0),
        "returns_materials": bool((row.get("returns_materials") if isinstance(row, dict) else row["returns_materials"]) or 0),
        "extra_ring_mode": (row.get("extra_ring_mode") if isinstance(row, dict) else row["extra_ring_mode"] if "extra_ring_mode" in row.keys() else "") or "",
        "extra_units": int((row.get("extra_units") if isinstance(row, dict) else row["extra_units"] if "extra_units" in row.keys() else 0) or 0),
        "k_goal": int((row.get("k_goal") if isinstance(row, dict) else row["k_goal"] if "k_goal" in row.keys() else 0) or 0),
        "box_capacity_wagons": int((row.get("box_capacity_wagons") if isinstance(row, dict) else row["box_capacity_wagons"] if "box_capacity_wagons" in row.keys() else 0) or 0),
    }


def _zone_from_location_label(label: str) -> str:
    text = (label or "").strip().upper()
    for zone in ("ТУРАН", "ГРУЗОВОЙ"):
        if text.startswith(zone):
            return zone
    return ""


def _row_material_item(row: sqlite3.Row, *, totals: dict[int, dict] | None = None) -> dict:
    totals = totals or {}
    item_totals = totals.get(int(row["id"]), {})
    on_hand = round(float(item_totals.get("on_hand") or 0), 3)
    reserved = round(float(item_totals.get("reserved") or 0), 3)
    available = round(on_hand - reserved, 3)
    norm_per_wagon = round(float(row["norm_per_wagon"] or 0), 3)
    min_level = round(float(row["min_level"] or 0), 3)
    available_wagons = None
    if norm_per_wagon > 0:
        available_wagons = max(0, int(available // norm_per_wagon))
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "unit": row["unit"] or "шт",
        "min_level": min_level,
        "norm_per_wagon": norm_per_wagon,
        "sort_order": int(row["sort_order"]),
        "active": bool(row["active"]),
        "on_hand": on_hand,
        "reserved": reserved,
        "available": available,
        "available_wagons": available_wagons,
        "low_stock": available < min_level if min_level > 0 else False,
    }


def _material_totals_map(conn: sqlite3.Connection) -> dict[int, dict]:
    rows = conn.execute(
        """
        SELECT
            material_id,
            COALESCE(SUM(on_hand_delta), 0) AS on_hand,
            COALESCE(SUM(reserved_delta), 0) AS reserved
        FROM material_movements
        GROUP BY material_id
        """
    ).fetchall()
    return {
        int(row["material_id"]): {
            "on_hand": float(row["on_hand"] or 0),
            "reserved": float(row["reserved"] or 0),
        }
        for row in rows
    }


def _material_snapshot(conn: sqlite3.Connection, material_id: int) -> dict:
    row = conn.execute(
        """
        SELECT
            COALESCE(SUM(on_hand_delta), 0) AS on_hand,
            COALESCE(SUM(reserved_delta), 0) AS reserved
        FROM material_movements
        WHERE material_id = ?
        """,
        (material_id,),
    ).fetchone()
    on_hand = round(float((row["on_hand"] if row else 0) or 0), 3)
    reserved = round(float((row["reserved"] if row else 0) or 0), 3)
    return {
        "on_hand": on_hand,
        "reserved": reserved,
        "available": round(on_hand - reserved, 3),
    }


def _current_reserved_for(
    conn: sqlite3.Connection, material_id: int, wagon_number: str
) -> float:
    row = conn.execute(
        """
        SELECT COALESCE(SUM(reserved_delta), 0) AS reserved
        FROM material_movements
        WHERE material_id = ? AND wagon_number = ?
        """,
        (material_id, wagon_number),
    ).fetchone()
    return round(float((row["reserved"] if row else 0) or 0), 3)


def _get_material_row(conn: sqlite3.Connection, material_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM material_items WHERE id = ? AND active = 1",
        (material_id,),
    ).fetchone()
    if not row:
        raise ValueError("Материал не найден")
    return row


def _material_move(
    conn: sqlite3.Connection,
    *,
    material_id: int,
    move_type: str,
    quantity: float,
    on_hand_delta: float,
    reserved_delta: float,
    wagon_number: str = "",
    template_id: int = 0,
    zone: str = "",
    dispatch_id: int | None = None,
    operator: str = "",
    note: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO material_movements (
            material_id, move_type, quantity, on_hand_delta, reserved_delta,
            wagon_number, template_id, zone, dispatch_id, operator, note, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            material_id,
            move_type,
            quantity,
            on_hand_delta,
            reserved_delta,
            (wagon_number or "").strip(),
            int(template_id or 0),
            (zone or "").strip().upper(),
            dispatch_id,
            (operator or "").strip(),
            (note or "").strip(),
            time.time(),
        ),
    )


def list_material_items() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM material_items
            WHERE active = 1
            ORDER BY sort_order, name
            """
        ).fetchall()
        totals = _material_totals_map(conn)
        return [_row_material_item(row, totals=totals) for row in rows]


def get_material_item(material_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM material_items WHERE id = ? AND active = 1",
            (material_id,),
        ).fetchone()
        if not row:
            return None
        return _row_material_item(
            row, totals={material_id: _material_snapshot(conn, material_id)}
        )


def create_material_item(
    *,
    name: str,
    unit: str,
    min_level=0,
    norm_per_wagon=0,
) -> dict:
    name = (name or "").strip()
    unit = (unit or "шт").strip() or "шт"
    if not name:
        raise ValueError("Укажите название материала")
    min_level = _normalize_non_negative(min_level, field_name="Мин. остаток")
    norm_per_wagon = _normalize_non_negative(
        norm_per_wagon, field_name="Норма на вагон"
    )
    now = time.time()
    with _connect() as conn:
        existing = conn.execute(
            "SELECT id, active FROM material_items WHERE name = ?",
            (name,),
        ).fetchone()
        if existing and int(existing["active"] or 0) == 1:
            raise ValueError("Такой материал уже есть")
        if existing:
            conn.execute(
                """
                UPDATE material_items
                SET unit = ?, min_level = ?, norm_per_wagon = ?, active = 1, updated_at = ?
                WHERE id = ?
                """,
                (unit, min_level, norm_per_wagon, now, int(existing["id"])),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM material_items WHERE id = ?",
                (int(existing["id"]),),
            ).fetchone()
        else:
            cur = conn.execute(
                """
                INSERT INTO material_items (
                    name, unit, min_level, norm_per_wagon, sort_order, created_at, updated_at
                )
                VALUES (
                    ?, ?, ?, ?,
                    (SELECT COALESCE(MAX(sort_order), 0) + 1 FROM material_items),
                    ?, ?
                )
                """,
                (name, unit, min_level, norm_per_wagon, now, now),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM material_items WHERE id = ?",
                (int(cur.lastrowid),),
            ).fetchone()
        return _row_material_item(row, totals=_material_totals_map(conn))


def update_material_item(material_id: int, data: dict) -> dict:
    with _connect() as conn:
        row = _get_material_row(conn, material_id)
        name = (data.get("name") or row["name"]).strip()
        unit = (data.get("unit") or row["unit"] or "шт").strip() or "шт"
        if not name:
            raise ValueError("Укажите название материала")
        duplicate = conn.execute(
            """
            SELECT id FROM material_items
            WHERE name = ? AND active = 1 AND id <> ?
            """,
            (name, material_id),
        ).fetchone()
        if duplicate:
            raise ValueError("Материал с таким названием уже есть")
        min_level = _normalize_non_negative(
            data.get("min_level", row["min_level"]), field_name="Мин. остаток"
        )
        norm_per_wagon = _normalize_non_negative(
            data.get("norm_per_wagon", row["norm_per_wagon"]),
            field_name="Норма на вагон",
        )
        conn.execute(
            """
            UPDATE material_items
            SET name = ?, unit = ?, min_level = ?, norm_per_wagon = ?, updated_at = ?
            WHERE id = ?
            """,
            (name, unit, min_level, norm_per_wagon, time.time(), material_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM material_items WHERE id = ?",
            (material_id,),
        ).fetchone()
        return _row_material_item(
            row, totals={material_id: _material_snapshot(conn, material_id)}
        )


def adjust_material_stock(
    material_id: int,
    *,
    quantity,
    operator: str = "",
    note: str = "",
) -> dict:
    try:
        delta = round(float(quantity), 3)
    except (TypeError, ValueError) as e:
        raise ValueError("Корректировка должна быть числом") from e
    if abs(delta) < 0.001:
        raise ValueError("Укажите корректировку остатка")
    with _connect() as conn:
        row = _get_material_row(conn, material_id)
        snapshot = _material_snapshot(conn, material_id)
        new_on_hand = round(float(snapshot["on_hand"]) + delta, 3)
        if new_on_hand < 0:
            raise ValueError("Физический остаток не может быть меньше нуля")
        if new_on_hand < float(snapshot["reserved"]):
            raise ValueError("Физический остаток не может быть меньше резерва")
        _material_move(
            conn,
            material_id=material_id,
            move_type=MOVE_ADJUST,
            quantity=delta,
            on_hand_delta=delta,
            reserved_delta=0,
            operator=operator,
            note=(note or "").strip() or f"Корректировка остатка {delta:+g}",
        )
        conn.commit()
        return _row_material_item(
            row, totals={material_id: _material_snapshot(conn, material_id)}
        )


def list_material_templates() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM material_templates
            WHERE active = 1
            ORDER BY name
            """
        ).fetchall()
        out = []
        for row in rows:
            items_rows = conn.execute(
                """
                SELECT
                    i.*,
                    m.name AS material_name,
                    m.unit AS material_unit
                FROM material_template_items i
                JOIN material_items m ON m.id = i.material_id
                WHERE i.template_id = ?
                ORDER BY i.sort_order, i.line_no, i.id
                """,
                (int(row["id"]),),
            ).fetchall()
            item_count = conn.execute(
                """
                SELECT COUNT(*), COALESCE(SUM(norm_minutes), 0)
                FROM material_template_items
                WHERE template_id = ?
                """,
                (int(row["id"]),),
            ).fetchone()
            out.append(
                {
                    "id": int(row["id"]),
                    "name": row["name"],
                    "description": row["description"] or "",
                    **_template_meta(row),
                    "item_count": int(item_count[0] or 0),
                    "total_norm_minutes": round(float(item_count[1] or 0), 3),
                    "items": [
                        {
                            "id": int(item["id"]),
                            "line_no": int(item["line_no"] or 0),
                            "material_id": int(item["material_id"]),
                            "material_name": item["material_name"],
                            "material_unit": item["material_unit"] or "шт",
                            "qty_norm": round(float(item["norm_qty"] or 0), 3),
                            "work_type": item["work_type"] or "",
                            "feature_text": item["feature_text"] or "",
                            "tool_text": item["tool_text"] or "",
                            "norm_minutes": round(float(item["norm_minutes"] or 0), 3),
                        }
                        for item in items_rows
                    ],
                }
            )
        return out


def create_material_template(
    *,
    name: str,
    description: str = "",
    scheme_code: str = SCHEME_CODE_DEFAULT,
    has_box=False,
    returns_materials=False,
    extra_ring_mode: str = "",
    extra_units=0,
    k_goal=0,
    box_capacity_wagons=0,
) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("Укажите название схемы")
    description = (description or "").strip()
    scheme_code = _normalize_scheme_code(scheme_code)
    has_box = _normalize_bool_flag(has_box)
    returns_materials = _normalize_bool_flag(returns_materials)
    extra_ring_mode = (extra_ring_mode or "").strip()
    extra_units = _normalize_non_negative_int(extra_units, field_name="Допы схемы")
    k_goal = _normalize_non_negative_int(k_goal, field_name="K по схеме")
    box_capacity_wagons = _normalize_non_negative_int(
        box_capacity_wagons, field_name="Ёмкость ящика"
    )
    now = time.time()
    with _connect() as conn:
        existing = conn.execute(
            "SELECT id, active FROM material_templates WHERE name = ?",
            (name,),
        ).fetchone()
        if existing and int(existing["active"] or 0) == 1:
            raise ValueError("Такая схема уже есть")
        if existing:
            conn.execute(
                """
                UPDATE material_templates
                SET description = ?, scheme_code = ?, has_box = ?, returns_materials = ?,
                    extra_ring_mode = ?, extra_units = ?, k_goal = ?,
                    box_capacity_wagons = ?, active = 1, updated_at = ?
                WHERE id = ?
                """,
                (
                    description,
                    scheme_code,
                    has_box,
                    returns_materials,
                    extra_ring_mode,
                    extra_units,
                    k_goal,
                    box_capacity_wagons,
                    now,
                    int(existing["id"]),
                ),
            )
            template_id = int(existing["id"])
        else:
            cur = conn.execute(
                """
                INSERT INTO material_templates (
                    name, description, scheme_code, has_box, returns_materials,
                    extra_ring_mode, extra_units, k_goal, box_capacity_wagons,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    description,
                    scheme_code,
                    has_box,
                    returns_materials,
                    extra_ring_mode,
                    extra_units,
                    k_goal,
                    box_capacity_wagons,
                    now,
                    now,
                ),
            )
            template_id = int(cur.lastrowid)
        conn.commit()
    templates = list_material_templates()
    template = next((item for item in templates if item["id"] == template_id), None)
    if not template:
        raise RuntimeError("Не удалось сохранить схему")
    return template


def add_template_item(
    template_id: int,
    *,
    material_id: int,
    qty_norm,
    work_type: str = "",
    feature_text: str = "",
    tool_text: str = "",
    norm_minutes=0,
    line_no: int | None = None,
) -> dict:
    qty_norm = _normalize_non_negative(qty_norm, field_name="Норма количества")
    norm_minutes = _normalize_non_negative(norm_minutes, field_name="Норма времени")
    work_type = (work_type or "").strip()
    feature_text = (feature_text or "").strip()
    tool_text = (tool_text or "").strip()
    with _connect() as conn:
        template = conn.execute(
            "SELECT id FROM material_templates WHERE id = ? AND active = 1",
            (template_id,),
        ).fetchone()
        if not template:
            raise ValueError("Схема не найдена")
        material = _get_material_row(conn, material_id)
        if line_no is None:
            row = conn.execute(
                "SELECT COALESCE(MAX(line_no), 0) + 1 FROM material_template_items WHERE template_id = ?",
                (template_id,),
            ).fetchone()
            line_no = int(row[0] or 1)
        cur = conn.execute(
            """
            INSERT INTO material_template_items (
                template_id, material_id, norm_qty, sort_order, line_no,
                work_type, feature_text, tool_text, norm_minutes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                template_id,
                material_id,
                qty_norm,
                line_no,
                line_no,
                work_type,
                feature_text,
                tool_text,
                norm_minutes,
            ),
        )
        conn.commit()
    return {
        "id": int(cur.lastrowid or 0),
        "template_id": template_id,
        "material_id": material_id,
        "material_name": material["name"],
        "material_unit": material["unit"] or "шт",
        "qty_norm": qty_norm,
        "work_type": work_type,
        "feature_text": feature_text,
        "tool_text": tool_text,
        "norm_minutes": norm_minutes,
        "line_no": line_no,
    }


def update_material_template(template_id: int, data: dict) -> dict:
    now = time.time()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM material_templates WHERE id = ? AND active = 1",
            (template_id,),
        ).fetchone()
        if not row:
            raise ValueError("Схема не найдена")
        name = (
            data["name"] if "name" in data else row["name"]
        )
        description = (
            data["description"] if "description" in data else row["description"]
        )
        name = (name or "").strip()
        description = (description or "").strip()
        if not name:
            raise ValueError("Укажите название схемы")
        existing = conn.execute(
            "SELECT id FROM material_templates WHERE name = ? AND id != ? AND active = 1",
            (name, template_id),
        ).fetchone()
        if existing:
            raise ValueError("Такая схема уже есть")
        scheme_code = _normalize_scheme_code(data.get("scheme_code") or row["scheme_code"])
        has_box = _normalize_bool_flag(data.get("has_box") if "has_box" in data else row["has_box"])
        returns_materials = _normalize_bool_flag(
            data.get("returns_materials") if "returns_materials" in data else row["returns_materials"]
        )
        extra_ring_mode = (
            data["extra_ring_mode"] if "extra_ring_mode" in data else row["extra_ring_mode"]
        )
        extra_ring_mode = (extra_ring_mode or "").strip()
        extra_units = _normalize_non_negative_int(
            data.get("extra_units", row["extra_units"]), field_name="Допы схемы"
        )
        k_goal = _normalize_non_negative_int(
            data.get("k_goal", row["k_goal"]), field_name="K по схеме"
        )
        box_capacity_wagons = _normalize_non_negative_int(
            data.get("box_capacity_wagons", row["box_capacity_wagons"]),
            field_name="Ёмкость ящика",
        )
        conn.execute(
            """
            UPDATE material_templates
            SET name = ?, description = ?, scheme_code = ?, has_box = ?,
                returns_materials = ?, extra_ring_mode = ?, extra_units = ?,
                k_goal = ?, box_capacity_wagons = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                name,
                description,
                scheme_code,
                has_box,
                returns_materials,
                extra_ring_mode,
                extra_units,
                k_goal,
                box_capacity_wagons,
                now,
                template_id,
            ),
        )
        conn.commit()
    templates = list_material_templates()
    template = next((item for item in templates if item["id"] == template_id), None)
    if not template:
        raise RuntimeError("Не удалось обновить схему")
    return template


def update_template_item(template_item_id: int, data: dict) -> dict:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT i.*, m.name AS material_name, m.unit AS material_unit
            FROM material_template_items i
            JOIN material_items m ON m.id = i.material_id
            WHERE i.id = ?
            """,
            (template_item_id,),
        ).fetchone()
        if not row:
            raise ValueError("Строка схемы не найдена")
        material_id = int(data.get("material_id") or row["material_id"])
        material = _get_material_row(conn, material_id)
        qty_norm = _normalize_non_negative(
            data.get("qty_norm", row["norm_qty"]), field_name="Норма количества"
        )
        norm_minutes = _normalize_non_negative(
            data.get("norm_minutes", row["norm_minutes"]), field_name="Норма времени"
        )
        work_type = data["work_type"] if "work_type" in data else row["work_type"]
        feature_text = (
            data["feature_text"] if "feature_text" in data else row["feature_text"]
        )
        tool_text = data["tool_text"] if "tool_text" in data else row["tool_text"]
        work_type = (work_type or "").strip()
        feature_text = (feature_text or "").strip()
        tool_text = (tool_text or "").strip()
        line_no = _normalize_non_negative_int(
            data.get("line_no", row["line_no"]), field_name="Номер строки"
        ) or int(row["line_no"] or 1)
        conn.execute(
            """
            UPDATE material_template_items
            SET material_id = ?, norm_qty = ?, sort_order = ?, line_no = ?,
                work_type = ?, feature_text = ?, tool_text = ?, norm_minutes = ?
            WHERE id = ?
            """,
            (
                material_id,
                qty_norm,
                line_no,
                line_no,
                work_type,
                feature_text,
                tool_text,
                norm_minutes,
                template_item_id,
            ),
        )
        conn.commit()
    return {
        "id": template_item_id,
        "template_id": int(row["template_id"]),
        "material_id": material_id,
        "material_name": material["name"],
        "material_unit": material["unit"] or "шт",
        "qty_norm": qty_norm,
        "work_type": work_type,
        "feature_text": feature_text,
        "tool_text": tool_text,
        "norm_minutes": norm_minutes,
        "line_no": line_no,
    }


def delete_template_item(template_item_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM material_template_items WHERE id = ?",
            (template_item_id,),
        )
        conn.commit()
        return bool(cur.rowcount)


def assign_template_to_wagon(
    wagon_number: str,
    *,
    template_id: int,
    operator: str = "",
    note: str = "",
) -> dict:
    wagon_number = (wagon_number or "").strip()
    if not wagon_number:
        raise ValueError("Укажите номер вагона")
    now = time.time()
    from taksimo_store_fleet import get_wagon_card

    with _connect() as conn:
        template = conn.execute(
            "SELECT * FROM material_templates WHERE id = ? AND active = 1",
            (template_id,),
        ).fetchone()
        if not template:
            raise ValueError("Схема не найдена")
        template_meta = _template_meta(template)
        wagon_card = get_wagon_card(wagon_number) or {}
        origin_zone = (
            _zone_from_location_label(wagon_card.get("location_label") or "")
            or (wagon_card.get("planned_zone") or "").strip().upper()
        )
        default_return_zone = (
            origin_zone if template_meta["returns_materials"] else ""
        )
        return_status = (
            RETURN_STATUS_PLANNED if template_meta["returns_materials"] else ""
        )
        template_items = conn.execute(
            """
            SELECT * FROM material_template_items
            WHERE template_id = ?
            ORDER BY sort_order, line_no, id
            """,
            (template_id,),
        ).fetchall()
        if not template_items:
            raise ValueError("В схеме пока нет строк")
        prep = conn.execute(
            """
            SELECT id FROM master_wagon_prep
            WHERE wagon_number = ?
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (wagon_number,),
        ).fetchone()
        if prep:
            prep_id = int(prep["id"])
            conn.execute(
                """
                UPDATE master_wagon_prep
                SET template_id = ?, scheme_code = ?, has_box = ?, returns_materials = ?,
                    extra_ring_mode = ?, extra_units = ?, k_goal = ?, box_capacity_wagons = ?,
                    origin_zone = ?, return_status = ?, return_target_zone = ?,
                    actual_return_zone = '', return_updated_at = ?, prep_status = 'in_progress',
                    prepared_by = ?, note = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    template_id,
                    template_meta["scheme_code"],
                    1 if template_meta["has_box"] else 0,
                    1 if template_meta["returns_materials"] else 0,
                    template_meta["extra_ring_mode"],
                    template_meta["extra_units"],
                    template_meta["k_goal"],
                    template_meta["box_capacity_wagons"],
                    origin_zone,
                    return_status,
                    default_return_zone,
                    now if return_status else None,
                    (operator or "").strip(),
                    note,
                    now,
                    prep_id,
                ),
            )
            conn.execute("DELETE FROM master_wagon_prep_items WHERE prep_id = ?", (prep_id,))
        else:
            cur = conn.execute(
                """
                INSERT INTO master_wagon_prep (
                    wagon_number, template_id, scheme_code, has_box, returns_materials,
                    extra_ring_mode, extra_units, k_goal, box_capacity_wagons,
                    origin_zone, prep_status, return_status, return_target_zone,
                    actual_return_zone, return_updated_at, prepared_by,
                    note, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'in_progress', ?, ?, '', ?, ?, ?, ?, ?)
                """,
                (
                    wagon_number,
                    template_id,
                    template_meta["scheme_code"],
                    1 if template_meta["has_box"] else 0,
                    1 if template_meta["returns_materials"] else 0,
                    template_meta["extra_ring_mode"],
                    template_meta["extra_units"],
                    template_meta["k_goal"],
                    template_meta["box_capacity_wagons"],
                    origin_zone,
                    return_status,
                    default_return_zone,
                    now if return_status else None,
                    (operator or "").strip(),
                    note,
                    now,
                    now,
                ),
            )
            prep_id = int(cur.lastrowid)
        for item in template_items:
            conn.execute(
                """
                INSERT INTO master_wagon_prep_items (
                    prep_id, template_item_id, material_id, line_no, qty_norm,
                    work_type, feature_text, tool_text, norm_minutes, sort_order
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    prep_id,
                    int(item["id"]),
                    int(item["material_id"]),
                    int(item["line_no"]),
                    float(item["norm_qty"] or 0),
                    item["work_type"] or "",
                    item["feature_text"] or "",
                    item["tool_text"] or "",
                    float(item["norm_minutes"] or 0),
                    int(item["sort_order"] or 0),
                ),
            )
        conn.commit()
    result = get_wagon_materials(wagon_number)
    if not result:
        raise RuntimeError("Не удалось собрать карточку вагона после выбора схемы")
    return result


def add_material_receipt(
    material_id: int,
    *,
    quantity,
    operator: str = "",
    note: str = "",
) -> dict:
    qty = _normalize_quantity(quantity)
    with _connect() as conn:
        _get_material_row(conn, material_id)
        _material_move(
            conn,
            material_id=material_id,
            move_type=MOVE_RECEIPT,
            quantity=qty,
            on_hand_delta=qty,
            reserved_delta=0,
            operator=operator,
            note=note,
        )
        conn.commit()
    item = get_material_item(material_id)
    if not item:
        raise RuntimeError("Материал не найден после прихода")
    return item


def reserve_material_for_wagon(
    material_id: int,
    *,
    wagon_number: str,
    quantity,
    operator: str = "",
    note: str = "",
) -> dict:
    wagon_number = (wagon_number or "").strip()
    if not wagon_number:
        raise ValueError("Укажите номер вагона")
    qty = _normalize_quantity(quantity)
    with _connect() as conn:
        row = _get_material_row(conn, material_id)
        prep = _latest_prep_row(conn, wagon_number)
        if prep and prep["template_id"]:
            allowed = {
                int(item["material_id"])
                for item in conn.execute(
                    """
                    SELECT material_id
                    FROM material_template_items
                    WHERE template_id = ?
                    """,
                    (int(prep["template_id"]),),
                ).fetchall()
            }
            if allowed and material_id not in allowed:
                raise ValueError("Для этого вагона выбирайте материал только из назначенной схемы")
        snap = _material_snapshot(conn, material_id)
        if qty > snap["available"]:
            raise ValueError(
                f"Недостаточно остатка: свободно {snap['available']} {row['unit']}"
            )
        _material_move(
            conn,
            material_id=material_id,
            move_type=MOVE_RESERVE,
            quantity=qty,
            on_hand_delta=0,
            reserved_delta=qty,
            wagon_number=wagon_number,
            operator=operator,
            note=note,
        )
        conn.commit()
    result = get_wagon_materials(wagon_number)
    if not result:
        raise RuntimeError("Не удалось собрать карточку вагона после резерва")
    return result


def get_wagon_materials(wagon_number: str) -> dict | None:
    wagon_number = (wagon_number or "").strip()
    if not wagon_number:
        return None
    from taksimo_store_fleet import get_wagon_card

    with _connect() as conn:
        prep = conn.execute(
            """
            SELECT p.*, t.name AS template_name, t.description AS template_description
            FROM master_wagon_prep p
            LEFT JOIN material_templates t ON t.id = p.template_id
            WHERE p.wagon_number = ?
            ORDER BY p.updated_at DESC, p.id DESC
            LIMIT 1
            """,
            (wagon_number,),
        ).fetchone()
        prep_items_rows = conn.execute(
            """
            SELECT
                i.*,
                m.name AS material_name,
                m.unit AS material_unit
            FROM master_wagon_prep_items i
            JOIN material_items m ON m.id = i.material_id
            WHERE i.prep_id = ?
            ORDER BY i.sort_order, i.line_no, i.id
            """,
            (int(prep["id"]),),
        ).fetchall() if prep else []
        stats_rows = conn.execute(
            """
            SELECT
                material_id,
                COALESCE(SUM(reserved_delta), 0) AS reserved_qty,
                COALESCE(SUM(CASE WHEN on_hand_delta < 0 THEN -on_hand_delta ELSE 0 END), 0) AS consumed_qty
            FROM material_movements
            WHERE wagon_number = ?
            GROUP BY material_id
            """,
            (wagon_number,),
        ).fetchall()
        by_material = {
            int(row["material_id"]): {
                "reserved_qty": round(float(row["reserved_qty"] or 0), 3),
                "consumed_qty": round(float(row["consumed_qty"] or 0), 3),
            }
            for row in stats_rows
        }
        norm_by_material: dict[int, float] = {}
        operations: list[dict] = []
        for row in prep_items_rows:
            material_id = int(row["material_id"])
            norm_by_material[material_id] = round(
                norm_by_material.get(material_id, 0.0) + float(row["qty_norm"] or 0),
                3,
            )
            operations.append(
                {
                    "line_no": int(row["line_no"] or 0),
                    "material_id": material_id,
                    "material_name": row["material_name"],
                    "qty_norm": round(float(row["qty_norm"] or 0), 3),
                    "work_type": row["work_type"] or "",
                    "feature_text": row["feature_text"] or "",
                    "tool_text": row["tool_text"] or "",
                    "norm_minutes": round(float(row["norm_minutes"] or 0), 3),
                }
            )
        rows = conn.execute(
            """
            SELECT * FROM material_items
            WHERE active = 1
            ORDER BY sort_order, name
            """
        ).fetchall()
        items: list[dict] = []
        for row in rows:
            material_id = int(row["id"])
            current = by_material.get(material_id, {})
            norm = norm_by_material.get(
                material_id, round(float(row["norm_per_wagon"] or 0), 3)
            )
            reserved_qty = current.get("reserved_qty", 0.0)
            consumed_qty = current.get("consumed_qty", 0.0)
            if norm <= 0 and reserved_qty <= 0 and consumed_qty <= 0:
                continue
            shortage_qty = round(max(0.0, norm - reserved_qty), 3) if norm > 0 else 0.0
            items.append(
                {
                    "id": material_id,
                    "material_id": material_id,
                    "name": row["name"],
                    "unit": row["unit"] or "шт",
                    "norm_per_wagon": norm,
                    "reserved_qty": reserved_qty,
                    "consumed_qty": consumed_qty,
                    "actual_default_qty": reserved_qty,
                    "shortage_qty": shortage_qty,
                    "is_ready": shortage_qty <= 0 and (norm > 0 or reserved_qty > 0),
                }
            )
    wagon = get_wagon_card(wagon_number)
    return {
        "wagon_number": wagon_number,
        "wagon": wagon,
        "prep": {
            "id": int(prep["id"]) if prep else None,
            "template_id": int(prep["template_id"]) if prep and prep["template_id"] else None,
            "template_name": prep["template_name"] if prep else "",
            "template_description": prep["template_description"] if prep else "",
            "scheme_code": prep["scheme_code"] if prep else SCHEME_CODE_DEFAULT,
            "has_box": bool(prep["has_box"]) if prep else False,
            "returns_materials": bool(prep["returns_materials"]) if prep else False,
            "extra_ring_mode": prep["extra_ring_mode"] if prep else "",
            "extra_units": int(prep["extra_units"] or 0) if prep else 0,
            "k_goal": int(prep["k_goal"] or 0) if prep else 0,
            "box_capacity_wagons": int(prep["box_capacity_wagons"] or 0) if prep else 0,
            "origin_zone": prep["origin_zone"] if prep else "",
            "status": prep["prep_status"] if prep else "",
            "return_status": prep["return_status"] if prep else "",
            "return_target_zone": prep["return_target_zone"] if prep else "",
            "actual_return_zone": prep["actual_return_zone"] if prep else "",
            "prepared_by": prep["prepared_by"] if prep else "",
            "note": prep["note"] if prep else "",
        },
        "items": items,
        "operations": operations,
        "total_norm_minutes": round(sum(item["norm_minutes"] for item in operations), 3),
        "reserved_item_count": sum(1 for item in items if item["reserved_qty"] > 0),
        "shortage_count": sum(1 for item in items if item["shortage_qty"] > 0),
        "has_reserves": any(item["reserved_qty"] > 0 for item in items),
    }


def finalize_wagon_materials(
    wagon_number: str,
    *,
    items: list[dict],
    operator: str = "",
    note: str = "",
) -> dict:
    wagon_number = (wagon_number or "").strip()
    if not wagon_number:
        raise ValueError("Укажите номер вагона")
    if not isinstance(items, list) or not items:
        raise ValueError("Передайте фактический расход по материалам")

    with _connect() as conn:
        _apply_wagon_writeoff(
            conn,
            wagon_number=wagon_number,
            items=items,
            operator=operator,
            note=note,
        )
        conn.commit()

    result = get_wagon_materials(wagon_number)
    if not result:
        raise RuntimeError("Не удалось собрать карточку вагона после списания")
    return result


def _apply_wagon_writeoff(
    conn: sqlite3.Connection,
    *,
    wagon_number: str,
    items: list[dict],
    operator: str = "",
    note: str = "",
    dispatch_id: int | None = None,
) -> None:
    for item in items:
        try:
            material_id = int(item.get("material_id"))
        except (TypeError, ValueError) as e:
            raise ValueError("material_id обязателен") from e
        actual_qty = _normalize_quantity(item.get("actual_qty", 0), allow_zero=True)
        material_row = _get_material_row(conn, material_id)
        reserved_qty = _current_reserved_for(conn, material_id, wagon_number)
        if actual_qty == 0 and reserved_qty == 0:
            continue
        snapshot = _material_snapshot(conn, material_id)
        reserved_part = min(reserved_qty, actual_qty)
        extra_part = round(actual_qty - reserved_part, 3)
        if extra_part > snapshot["available"]:
            raise ValueError(
                f"Недостаточно свободного остатка для {material_row['name']}: "
                f"нужно ещё {extra_part} {material_row['unit']}, свободно {snapshot['available']}"
            )
        if reserved_part > 0:
            _material_move(
                conn,
                material_id=material_id,
                move_type=MOVE_CONSUME_RESERVED,
                quantity=reserved_part,
                on_hand_delta=-reserved_part,
                reserved_delta=-reserved_part,
                wagon_number=wagon_number,
                dispatch_id=dispatch_id,
                operator=operator,
                note=note,
            )
        release_qty = round(reserved_qty - reserved_part, 3)
        if release_qty > 0:
            _material_move(
                conn,
                material_id=material_id,
                move_type=MOVE_RELEASE,
                quantity=release_qty,
                on_hand_delta=0,
                reserved_delta=-release_qty,
                wagon_number=wagon_number,
                dispatch_id=dispatch_id,
                operator=operator,
                note=note,
            )
        if extra_part > 0:
            _material_move(
                conn,
                material_id=material_id,
                move_type=MOVE_CONSUME,
                quantity=extra_part,
                on_hand_delta=-extra_part,
                reserved_delta=0,
                wagon_number=wagon_number,
                dispatch_id=dispatch_id,
                operator=operator,
                note=note,
            )


def auto_writeoff_for_dispatch(
    conn: sqlite3.Connection,
    *,
    wagon_number: str,
    dispatch_id: int | None = None,
    operator: str = "",
) -> None:
    wagon_number = (wagon_number or "").strip()
    if not wagon_number:
        return
    reserved_rows = conn.execute(
        """
        SELECT
            material_id,
            COALESCE(SUM(reserved_delta), 0) AS reserved_qty
        FROM material_movements
        WHERE wagon_number = ?
        GROUP BY material_id
        HAVING ABS(COALESCE(SUM(reserved_delta), 0)) > 0.0001
        """,
        (wagon_number,),
    ).fetchall()
    if not reserved_rows:
        return
    items = [
        {
            "material_id": int(row["material_id"]),
            "actual_qty": round(float(row["reserved_qty"] or 0), 3),
        }
        for row in reserved_rows
        if float(row["reserved_qty"] or 0) > 0
    ]
    if not items:
        return
    _apply_wagon_writeoff(
        conn,
        wagon_number=wagon_number,
        items=items,
        operator=operator,
        note="Автосписание при отправке вагона из тупика",
        dispatch_id=dispatch_id,
    )


def _latest_prep_row(
    conn: sqlite3.Connection, wagon_number: str
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT p.*, t.name AS template_name, t.description AS template_description
        FROM master_wagon_prep p
        LEFT JOIN material_templates t ON t.id = p.template_id
        WHERE p.wagon_number = ?
        ORDER BY p.updated_at DESC, p.id DESC
        LIMIT 1
        """,
        ((wagon_number or "").strip(),),
    ).fetchone()


def sync_dispatch_scheme(
    conn: sqlite3.Connection,
    *,
    wagon_number: str,
    dispatch_id: int,
    slot_zone: str = "",
) -> None:
    prep = _latest_prep_row(conn, wagon_number)
    if not prep:
        return
    origin_zone = (slot_zone or prep["origin_zone"] or "").strip().upper()
    if origin_zone and origin_zone != (prep["origin_zone"] or "").strip().upper():
        conn.execute(
            """
            UPDATE master_wagon_prep
            SET origin_zone = ?, updated_at = ?
            WHERE id = ?
            """,
            (origin_zone, time.time(), int(prep["id"])),
        )
    conn.execute(
        """
        UPDATE wagon_dispatches
        SET scheme_template_id = ?, scheme_name = ?, scheme_code = ?, has_box = ?,
            returns_materials = ?, extra_units = ?, k_goal = ?, origin_zone = ?,
            return_status = ?, return_target_zone = ?, return_actual_zone = ?
        WHERE id = ?
        """,
        (
            int(prep["template_id"] or 0),
            prep["template_name"] or "",
            prep["scheme_code"] or SCHEME_CODE_DEFAULT,
            int(prep["has_box"] or 0),
            int(prep["returns_materials"] or 0),
            int(prep["extra_units"] or 0),
            int(prep["k_goal"] or 0),
            origin_zone,
            prep["return_status"] or "",
            prep["return_target_zone"] or "",
            prep["actual_return_zone"] or "",
            int(dispatch_id),
        ),
    )


def mark_return_in_transit(
    conn: sqlite3.Connection, *, wagon_number: str, dispatch_id: int
) -> None:
    prep = _latest_prep_row(conn, wagon_number)
    if not prep or not int(prep["returns_materials"] or 0):
        return
    now = time.time()
    target_zone = (prep["return_target_zone"] or prep["origin_zone"] or "").strip().upper()
    conn.execute(
        """
        UPDATE master_wagon_prep
        SET return_status = ?, return_target_zone = ?, return_updated_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (RETURN_STATUS_IN_TRANSIT, target_zone, now, now, int(prep["id"])),
    )
    conn.execute(
        """
        UPDATE wagon_dispatches
        SET return_status = ?, return_target_zone = ?
        WHERE id = ?
        """,
        (RETURN_STATUS_IN_TRANSIT, target_zone, int(dispatch_id)),
    )


def mark_return_target_zone(
    conn: sqlite3.Connection, *, wagon_number: str, target_zone: str
) -> None:
    zone = (target_zone or "").strip().upper()
    if not zone:
        return
    prep = _latest_prep_row(conn, wagon_number)
    if not prep or not int(prep["returns_materials"] or 0):
        return
    now = time.time()
    conn.execute(
        """
        UPDATE master_wagon_prep
        SET return_target_zone = ?, return_updated_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (zone, now, now, int(prep["id"])),
    )
    conn.execute(
        """
        UPDATE wagon_dispatches
        SET return_target_zone = ?
        WHERE wagon_number = ?
          AND id = (
              SELECT id FROM wagon_dispatches
              WHERE wagon_number = ?
              ORDER BY dispatched_at DESC, id DESC
              LIMIT 1
          )
        """,
        (zone, wagon_number, wagon_number),
    )


def mark_returned_to_zone(
    conn: sqlite3.Connection, *, wagon_number: str, actual_zone: str
) -> None:
    zone = (actual_zone or "").strip().upper()
    if not zone:
        return
    prep = _latest_prep_row(conn, wagon_number)
    if not prep or not int(prep["returns_materials"] or 0):
        return
    now = time.time()
    conn.execute(
        """
        UPDATE master_wagon_prep
        SET return_status = ?, return_target_zone = COALESCE(NULLIF(return_target_zone, ''), ?),
            actual_return_zone = ?, return_updated_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (RETURN_STATUS_RETURNED, zone, zone, now, now, int(prep["id"])),
    )
    conn.execute(
        """
        UPDATE wagon_dispatches
        SET return_status = ?, return_target_zone = COALESCE(NULLIF(return_target_zone, ''), ?),
            return_actual_zone = ?
        WHERE wagon_number = ?
          AND id = (
              SELECT id FROM wagon_dispatches
              WHERE wagon_number = ?
              ORDER BY dispatched_at DESC, id DESC
              LIMIT 1
          )
        """,
        (RETURN_STATUS_RETURNED, zone, zone, wagon_number, wagon_number),
    )


def materials_dashboard() -> dict:
    materials = list_material_items()
    from taksimo_store_fleet import list_wagon_cards

    templates = list_material_templates()
    active_wagons = [
        wagon
        for wagon in list_wagon_cards(limit=120)
        if wagon.get("stage") != "history"
    ]
    active_by_number = {wagon["number"]: wagon for wagon in active_wagons}
    norms = [item for item in materials if item["norm_per_wagon"] > 0 and item["active"]]
    overall_wagons_left = None
    if norms:
        overall_wagons_left = min(
            item["available_wagons"] or 0 for item in norms
        )

    with _connect() as conn:
        prep_rows = conn.execute(
            """
            SELECT
                p.*,
                t.name AS template_name
            FROM master_wagon_prep p
            LEFT JOIN material_templates t ON t.id = p.template_id
            WHERE p.id IN (
                SELECT MAX(id)
                FROM master_wagon_prep
                GROUP BY wagon_number
            )
            """
        ).fetchall()
        rows = conn.execute(
            """
            SELECT
                wagon_number,
                material_id,
                COALESCE(SUM(reserved_delta), 0) AS reserved_qty
            FROM material_movements
            WHERE wagon_number != ''
            GROUP BY wagon_number, material_id
            HAVING ABS(COALESCE(SUM(reserved_delta), 0)) > 0.0001
            """
        ).fetchall()
        prep_items = conn.execute(
            """
            SELECT
                p.wagon_number,
                i.material_id,
                COALESCE(SUM(i.qty_norm), 0) AS norm_qty,
                COALESCE(SUM(i.norm_minutes), 0) AS norm_minutes
            FROM master_wagon_prep_items i
            JOIN master_wagon_prep p ON p.id = i.prep_id
            GROUP BY p.wagon_number, i.material_id
            """
        ).fetchall()
        dispatch_rows = conn.execute(
            """
            SELECT
                wagon_number,
                scheme_code,
                scheme_name,
                extra_units,
                k_goal,
                has_box,
                returns_materials,
                origin_zone,
                return_status,
                return_target_zone,
                return_actual_zone,
                dispatched_at,
                received_at,
                status
            FROM wagon_dispatches
            WHERE COALESCE(scheme_code, '') != ''
            ORDER BY dispatched_at DESC, id DESC
            """
        ).fetchall()
    reserved_by_wagon: dict[str, dict[int, float]] = {}
    for row in rows:
        wagon_number = (row["wagon_number"] or "").strip()
        reserved_by_wagon.setdefault(wagon_number, {})[int(row["material_id"])] = round(
            float(row["reserved_qty"] or 0), 3
        )
    prep_by_wagon = {
        (row["wagon_number"] or "").strip(): {
            "template_id": int(row["template_id"]) if row["template_id"] else None,
            "template_name": row["template_name"] or "",
            "scheme_code": row["scheme_code"] or SCHEME_CODE_DEFAULT,
            "has_box": bool(row["has_box"]),
            "returns_materials": bool(row["returns_materials"]),
            "extra_ring_mode": row["extra_ring_mode"] or "",
            "extra_units": int(row["extra_units"] or 0),
            "k_goal": int(row["k_goal"] or 0),
            "box_capacity_wagons": int(row["box_capacity_wagons"] or 0),
            "origin_zone": row["origin_zone"] or "",
            "prep_status": row["prep_status"] or "",
            "return_status": row["return_status"] or "",
            "return_target_zone": row["return_target_zone"] or "",
            "actual_return_zone": row["actual_return_zone"] or "",
        }
        for row in prep_rows
        if (row["wagon_number"] or "").strip()
    }
    norm_by_wagon: dict[str, dict[int, float]] = {}
    minutes_by_wagon: dict[str, float] = {}
    for row in prep_items:
        wagon_number = (row["wagon_number"] or "").strip()
        norm_by_wagon.setdefault(wagon_number, {})[int(row["material_id"])] = round(
            float(row["norm_qty"] or 0), 3
        )
        minutes_by_wagon[wagon_number] = round(
            minutes_by_wagon.get(wagon_number, 0.0) + float(row["norm_minutes"] or 0),
            3,
        )

    scheme_summary = {
        "dispatched": {"scheme1": 0, "scheme2": 0, "scheme3": 0},
        "assigned": {"scheme1": 0, "scheme2": 0, "scheme3": 0},
        "historical_k_total": 0,
        "historical_extra_units": 0,
        "returning_box_wagons": 0,
        "return_capacity_wagons": 0,
        "returned_box_wagons": 0,
    }
    for row in dispatch_rows:
        code = _normalize_scheme_code(row["scheme_code"] or SCHEME_CODE_DEFAULT)
        scheme_summary["dispatched"][code] += 1
        scheme_summary["historical_k_total"] += int(row["k_goal"] or 0)
        scheme_summary["historical_extra_units"] += int(row["extra_units"] or 0)
    for prep in prep_by_wagon.values():
        code = _normalize_scheme_code(prep.get("scheme_code") or SCHEME_CODE_DEFAULT)
        scheme_summary["assigned"][code] += 1
    return_queue = []
    for wagon_number, prep in prep_by_wagon.items():
        if not prep.get("returns_materials"):
            continue
        status = prep.get("return_status") or ""
        if status == RETURN_STATUS_RETURNED:
            scheme_summary["returned_box_wagons"] += 1
            continue
        scheme_summary["returning_box_wagons"] += 1
        scheme_summary["return_capacity_wagons"] += int(prep.get("box_capacity_wagons") or 0)
        wagon = active_by_number.get(wagon_number) or {
            "number": wagon_number,
            "stage": "history",
            "stage_label": "В истории",
            "location_label": "Только по расходникам",
        }
        return_queue.append(
            {
                "wagon_number": wagon_number,
                "template_name": prep.get("template_name") or "",
                "scheme_code": prep.get("scheme_code") or SCHEME_CODE_DEFAULT,
                "return_status": status or RETURN_STATUS_PLANNED,
                "origin_zone": prep.get("origin_zone") or "",
                "return_target_zone": prep.get("return_target_zone") or "",
                "actual_return_zone": prep.get("actual_return_zone") or "",
                "box_capacity_wagons": int(prep.get("box_capacity_wagons") or 0),
                "stage": wagon.get("stage") or "history",
                "stage_label": wagon.get("stage_label") or "В истории",
                "location_label": wagon.get("location_label") or "",
            }
        )

    queue_numbers = set(active_by_number) | set(reserved_by_wagon) | set(prep_by_wagon)
    stage_rank = {"at_slot": 0, "departed": 1, "returning": 2, "available": 3}
    wagons = []
    for wagon_number in queue_numbers:
        wagon = active_by_number.get(wagon_number) or {
            "number": wagon_number,
            "stage": "history",
            "stage_label": "В истории",
            "location_label": "Только по расходникам",
        }
        reserved_map = reserved_by_wagon.get(wagon_number, {})
        norm_map = norm_by_wagon.get(wagon_number, {})
        prep = prep_by_wagon.get(wagon_number, {})
        shortage_count = 0
        reserved_item_count = 0
        shortage_names = []
        if norm_map:
            for material in materials:
                norm = norm_map.get(material["id"], 0.0)
                if norm <= 0:
                    continue
                reserved_qty = reserved_map.get(material["id"], 0.0)
                if reserved_qty > 0:
                    reserved_item_count += 1
                if reserved_qty < norm:
                    shortage_count += 1
                    shortage_names.append(material["name"])
        else:
            for material in materials:
                norm = material["norm_per_wagon"]
                reserved_qty = reserved_map.get(material["id"], 0.0)
                if reserved_qty > 0:
                    reserved_item_count += 1
                if norm > 0 and reserved_qty < norm:
                    shortage_count += 1
                    shortage_names.append(material["name"])
        if not reserved_item_count and not norm_map and wagon.get("stage") == "history":
            continue
        wagons.append(
            {
                "number": wagon_number,
                "stage": wagon.get("stage") or "history",
                "stage_label": wagon.get("stage_label") or "В истории",
                "location_label": wagon.get("location_label") or "",
                "template_id": prep.get("template_id"),
                "template_name": prep.get("template_name") or "",
                "scheme_code": prep.get("scheme_code") or SCHEME_CODE_DEFAULT,
                "has_box": bool(prep.get("has_box")),
                "returns_materials": bool(prep.get("returns_materials")),
                "extra_units": int(prep.get("extra_units") or 0),
                "k_goal": int(prep.get("k_goal") or 0),
                "box_capacity_wagons": int(prep.get("box_capacity_wagons") or 0),
                "prep_status": prep.get("prep_status") or "",
                "origin_zone": prep.get("origin_zone") or "",
                "return_status": prep.get("return_status") or "",
                "return_target_zone": prep.get("return_target_zone") or "",
                "actual_return_zone": prep.get("actual_return_zone") or "",
                "reserved_item_count": reserved_item_count,
                "shortage_count": shortage_count,
                "is_ready": shortage_count == 0 and reserved_item_count > 0,
                "shortage_names": shortage_names[:5],
                "norm_minutes": minutes_by_wagon.get(wagon_number, 0.0),
            }
        )
    wagons.sort(
        key=lambda item: (
            stage_rank.get(item["stage"], 9),
            -item["reserved_item_count"],
            -item["shortage_count"],
            item["number"],
        )
    )

    return {
        "units": list(MATERIAL_UNITS),
        "materials": materials,
        "templates": templates,
        "wagons": wagons,
        "scheme_summary": scheme_summary,
        "return_queue": return_queue,
        "summary": {
            "material_count": len(materials),
            "template_count": len(templates),
            "low_stock_count": sum(1 for item in materials if item["low_stock"]),
            "overall_wagons_left": overall_wagons_left,
            "ready_wagons": sum(1 for item in wagons if item["is_ready"]),
            "historical_k_total": scheme_summary["historical_k_total"],
            "historical_extra_units": scheme_summary["historical_extra_units"],
            "returning_box_wagons": scheme_summary["returning_box_wagons"],
            "return_capacity_wagons": scheme_summary["return_capacity_wagons"],
        },
    }
