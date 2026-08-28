"""SQLite storage for the ``Склад Мастер`` mini application.

Balances are projections of the immutable movement ledger.  Schema upgrades are
additive so installations with the original one-material requests keep all data.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "sklad_master.db"
SCHEMA_VERSION = 6

DEFAULT_SITES = ("Туран", "Грузовой", "Площадка 3", "Площадка 4")
DEFAULT_SUPPLIERS = (
    "Кодар возврат", "Николай", "Мурат Ветим Вторчермет", "Грифон",
    "Сергей лес", "Шпильки", "магазин Стиль", "магазин Спутник",
    "магазин Интерьер",
)
DEFAULT_MATERIALS = (
    ("Брус 150х150", "шт"), ("Брус 100х150", "шт"),
    ("Швеллер 2 метра верхний", "шт"), ("Швеллер 2 метра нижний", "шт"),
    ("Швеллер 4 метра", "шт"), ("Стяжка-струбцина М30", "шт"),
    ("Гайки М30", "шт"), ("Шайба М30", "шт"), ("Уголок 20 см", "шт"),
    ("Уголок 1 метр", "шт"), ("Проволока 6 мм", "кг"),
    ("Проволока 5 мм", "кг"), ("Электроды", "кг"),
    ("Диск для шлифмашинки 180 мм", "шт"),
    ("Диск для шлифмашинки 220 мм", "шт"), ("Перчатки ХБ", "шт"),
    ("Гвозди 200 мм, 25 кг", "шт"), ("Гвозди 150 мм, 25 кг", "шт"),
    ("Гвозди 100 мм, 25 кг", "шт"),
    ("Услуги доставка по городу: Евгений Д.", "шт"),
    ("3 метра Туран манипулятор 6 метров", "шт"), ("Дмитрий 3 метра", "шт"),
)

ROLES = {"master", "supply", "admin"}
REQUEST_STATUSES = (
    "draft", "submitted", "accepted", "in_transit", "partially_received",
    "received", "closed", "rejected", "cancelled",
)
TRANSITIONS = {
    "draft": {"submitted": {"master", "admin"}, "cancelled": {"master", "admin"}},
    "submitted": {"accepted": {"supply", "admin"}, "rejected": {"supply", "admin"},
                  "cancelled": {"master", "admin"}},
    "accepted": {"in_transit": {"supply", "admin"}, "rejected": {"supply", "admin"},
                 "cancelled": {"supply", "admin"}},
    "in_transit": {"partially_received": {"master", "supply", "admin"},
                   "received": {"master", "supply", "admin"},
                   "cancelled": {"admin"}},
    "partially_received": {"received": {"master", "supply", "admin"},
                           "cancelled": {"admin"}},
    "received": {"closed": {"master", "supply", "admin"}},
    "rejected": {}, "cancelled": {}, "closed": {},
}
ROLE_ACTIONS = {
    "master": {
        "view", "receipt", "issue", "transfer", "request_create", "request_receive",
    },
    "supply": {"view", "request_manage", "delivery_create", "request_receive"},
    "admin": {
        "view", "receipt", "issue", "transfer", "inventory_adjustment",
        "request_create", "request_manage", "request_receive", "delivery_create",
        "settings_manage", "roles_manage",
    },
}


def _db_path() -> Path:
    override = os.getenv("SKLAD_MASTER_DB_PATH")
    return Path(override) if override else Path(DB_PATH)


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _backup_before_migration(path: Path) -> Path | None:
    if not path.is_file():
        return None
    with sqlite3.connect(path) as conn:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if version >= SCHEMA_VERSION:
        return None
    backup_dir = path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    destination = backup_dir / f"{path.stem}-pre-v{SCHEMA_VERSION}-{stamp}{path.suffix}"
    shutil.copy2(path, destination)
    return destination


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_column(conn: sqlite3.Connection, table: str, definition: str) -> None:
    name = definition.split()[0]
    if name not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def init_sklad_master_db() -> None:
    _backup_before_migration(_db_path())
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sm_sites (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
                sort_order INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sm_materials (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
                unit TEXT NOT NULL DEFAULT 'шт', min_level REAL NOT NULL DEFAULT 0,
                sort_order INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sm_suppliers (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
                sort_order INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL, updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sm_stock_ops (
                id INTEGER PRIMARY KEY AUTOINCREMENT, site_id INTEGER NOT NULL,
                material_id INTEGER NOT NULL, supplier_id INTEGER, op_type TEXT NOT NULL,
                quantity REAL NOT NULL DEFAULT 0, quantity_delta REAL NOT NULL DEFAULT 0,
                actor_max_id INTEGER, actor_name TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '', request_id INTEGER, created_at REAL NOT NULL,
                FOREIGN KEY(site_id) REFERENCES sm_sites(id),
                FOREIGN KEY(material_id) REFERENCES sm_materials(id),
                FOREIGN KEY(supplier_id) REFERENCES sm_suppliers(id)
            );
            CREATE TABLE IF NOT EXISTS sm_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT, site_id INTEGER NOT NULL,
                material_id INTEGER NOT NULL, quantity REAL NOT NULL DEFAULT 0,
                urgency TEXT NOT NULL DEFAULT 'plan', status TEXT NOT NULL DEFAULT 'new',
                requested_by_max_id INTEGER, requested_by_name TEXT NOT NULL DEFAULT '',
                comment TEXT NOT NULL DEFAULT '', supply_comment TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                FOREIGN KEY(site_id) REFERENCES sm_sites(id),
                FOREIGN KEY(material_id) REFERENCES sm_materials(id)
            );
            """
        )
        _add_column(conn, "sm_stock_ops", "idempotency_key TEXT")
        _add_column(conn, "sm_stock_ops", "transfer_id TEXT")
        _add_column(conn, "sm_stock_ops", "related_op_id INTEGER")
        _add_column(conn, "sm_stock_ops", "delivery_item_id INTEGER")
        _add_column(conn, "sm_stock_ops", "receipt_id INTEGER")
        _add_column(conn, "sm_requests", "accepted_at REAL")
        _add_column(conn, "sm_requests", "closed_at REAL")
        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_sm_stock_ops_site_material
                ON sm_stock_ops(site_id, material_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_sm_requests_site_status
                ON sm_requests(site_id, status, updated_at);
            CREATE UNIQUE INDEX IF NOT EXISTS ux_sm_stock_ops_idempotency
                ON sm_stock_ops(idempotency_key) WHERE idempotency_key IS NOT NULL;

            CREATE TABLE IF NOT EXISTS sm_receipts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site_id INTEGER NOT NULL, supplier_id INTEGER NOT NULL,
                actor_max_id INTEGER, actor_name TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '', idempotency_key TEXT UNIQUE,
                created_at REAL NOT NULL,
                FOREIGN KEY(site_id) REFERENCES sm_sites(id),
                FOREIGN KEY(supplier_id) REFERENCES sm_suppliers(id)
            );
            CREATE INDEX IF NOT EXISTS idx_sm_receipts_site_created
                ON sm_receipts(site_id,created_at DESC);

            CREATE TABLE IF NOT EXISTS sm_request_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER NOT NULL, material_id INTEGER NOT NULL,
                quantity REAL NOT NULL, created_at REAL NOT NULL,
                FOREIGN KEY(request_id) REFERENCES sm_requests(id) ON DELETE CASCADE,
                FOREIGN KEY(material_id) REFERENCES sm_materials(id)
            );
            CREATE INDEX IF NOT EXISTS idx_sm_request_items_request
                ON sm_request_items(request_id);
            CREATE TABLE IF NOT EXISTS sm_site_material_settings (
                site_id INTEGER NOT NULL, material_id INTEGER NOT NULL,
                min_level REAL NOT NULL DEFAULT 0, updated_at REAL NOT NULL,
                updated_by_max_id INTEGER,
                PRIMARY KEY(site_id, material_id),
                FOREIGN KEY(site_id) REFERENCES sm_sites(id) ON DELETE CASCADE,
                FOREIGN KEY(material_id) REFERENCES sm_materials(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS sm_deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT, request_id INTEGER NOT NULL,
                supplier_id INTEGER, status TEXT NOT NULL DEFAULT 'created',
                note TEXT NOT NULL DEFAULT '', created_by_max_id INTEGER,
                created_by_name TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL,
                received_at REAL,
                FOREIGN KEY(request_id) REFERENCES sm_requests(id),
                FOREIGN KEY(supplier_id) REFERENCES sm_suppliers(id)
            );
            CREATE TABLE IF NOT EXISTS sm_delivery_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT, delivery_id INTEGER NOT NULL,
                request_item_id INTEGER NOT NULL, quantity REAL NOT NULL,
                received_quantity REAL NOT NULL DEFAULT 0,
                FOREIGN KEY(delivery_id) REFERENCES sm_deliveries(id) ON DELETE CASCADE,
                FOREIGN KEY(request_item_id) REFERENCES sm_request_items(id)
            );
            CREATE TABLE IF NOT EXISTS sm_roles (
                id INTEGER PRIMARY KEY AUTOINCREMENT, max_id INTEGER NOT NULL,
                role TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                UNIQUE(max_id, role)
            );
            CREATE TABLE IF NOT EXISTS sm_role_sites (
                role_id INTEGER NOT NULL, site_id INTEGER NOT NULL,
                PRIMARY KEY(role_id, site_id),
                FOREIGN KEY(role_id) REFERENCES sm_roles(id) ON DELETE CASCADE,
                FOREIGN KEY(site_id) REFERENCES sm_sites(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS sm_commands (
                idempotency_key TEXT PRIMARY KEY, command_type TEXT NOT NULL,
                result_json TEXT NOT NULL, created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sm_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT, actor_max_id INTEGER,
                actor_name TEXT NOT NULL DEFAULT '', action TEXT NOT NULL,
                entity_type TEXT NOT NULL, entity_id TEXT NOT NULL DEFAULT '',
                details_json TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL
            );
            """
        )
        _seed(conn, "sm_sites", ((x,) for x in DEFAULT_SITES))
        _seed(conn, "sm_suppliers", ((x,) for x in DEFAULT_SUPPLIERS))
        _seed(conn, "sm_materials", DEFAULT_MATERIALS)
        # Backfill exactly one item for every legacy request.
        conn.execute(
            """
            INSERT INTO sm_request_items(request_id, material_id, quantity, created_at)
            SELECT r.id, r.material_id, r.quantity, r.created_at
            FROM sm_requests r
            WHERE NOT EXISTS (
                SELECT 1 FROM sm_request_items i WHERE i.request_id = r.id
            )
            """
        )
        conn.execute("UPDATE sm_requests SET status='submitted' WHERE status='new'")
        conn.execute("UPDATE sm_requests SET status='received' WHERE status='delivered'")
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()


def _seed(conn: sqlite3.Connection, table: str, values: Iterable[tuple]) -> None:
    now = time.time()
    existing = {str(r["name"]).strip().lower() for r in conn.execute(f"SELECT name FROM {table}")}
    for order, value in enumerate(values, 1):
        name = str(value[0])
        if name.strip().lower() in existing:
            continue
        if table == "sm_materials":
            conn.execute(
                "INSERT INTO sm_materials(name,unit,sort_order,created_at,updated_at) VALUES(?,?,?,?,?)",
                (name, value[1], order, now, now),
            )
        else:
            conn.execute(
                f"INSERT INTO {table}(name,sort_order,created_at,updated_at) VALUES(?,?,?,?)",
                (name, order, now, now),
            )


def _qty(value: Any, *, positive: bool = True) -> float:
    try:
        result = round(float(value), 3)
    except (TypeError, ValueError) as exc:
        raise ValueError("Количество должно быть числом") from exc
    if positive and result <= 0:
        raise ValueError("Количество должно быть больше нуля")
    return result


def _require(conn: sqlite3.Connection, table: str, item_id: int, title: str) -> sqlite3.Row:
    row = conn.execute(f"SELECT * FROM {table} WHERE id=? AND active=1", (int(item_id),)).fetchone()
    if not row:
        raise ValueError(f"{title} не найден")
    return row


def _audit(conn: sqlite3.Connection, actor_max_id: int | None, actor_name: str,
           action: str, entity_type: str, entity_id: Any, details: dict | None = None) -> None:
    conn.execute(
        """INSERT INTO sm_audit_log(actor_max_id,actor_name,action,entity_type,
           entity_id,details_json,created_at) VALUES(?,?,?,?,?,?,?)""",
        (actor_max_id, actor_name.strip(), action, entity_type, str(entity_id),
         json.dumps(details or {}, ensure_ascii=False, sort_keys=True), time.time()),
    )


def _cached(conn: sqlite3.Connection, key: str | None, command: str) -> dict | None:
    if not key:
        return None
    row = conn.execute(
        "SELECT command_type,result_json FROM sm_commands WHERE idempotency_key=?", (key,)
    ).fetchone()
    if not row:
        return None
    if row["command_type"] != command:
        raise ValueError("Ключ идемпотентности уже использован другой командой")
    result = json.loads(row["result_json"])
    result["_idempotent_replay"] = True
    return result


def _cache(conn: sqlite3.Connection, key: str | None, command: str, result: dict) -> None:
    if key:
        conn.execute(
            "INSERT INTO sm_commands VALUES(?,?,?,?)",
            (key, command, json.dumps(result, ensure_ascii=False), time.time()),
        )


def _balance(conn: sqlite3.Connection, site_id: int, material_id: int) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(quantity_delta),0) value FROM sm_stock_ops WHERE site_id=? AND material_id=?",
        (site_id, material_id),
    ).fetchone()
    return round(float(row["value"]), 3)


def _minimum(conn: sqlite3.Connection, site_id: int, material_id: int) -> float:
    row = conn.execute(
        """SELECT COALESCE(s.min_level,m.min_level,0) value
           FROM sm_materials m
           LEFT JOIN sm_site_material_settings s
             ON s.material_id=m.id AND s.site_id=?
           WHERE m.id=?""",
        (site_id, material_id),
    ).fetchone()
    return round(float(row["value"] if row else 0), 3)


def _stock_status(balance: float, minimum: float) -> str:
    if minimum <= 0:
        return "ok"
    if balance < minimum:
        return "critical"
    if balance == minimum:
        return "warning"
    return "ok"


def _stock_alert(
    conn: sqlite3.Connection,
    *,
    site_id: int,
    material_id: int,
    old_balance: float,
    new_balance: float,
    old_minimum: float | None = None,
    new_minimum: float | None = None,
) -> dict | None:
    before_minimum = _minimum(conn, site_id, material_id) if old_minimum is None else old_minimum
    after_minimum = _minimum(conn, site_id, material_id) if new_minimum is None else new_minimum
    old_status = _stock_status(old_balance, before_minimum)
    new_status = _stock_status(new_balance, after_minimum)
    if new_status not in {"warning", "critical"} or new_status == old_status:
        return None
    site = conn.execute("SELECT name FROM sm_sites WHERE id=?", (site_id,)).fetchone()
    material = conn.execute(
        "SELECT name,unit FROM sm_materials WHERE id=?", (material_id,)
    ).fetchone()
    return {
        "status": new_status,
        "previous_status": old_status,
        "site_id": site_id,
        "site_name": site["name"] if site else "",
        "material_id": material_id,
        "material_name": material["name"] if material else "",
        "unit": material["unit"] if material else "",
        "balance": round(new_balance, 3),
        "min_level": round(after_minimum, 3),
    }


def _site(row: sqlite3.Row) -> dict:
    return {"id": int(row["id"]), "name": row["name"], "sort_order": int(row["sort_order"]),
            "active": bool(row["active"])}


def _material(row: sqlite3.Row) -> dict:
    return {"id": int(row["id"]), "name": row["name"], "unit": row["unit"],
            "min_level": round(float(row["min_level"]), 3),
            "sort_order": int(row["sort_order"]), "active": bool(row["active"])}


def _supplier(row: sqlite3.Row) -> dict:
    return {"id": int(row["id"]), "name": row["name"],
            "sort_order": int(row["sort_order"]), "active": bool(row["active"])}


def list_sites(*, include_inactive: bool = False) -> list[dict]:
    with _connect() as conn:
        sql = "SELECT * FROM sm_sites" + ("" if include_inactive else " WHERE active=1")
        return [_site(r) for r in conn.execute(sql + " ORDER BY sort_order,name")]


def list_materials(*, include_inactive: bool = False) -> list[dict]:
    with _connect() as conn:
        sql = "SELECT * FROM sm_materials" + ("" if include_inactive else " WHERE active=1")
        return [_material(r) for r in conn.execute(sql + " ORDER BY sort_order,name")]


def list_suppliers() -> list[dict]:
    with _connect() as conn:
        return [_supplier(r) for r in conn.execute(
            "SELECT * FROM sm_suppliers WHERE active=1 ORDER BY sort_order,name"
        )]


def create_material(*, name: str, unit: str = "шт", min_level=0) -> dict:
    title, measure = str(name or "").strip(), str(unit or "шт").strip()
    if not title:
        raise ValueError("Укажите материал")
    minimum = _qty(min_level, positive=False)
    if minimum < 0:
        raise ValueError("Минимум не может быть меньше нуля")
    now = time.time()
    with _connect() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO sm_materials(name,unit,min_level,created_at,updated_at) VALUES(?,?,?,?,?)",
                (title, measure or "шт", minimum, now, now),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("Такой материал уже есть") from exc
        row = conn.execute("SELECT * FROM sm_materials WHERE id=?", (cur.lastrowid,)).fetchone()
    return _material(row)


def update_material(
    material_id: int,
    *,
    actor_max_id: int | None = None,
    actor_name: str = "",
    **changes: Any,
) -> dict:
    allowed = {k: v for k, v in changes.items() if k in {"name", "unit", "min_level", "active", "sort_order"}}
    if not allowed:
        raise ValueError("Нет изменений")
    if "name" in allowed:
        allowed["name"] = str(allowed["name"] or "").strip()
        if not allowed["name"]:
            raise ValueError("Название материала не может быть пустым")
    if "unit" in allowed:
        allowed["unit"] = str(allowed["unit"] or "").strip()
        if allowed["unit"] not in {"шт", "кг", "м", "м3"}:
            raise ValueError("Недопустимая единица измерения")
    if "min_level" in allowed:
        allowed["min_level"] = _qty(allowed["min_level"], positive=False)
        if allowed["min_level"] < 0:
            raise ValueError("Минимум не может быть меньше нуля")
    if "active" in allowed:
        allowed["active"] = int(bool(allowed["active"]))
    allowed["updated_at"] = time.time()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT * FROM sm_materials WHERE id=?", (material_id,)
        ).fetchone()
        if not current:
            raise ValueError("Материал не найден")
        if allowed.get("active") == 0 and bool(current["active"]):
            nonzero_sites = int(
                conn.execute(
                    """SELECT COUNT(*) FROM (
                           SELECT site_id
                           FROM sm_stock_ops
                           WHERE material_id=?
                           GROUP BY site_id
                           HAVING ABS(SUM(quantity_delta)) > 0.000001
                       )""",
                    (material_id,),
                ).fetchone()[0]
            )
            open_requests = int(
                conn.execute(
                    """SELECT COUNT(*)
                       FROM sm_request_items i
                       JOIN sm_requests r ON r.id=i.request_id
                       WHERE i.material_id=?
                         AND r.status NOT IN ('closed','rejected','cancelled')""",
                    (material_id,),
                ).fetchone()[0]
            )
            if nonzero_sites:
                raise ValueError("Нельзя отключить материал с ненулевым остатком")
            if open_requests:
                raise ValueError("Нельзя отключить материал в активной заявке")
        try:
            conn.execute(
                f"UPDATE sm_materials SET {','.join(f'{k}=?' for k in allowed)} WHERE id=?",
                (*allowed.values(), material_id),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("Материал с таким названием уже существует") from exc
        row = conn.execute("SELECT * FROM sm_materials WHERE id=?", (material_id,)).fetchone()
        _audit(
            conn,
            actor_max_id,
            actor_name,
            "material_update",
            "material",
            material_id,
            {
                "before": _material(current),
                "after": _material(row),
            },
        )
        conn.commit()
    return _material(row)


def update_site(site_id: int, **changes: Any) -> dict:
    allowed = {k: v for k, v in changes.items() if k in {"name", "active", "sort_order"}}
    if not allowed:
        raise ValueError("Нет изменений")
    allowed["updated_at"] = time.time()
    with _connect() as conn:
        if not conn.execute("SELECT id FROM sm_sites WHERE id=?", (site_id,)).fetchone():
            raise ValueError("Площадка не найдена")
        conn.execute(f"UPDATE sm_sites SET {','.join(f'{k}=?' for k in allowed)} WHERE id=?",
                     (*allowed.values(), site_id))
        row = conn.execute("SELECT * FROM sm_sites WHERE id=?", (site_id,)).fetchone()
    return _site(row)


def set_site_material_minimum(*, site_id: int, material_id: int, min_level: Any,
                              actor_max_id: int | None, actor_name: str = "") -> dict:
    minimum = _qty(min_level, positive=False)
    if minimum < 0:
        raise ValueError("Минимум не может быть меньше нуля")
    now = time.time()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        site = _require(conn, "sm_sites", site_id, "Площадка")
        material = _require(conn, "sm_materials", material_id, "Материал")
        balance = _balance(conn, site_id, material_id)
        old_minimum = _minimum(conn, site_id, material_id)
        conn.execute(
            """INSERT INTO sm_site_material_settings(
                   site_id,material_id,min_level,updated_at,updated_by_max_id
               ) VALUES(?,?,?,?,?)
               ON CONFLICT(site_id,material_id) DO UPDATE SET
                   min_level=excluded.min_level,updated_at=excluded.updated_at,
                   updated_by_max_id=excluded.updated_by_max_id""",
            (site_id, material_id, minimum, now, actor_max_id),
        )
        result = {
            "site": _site(site), "material": _material(material),
            "min_level": minimum, "updated_at": now,
        }
        alert = _stock_alert(
            conn,
            site_id=site_id,
            material_id=material_id,
            old_balance=balance,
            new_balance=balance,
            old_minimum=old_minimum,
            new_minimum=minimum,
        )
        result["stock_alerts"] = [alert] if alert else []
        _audit(conn, actor_max_id, actor_name, "site_material_minimum",
               "site_material", f"{site_id}:{material_id}", result)
        conn.commit()
    return result


def create_supplier(*, name: str) -> dict:
    title = str(name or "").strip()
    if not title:
        raise ValueError("Укажите поставщика")
    now = time.time()
    with _connect() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO sm_suppliers(name,created_at,updated_at) VALUES(?,?,?)",
                (title, now, now),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("Такой поставщик уже есть") from exc
        row = conn.execute("SELECT * FROM sm_suppliers WHERE id=?", (cur.lastrowid,)).fetchone()
    return _supplier(row)


def _movement(conn: sqlite3.Connection, *, site_id: int, material_id: int,
              op_type: str, quantity: float, delta: float, actor_max_id: int | None,
              actor_name: str, note: str = "", supplier_id: int | None = None,
              request_id: int | None = None, idempotency_key: str | None = None,
              transfer_id: str | None = None, related_op_id: int | None = None,
              delivery_item_id: int | None = None,
              receipt_id: int | None = None) -> int:
    cur = conn.execute(
        """INSERT INTO sm_stock_ops(site_id,material_id,supplier_id,op_type,quantity,
           quantity_delta,actor_max_id,actor_name,note,request_id,created_at,
           idempotency_key,transfer_id,related_op_id,delivery_item_id,receipt_id)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (site_id, material_id, supplier_id, op_type, quantity, delta, actor_max_id,
         actor_name.strip(), note.strip(), request_id, time.time(), idempotency_key,
         transfer_id, related_op_id, delivery_item_id, receipt_id),
    )
    return int(cur.lastrowid)


def record_receipt_batch(
    *,
    site_id: int,
    supplier_id: int,
    items: list[dict],
    actor_max_id: int | None,
    actor_name: str,
    note: str = "",
    idempotency_key: str | None = None,
) -> dict:
    if not isinstance(items, list) or not items:
        raise ValueError("Добавьте хотя бы одну позицию прихода")
    combined: dict[int, float] = {}
    for item in items:
        try:
            material_id = int(item["material_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("В позиции не указан материал") from exc
        quantity = _qty(item.get("quantity"))
        combined[material_id] = round(combined.get(material_id, 0) + quantity, 3)

    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cached = _cached(conn, idempotency_key, "receipt_batch")
        if cached:
            return cached
        site = _require(conn, "sm_sites", site_id, "Площадка")
        supplier = _require(conn, "sm_suppliers", supplier_id, "Поставщик")
        materials = {
            material_id: _require(conn, "sm_materials", material_id, "Материал")
            for material_id in combined
        }
        now = time.time()
        receipt_cur = conn.execute(
            """INSERT INTO sm_receipts(
                   site_id,supplier_id,actor_max_id,actor_name,note,
                   idempotency_key,created_at
               ) VALUES(?,?,?,?,?,?,?)""",
            (
                site_id, supplier_id, actor_max_id, actor_name.strip(),
                note.strip(), idempotency_key, now,
            ),
        )
        receipt_id = int(receipt_cur.lastrowid)
        saved_items: list[dict] = []
        for material_id, quantity in combined.items():
            movement_id = _movement(
                conn,
                site_id=site_id,
                material_id=material_id,
                supplier_id=supplier_id,
                op_type="receipt",
                quantity=quantity,
                delta=quantity,
                actor_max_id=actor_max_id,
                actor_name=actor_name,
                note=note,
                receipt_id=receipt_id,
            )
            material = _material(materials[material_id])
            saved_items.append(
                {
                    "movement_id": movement_id,
                    "material": material,
                    "material_id": material_id,
                    "quantity": quantity,
                }
            )
        result = {
            "id": receipt_id,
            "site": _site(site),
            "supplier": _supplier(supplier),
            "items": saved_items,
            "item_count": len(saved_items),
            "actor_max_id": actor_max_id,
            "actor_name": actor_name.strip(),
            "note": note.strip(),
            "created_at": now,
        }
        _audit(conn, actor_max_id, actor_name, "receipt_batch", "receipt", receipt_id, result)
        _cache(conn, idempotency_key, "receipt_batch", result)
        conn.commit()
        return result


def record_receipt(*, site_id: int, material_id: int, supplier_id: int,
                   quantity: Any, actor_max_id: int | None, actor_name: str,
                   note: str = "", idempotency_key: str | None = None) -> dict:
    qty = _qty(quantity)
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cached = _cached(conn, idempotency_key, "receipt")
        if cached:
            return cached
        site = _require(conn, "sm_sites", site_id, "Площадка")
        material = _require(conn, "sm_materials", material_id, "Материал")
        supplier = _require(conn, "sm_suppliers", supplier_id, "Поставщик")
        op_id = _movement(conn, site_id=site_id, material_id=material_id,
                          supplier_id=supplier_id, op_type="receipt", quantity=qty,
                          delta=qty, actor_max_id=actor_max_id, actor_name=actor_name,
                          note=note, idempotency_key=idempotency_key)
        result = {"id": op_id, "site": _site(site), "material": _material(material),
                  "supplier": _supplier(supplier), "quantity": qty,
                  "actor_max_id": actor_max_id, "actor_name": actor_name.strip(),
                  "note": note.strip(), "created_at": time.time()}
        _audit(conn, actor_max_id, actor_name, "receipt", "movement", op_id, result)
        _cache(conn, idempotency_key, "receipt", result)
        conn.commit()
        return result


def record_issue(*, site_id: int, material_id: int, quantity: Any,
                 actor_max_id: int | None, actor_name: str, note: str = "",
                 request_id: int | None = None, idempotency_key: str | None = None) -> dict:
    qty = _qty(quantity)
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cached = _cached(conn, idempotency_key, "issue")
        if cached:
            return cached
        _require(conn, "sm_sites", site_id, "Площадка")
        material = _require(conn, "sm_materials", material_id, "Материал")
        old_balance = _balance(conn, site_id, material_id)
        if old_balance < qty:
            raise ValueError("Недостаточно остатка")
        op_id = _movement(conn, site_id=site_id, material_id=material_id,
                          op_type="issue", quantity=qty, delta=-qty,
                          actor_max_id=actor_max_id, actor_name=actor_name, note=note,
                          request_id=request_id, idempotency_key=idempotency_key)
        new_balance = _balance(conn, site_id, material_id)
        alert = _stock_alert(
            conn,
            site_id=site_id,
            material_id=material_id,
            old_balance=old_balance,
            new_balance=new_balance,
        )
        result = {"id": op_id, "site_id": site_id, "material": _material(material),
                  "quantity": qty, "balance": new_balance,
                  "stock_alerts": [alert] if alert else []}
        _audit(conn, actor_max_id, actor_name, "issue", "movement", op_id, result)
        _cache(conn, idempotency_key, "issue", result)
        conn.commit()
        return result


def record_transfer(*, from_site_id: int, to_site_id: int, material_id: int,
                    quantity: Any, actor_max_id: int | None, actor_name: str,
                    note: str = "", idempotency_key: str | None = None) -> dict:
    qty = _qty(quantity)
    if int(from_site_id) == int(to_site_id):
        raise ValueError("Площадки должны различаться")
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cached = _cached(conn, idempotency_key, "transfer")
        if cached:
            return cached
        _require(conn, "sm_sites", from_site_id, "Площадка отправления")
        _require(conn, "sm_sites", to_site_id, "Площадка назначения")
        material = _require(conn, "sm_materials", material_id, "Материал")
        old_source_balance = _balance(conn, from_site_id, material_id)
        old_target_balance = _balance(conn, to_site_id, material_id)
        if old_source_balance < qty:
            raise ValueError("Недостаточно остатка")
        transfer_id = uuid.uuid4().hex
        out_id = _movement(conn, site_id=from_site_id, material_id=material_id,
                           op_type="transfer_out", quantity=qty, delta=-qty,
                           actor_max_id=actor_max_id, actor_name=actor_name, note=note,
                           transfer_id=transfer_id)
        in_id = _movement(conn, site_id=to_site_id, material_id=material_id,
                          op_type="transfer_in", quantity=qty, delta=qty,
                          actor_max_id=actor_max_id, actor_name=actor_name, note=note,
                          transfer_id=transfer_id, related_op_id=out_id)
        conn.execute("UPDATE sm_stock_ops SET related_op_id=? WHERE id=?", (in_id, out_id))
        result = {"transfer_id": transfer_id, "out_id": out_id, "in_id": in_id,
                  "from_site_id": from_site_id, "to_site_id": to_site_id,
                  "material": _material(material), "quantity": qty}
        alerts = [
            _stock_alert(
                conn,
                site_id=from_site_id,
                material_id=material_id,
                old_balance=old_source_balance,
                new_balance=_balance(conn, from_site_id, material_id),
            ),
            _stock_alert(
                conn,
                site_id=to_site_id,
                material_id=material_id,
                old_balance=old_target_balance,
                new_balance=_balance(conn, to_site_id, material_id),
            ),
        ]
        result["stock_alerts"] = [alert for alert in alerts if alert]
        _audit(conn, actor_max_id, actor_name, "transfer", "transfer", transfer_id, result)
        _cache(conn, idempotency_key, "transfer", result)
        conn.commit()
        return result


def record_inventory_adjustment(*, site_id: int, material_id: int, quantity_delta: Any,
                                actor_max_id: int | None, actor_name: str, note: str,
                                allow_negative: bool = False,
                                idempotency_key: str | None = None) -> dict:
    delta = _qty(quantity_delta, positive=False)
    if delta == 0:
        raise ValueError("Корректировка не может быть нулевой")
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cached = _cached(conn, idempotency_key, "inventory_adjustment")
        if cached:
            return cached
        _require(conn, "sm_sites", site_id, "Площадка")
        _require(conn, "sm_materials", material_id, "Материал")
        old_balance = _balance(conn, site_id, material_id)
        new_balance = round(old_balance + delta, 3)
        if new_balance < 0 and not allow_negative:
            raise ValueError("Корректировка создаст отрицательный остаток")
        op_id = _movement(conn, site_id=site_id, material_id=material_id,
                          op_type="inventory_adjustment", quantity=abs(delta), delta=delta,
                          actor_max_id=actor_max_id, actor_name=actor_name, note=note,
                          idempotency_key=idempotency_key)
        alert = _stock_alert(
            conn,
            site_id=site_id,
            material_id=material_id,
            old_balance=old_balance,
            new_balance=new_balance,
        )
        result = {"id": op_id, "site_id": site_id, "material_id": material_id,
                  "quantity_delta": delta, "balance": new_balance,
                  "stock_alerts": [alert] if alert else []}
        _audit(conn, actor_max_id, actor_name, "inventory_adjustment", "movement", op_id, result)
        _cache(conn, idempotency_key, "inventory_adjustment", result)
        conn.commit()
        return result


def reverse_movement(*, movement_id: int, actor_max_id: int | None, actor_name: str,
                     note: str = "", allow_negative: bool = False,
                     idempotency_key: str | None = None) -> dict:
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cached = _cached(conn, idempotency_key, "reversal")
        if cached:
            return cached
        original = conn.execute("SELECT * FROM sm_stock_ops WHERE id=?", (movement_id,)).fetchone()
        if not original:
            raise ValueError("Движение не найдено")
        if original["op_type"] == "reversal" or conn.execute(
            "SELECT 1 FROM sm_stock_ops WHERE related_op_id=? AND op_type='reversal'", (movement_id,)
        ).fetchone():
            raise ValueError("Движение уже сторнировано")
        delta = -float(original["quantity_delta"])
        old_balance = _balance(conn, original["site_id"], original["material_id"])
        if old_balance + delta < 0 and not allow_negative:
            raise ValueError("Сторно создаст отрицательный остаток")
        op_id = _movement(
            conn, site_id=original["site_id"], material_id=original["material_id"],
            op_type="reversal", quantity=abs(delta), delta=delta,
            actor_max_id=actor_max_id, actor_name=actor_name, note=note,
            related_op_id=movement_id, idempotency_key=idempotency_key,
        )
        new_balance = round(old_balance + delta, 3)
        alert = _stock_alert(
            conn,
            site_id=int(original["site_id"]),
            material_id=int(original["material_id"]),
            old_balance=old_balance,
            new_balance=new_balance,
        )
        result = {"id": op_id, "reversed_movement_id": movement_id,
                  "quantity_delta": delta, "balance": new_balance,
                  "stock_alerts": [alert] if alert else []}
        _audit(conn, actor_max_id, actor_name, "reversal", "movement", op_id, result)
        _cache(conn, idempotency_key, "reversal", result)
        conn.commit()
        return result


def list_movements(*, site_id: int | None = None, material_id: int | None = None,
                   op_type: str | None = None, request_id: int | None = None,
                   limit: int = 50, offset: int = 0) -> dict:
    limit, offset = max(1, min(int(limit), 200)), max(0, int(offset))
    clauses, params = [], []
    for column, value in (("o.site_id", site_id), ("o.material_id", material_id),
                          ("o.op_type", op_type), ("o.request_id", request_id)):
        if value is not None:
            clauses.append(f"{column}=?")
            params.append(value)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with _connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) n FROM sm_stock_ops o{where}", params).fetchone()["n"]
        rows = conn.execute(
            f"""SELECT o.*,s.name site_name,m.name material_name,m.unit material_unit
                FROM sm_stock_ops o JOIN sm_sites s ON s.id=o.site_id
                JOIN sm_materials m ON m.id=o.material_id {where}
                ORDER BY o.created_at DESC,o.id DESC LIMIT ? OFFSET ?""",
            (*params, limit, offset),
        ).fetchall()
    return {"items": [dict(r) for r in rows], "total": int(total),
            "limit": limit, "offset": offset}


def create_request(*, site_id: int, material_id: int | None = None, quantity: Any = None,
                   items: list[dict] | None = None, urgency: str = "plan",
                   actor_max_id: int | None, actor_name: str, comment: str = "",
                   status: str = "submitted", idempotency_key: str | None = None) -> dict:
    normalized = items or [{"material_id": material_id, "quantity": quantity}]
    if not normalized:
        raise ValueError("Добавьте позиции")
    parsed = [(int(x["material_id"]), _qty(x.get("quantity"))) for x in normalized]
    status = status if status in {"draft", "submitted"} else "submitted"
    urgency = urgency if urgency in {"plan", "urgent"} else "plan"
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cached = _cached(conn, idempotency_key, "request_create")
        if cached:
            return cached
        _require(conn, "sm_sites", site_id, "Площадка")
        for mid, _ in parsed:
            _require(conn, "sm_materials", mid, "Материал")
        now = time.time()
        first_mid, first_qty = parsed[0]
        cur = conn.execute(
            """INSERT INTO sm_requests(site_id,material_id,quantity,urgency,status,
               requested_by_max_id,requested_by_name,comment,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (site_id, first_mid, first_qty, urgency, status, actor_max_id,
             actor_name.strip(), comment.strip(), now, now),
        )
        request_id = int(cur.lastrowid)
        conn.executemany(
            "INSERT INTO sm_request_items(request_id,material_id,quantity,created_at) VALUES(?,?,?,?)",
            ((request_id, mid, qty, now) for mid, qty in parsed),
        )
        result = _get_request(conn, request_id)
        _audit(conn, actor_max_id, actor_name, "request_create", "request", request_id, result)
        _cache(conn, idempotency_key, "request_create", result)
        conn.commit()
        return result


def _get_request(conn: sqlite3.Connection, request_id: int) -> dict:
    row = conn.execute(
        """SELECT r.*,s.name site_name FROM sm_requests r
           JOIN sm_sites s ON s.id=r.site_id WHERE r.id=?""", (request_id,)
    ).fetchone()
    if not row:
        raise ValueError("Заявка не найдена")
    items = conn.execute(
        """SELECT i.*,m.name material_name,m.unit material_unit,
           COALESCE((SELECT SUM(di.quantity) FROM sm_delivery_items di
                     WHERE di.request_item_id=i.id),0) in_transit,
           COALESCE((SELECT SUM(di.received_quantity) FROM sm_delivery_items di
                     WHERE di.request_item_id=i.id),0) received
           FROM sm_request_items i JOIN sm_materials m ON m.id=i.material_id
           WHERE i.request_id=? ORDER BY i.id""", (request_id,)
    ).fetchall()
    item_dicts = []
    for item in items:
        data = dict(item)
        data["ordered"] = round(float(item["quantity"]), 3)
        data["in_transit"] = round(float(item["in_transit"]) - float(item["received"]), 3)
        data["received"] = round(float(item["received"]), 3)
        data["remaining"] = max(0, round(float(item["quantity"]) - float(item["received"]), 3))
        item_dicts.append(data)
    result = dict(row)
    result["items"] = item_dicts
    delivery_ids = [
        int(delivery["id"])
        for delivery in conn.execute(
            "SELECT id FROM sm_deliveries WHERE request_id=? ORDER BY created_at DESC,id DESC",
            (request_id,),
        )
    ]
    result["deliveries"] = [_get_delivery(conn, delivery_id) for delivery_id in delivery_ids]
    if item_dicts:
        first = item_dicts[0]
        result.update(material_id=first["material_id"], material_name=first["material_name"],
                      material_unit=first["material_unit"], quantity=first["quantity"])
    return result


def get_request(request_id: int) -> dict:
    with _connect() as conn:
        return _get_request(conn, int(request_id))


def list_requests(*, site_id: int | None = None, status: str | None = None,
                  limit: int = 30, offset: int = 0) -> list[dict]:
    limit, offset = max(1, min(int(limit), 100)), max(0, int(offset))
    clauses, params = [], []
    if site_id:
        clauses.append("site_id=?"); params.append(int(site_id))
    if status:
        clauses.append("status=?"); params.append(status)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with _connect() as conn:
        ids = [r["id"] for r in conn.execute(
            f"SELECT id FROM sm_requests{where} ORDER BY updated_at DESC,id DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )]
        return [_get_request(conn, item_id) for item_id in ids]


def transition_request(*, request_id: int, new_status: str, actor_role: str,
                       actor_max_id: int | None, actor_name: str,
                       comment: str = "", idempotency_key: str | None = None) -> dict:
    if new_status not in REQUEST_STATUSES or actor_role not in ROLES:
        raise ValueError("Недопустимый статус или роль")
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cached = _cached(conn, idempotency_key, "request_transition")
        if cached:
            return cached
        current = _get_request(conn, request_id)
        if actor_role not in TRANSITIONS.get(current["status"], {}).get(new_status, set()):
            raise ValueError(f"Переход {current['status']} → {new_status} недоступен для роли {actor_role}")
        now = time.time()
        extra = ", accepted_at=?" if new_status == "accepted" else ", closed_at=?" if new_status == "closed" else ""
        params: list[Any] = [new_status, comment.strip(), now]
        if extra:
            params.append(now)
        params.append(request_id)
        conn.execute(
            f"UPDATE sm_requests SET status=?,supply_comment=?,updated_at=?{extra} WHERE id=?",
            params,
        )
        result = _get_request(conn, request_id)
        _audit(conn, actor_max_id, actor_name, "request_transition", "request", request_id,
               {"from": current["status"], "to": new_status})
        _cache(conn, idempotency_key, "request_transition", result)
        conn.commit()
        return result


def create_delivery(*, request_id: int, items: list[dict], supplier_id: int | None,
                    actor_max_id: int | None, actor_name: str, note: str = "",
                    idempotency_key: str | None = None) -> dict:
    if not items:
        raise ValueError("Добавьте позиции поставки")
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cached = _cached(conn, idempotency_key, "delivery_create")
        if cached:
            return cached
        request_doc = _get_request(conn, request_id)
        if request_doc["status"] not in {"accepted", "in_transit", "partially_received"}:
            raise ValueError("Поставка недоступна в текущем статусе заявки")
        if supplier_id is not None:
            _require(conn, "sm_suppliers", supplier_id, "Поставщик")
        request_items = {x["id"]: x for x in request_doc["items"]}
        requested: dict[int, float] = {}
        for item in items:
            item_id, qty = int(item["request_item_id"]), _qty(item.get("quantity"))
            if item_id not in request_items:
                raise ValueError("Позиция не относится к заявке")
            requested[item_id] = round(requested.get(item_id, 0) + qty, 3)
        parsed = []
        for item_id, qty in requested.items():
            already = request_items[item_id]["received"] + request_items[item_id]["in_transit"]
            if already + qty > request_items[item_id]["ordered"] + 1e-9:
                raise ValueError("Количество поставки превышает остаток заявки")
            parsed.append((item_id, qty))
        now = time.time()
        cur = conn.execute(
            """INSERT INTO sm_deliveries(request_id,supplier_id,note,created_by_max_id,
               created_by_name,created_at) VALUES(?,?,?,?,?,?)""",
            (request_id, supplier_id, note.strip(), actor_max_id, actor_name.strip(), now),
        )
        delivery_id = int(cur.lastrowid)
        conn.executemany(
            "INSERT INTO sm_delivery_items(delivery_id,request_item_id,quantity) VALUES(?,?,?)",
            ((delivery_id, item_id, qty) for item_id, qty in parsed),
        )
        conn.execute("UPDATE sm_requests SET status='in_transit',updated_at=? WHERE id=?",
                     (now, request_id))
        result = _get_delivery(conn, delivery_id)
        _audit(conn, actor_max_id, actor_name, "delivery_create", "delivery", delivery_id, result)
        _cache(conn, idempotency_key, "delivery_create", result)
        conn.commit()
        return result


def _get_delivery(conn: sqlite3.Connection, delivery_id: int) -> dict:
    row = conn.execute(
        """SELECT d.*,s.name supplier_name
           FROM sm_deliveries d
           LEFT JOIN sm_suppliers s ON s.id=d.supplier_id
           WHERE d.id=?""",
        (delivery_id,),
    ).fetchone()
    if not row:
        raise ValueError("Поставка не найдена")
    result = dict(row)
    result["items"] = [dict(r) for r in conn.execute(
        """SELECT di.*,ri.material_id,m.name material_name,m.unit material_unit
           FROM sm_delivery_items di JOIN sm_request_items ri ON ri.id=di.request_item_id
           JOIN sm_materials m ON m.id=ri.material_id WHERE di.delivery_id=? ORDER BY di.id""",
        (delivery_id,),
    )]
    return result


def get_delivery(delivery_id: int) -> dict:
    with _connect() as conn:
        result = _get_delivery(conn, int(delivery_id))
        result["request"] = _get_request(conn, result["request_id"])
        return result


def receive_delivery(*, delivery_id: int, items: list[dict] | None,
                     actor_max_id: int | None, actor_name: str, note: str = "",
                     idempotency_key: str | None = None) -> dict:
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cached = _cached(conn, idempotency_key, "delivery_receive")
        if cached:
            return cached
        delivery = _get_delivery(conn, delivery_id)
        available = {x["id"]: x for x in delivery["items"]}
        incoming = items or [
            {"delivery_item_id": x["id"], "quantity": x["quantity"] - x["received_quantity"]}
            for x in delivery["items"]
        ]
        requested: dict[int, float] = {}
        for item in incoming:
            item_id, qty = int(item["delivery_item_id"]), _qty(item.get("quantity"))
            if item_id not in available:
                raise ValueError("Позиция не относится к поставке")
            requested[item_id] = round(requested.get(item_id, 0) + qty, 3)
        parsed = []
        for item_id, qty in requested.items():
            source = available[item_id]
            if float(source["received_quantity"]) + qty > float(source["quantity"]) + 1e-9:
                raise ValueError("Принятое количество превышает поставку")
            parsed.append((source, qty))
        request_doc = _get_request(conn, delivery["request_id"])
        received_now: list[dict] = []
        for source, qty in parsed:
            _movement(
                conn, site_id=request_doc["site_id"], material_id=source["material_id"],
                supplier_id=delivery["supplier_id"], op_type="receipt", quantity=qty,
                delta=qty, actor_max_id=actor_max_id, actor_name=actor_name,
                note=note, request_id=delivery["request_id"], delivery_item_id=source["id"],
            )
            conn.execute(
                "UPDATE sm_delivery_items SET received_quantity=received_quantity+? WHERE id=?",
                (qty, source["id"]),
            )
            received_now.append(
                {
                    "delivery_item_id": int(source["id"]),
                    "material_id": int(source["material_id"]),
                    "material_name": source["material_name"],
                    "material_unit": source["material_unit"],
                    "quantity": qty,
                }
            )
        refreshed = _get_delivery(conn, delivery_id)
        complete = all(abs(float(x["quantity"]) - float(x["received_quantity"])) < 1e-9
                       for x in refreshed["items"])
        conn.execute(
            "UPDATE sm_deliveries SET status=?,received_at=? WHERE id=?",
            ("received" if complete else "partially_received", time.time() if complete else None,
             delivery_id),
        )
        request_after = _get_request(conn, delivery["request_id"])
        all_received = all(x["remaining"] <= 0 for x in request_after["items"])
        any_received = any(x["received"] > 0 for x in request_after["items"])
        request_status = "received" if all_received else "partially_received" if any_received else "in_transit"
        conn.execute("UPDATE sm_requests SET status=?,updated_at=? WHERE id=?",
                     (request_status, time.time(), delivery["request_id"]))
        result = _get_delivery(conn, delivery_id)
        result["request"] = _get_request(conn, delivery["request_id"])
        result["received_now"] = received_now
        _audit(conn, actor_max_id, actor_name, "delivery_receive", "delivery", delivery_id,
               {"items": incoming})
        _cache(conn, idempotency_key, "delivery_receive", result)
        conn.commit()
        return result


def _env_ids(name: str) -> set[int]:
    result = set()
    for value in os.getenv(name, "").replace(";", ",").split(","):
        try:
            if value.strip():
                result.add(int(value.strip()))
        except ValueError:
            continue
    return result


def get_access(max_id: int) -> dict:
    env_roles = set()
    for role, variable in (("master", "MATERIALS_MASTER_MAX_IDS"),
                           ("supply", "MATERIALS_SUPPLY_MAX_IDS"),
                           ("admin", "DRIVERS_ADMIN_MAX_IDS")):
        if int(max_id) in _env_ids(variable):
            env_roles.add(role)
    with _connect() as conn:
        rows = conn.execute(
            """SELECT r.id,r.role,rs.site_id FROM sm_roles r LEFT JOIN sm_role_sites rs
               ON rs.role_id=r.id WHERE r.max_id=? AND r.active=1""", (int(max_id),)
        ).fetchall()
    db_roles = {r["role"] for r in rows if r["role"] in ROLES}
    roles = sorted(env_roles | db_roles)
    restricted = {int(r["site_id"]) for r in rows if r["site_id"] is not None}
    has_global_db_role = any(
        r["role"] in ROLES and r["site_id"] is None for r in rows
    )
    all_sites = bool(env_roles) or has_global_db_role or "admin" in roles
    actions = sorted(set().union(*(ROLE_ACTIONS[r] for r in roles)) if roles else set())
    return {"roles": roles, "role": "admin" if "admin" in roles else roles[0] if roles else None,
            "allowed_actions": actions, "capabilities": {x: True for x in actions},
            "site_ids": sorted(restricted), "all_sites": all_sites}


def set_role(*, max_id: int, role: str, site_ids: list[int] | None = None,
             active: bool = True, actor_max_id: int | None = None,
             actor_name: str = "") -> dict:
    if role not in ROLES:
        raise ValueError("Неизвестная роль")
    now = time.time()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """INSERT INTO sm_roles(max_id,role,active,created_at,updated_at) VALUES(?,?,?,?,?)
               ON CONFLICT(max_id,role) DO UPDATE SET active=excluded.active,updated_at=excluded.updated_at""",
            (max_id, role, int(active), now, now),
        )
        role_id = conn.execute("SELECT id FROM sm_roles WHERE max_id=? AND role=?",
                               (max_id, role)).fetchone()["id"]
        conn.execute("DELETE FROM sm_role_sites WHERE role_id=?", (role_id,))
        for site_id in site_ids or []:
            _require(conn, "sm_sites", site_id, "Площадка")
            conn.execute("INSERT INTO sm_role_sites(role_id,site_id) VALUES(?,?)",
                         (role_id, site_id))
        _audit(conn, actor_max_id, actor_name, "role_update", "role", role_id,
               {"max_id": max_id, "role": role, "site_ids": site_ids or [], "active": active})
        conn.commit()
    return get_access(max_id)


def site_stock(site_id: int) -> list[dict]:
    with _connect() as conn:
        _require(conn, "sm_sites", site_id, "Площадка")
        rows = conn.execute(
            """SELECT m.*,
                      COALESCE(sms.min_level,m.min_level,0) site_min_level,
                      COALESCE(SUM(o.quantity_delta),0) balance
               FROM sm_materials m
               LEFT JOIN sm_stock_ops o ON o.material_id=m.id AND o.site_id=?
               LEFT JOIN sm_site_material_settings sms
                 ON sms.material_id=m.id AND sms.site_id=?
               WHERE m.active=1
               GROUP BY m.id,sms.min_level
               ORDER BY m.sort_order,m.name""", (site_id, site_id)
        ).fetchall()
    result = []
    for row in rows:
        balance = round(float(row["balance"]), 3)
        minimum = round(float(row["site_min_level"]), 3)
        result.append({"id": row["id"], "name": row["name"], "unit": row["unit"],
                       "min_level": minimum, "balance": balance,
                       "status": _stock_status(balance, minimum)})
    return result


def dashboard(*, site_id: int | None = None) -> dict:
    sites, materials, suppliers = list_sites(), list_materials(), list_suppliers()
    selected = site_id or (sites[0]["id"] if sites else 0)
    stock = site_stock(selected) if selected else []
    summaries = []
    for site in sites:
        items = site_stock(site["id"])
        summaries.append({"site_id": site["id"], "site_name": site["name"],
                          "material_count": len(items),
                          "critical_count": sum(x["status"] == "critical" for x in items),
                          "warning_count": sum(x["status"] == "warning" for x in items)})
    return {"sites": sites, "materials": materials, "suppliers": suppliers,
            "selected_site_id": selected, "stock": stock, "site_summaries": summaries,
            "requests": list_requests(site_id=selected or None, limit=20),
            "summary": {"site_count": len(sites), "material_count": len(materials),
                        "critical_count": sum(x["status"] == "critical" for x in stock),
                        "warning_count": sum(x["status"] == "warning" for x in stock)}}
