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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "sklad_master.db"
SCHEMA_VERSION = 14

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

ROLES = {"master", "supply", "admin", "manager"}
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
    "in_transit": {"partially_received": {"master", "admin"},
                   "received": {"master", "admin"},
                   "cancelled": {"admin"}},
    "partially_received": {"received": {"master", "admin"},
                           "cancelled": {"admin"}},
    "received": {"closed": {"master", "admin"}},
    "rejected": {}, "cancelled": {}, "closed": {},
}
ROLE_ACTIONS = {
    "master": {
        "view", "receipt", "issue", "transfer", "request_create", "request_receive",
    },
    "supply": {
        "view", "request_manage", "request_receive", "delivery_create",
        "receipt_price", "receipt_send_manager",
    },
    "manager": {"view", "payment_view"},
    "admin": {
        "view", "receipt", "issue", "transfer", "inventory_adjustment",
        "request_create", "request_manage", "request_receive", "delivery_create",
        "settings_manage", "roles_manage", "receipt_price", "receipt_send_manager", "payment_view",
    },
}
PAYMENT_STATUSES = ("pending", "priced", "sent")
ETA_DAYS_MIN, ETA_DAYS_MAX = 1, 90


def _validate_eta_days(days: Any) -> int:
    try:
        value = int(days)
    except (TypeError, ValueError):
        raise ValueError("Укажите срок поставки в днях") from None
    if value < ETA_DAYS_MIN or value > ETA_DAYS_MAX:
        raise ValueError(f"Срок поставки: от {ETA_DAYS_MIN} до {ETA_DAYS_MAX} дней")
    return value


def _eta_timestamp(days: int, *, from_ts: float | None = None) -> float:
    base = datetime.fromtimestamp(from_ts or time.time())
    target = (base + timedelta(days=int(days))).replace(
        hour=23, minute=59, second=59, microsecond=0,
    )
    return target.timestamp()


def _apply_request_eta(
    conn: sqlite3.Connection,
    request_id: int,
    days: Any,
    *,
    actor_max_id: int | None,
    actor_name: str,
    now: float,
) -> None:
    parsed_days = _validate_eta_days(days)
    eta_at = _eta_timestamp(parsed_days, from_ts=now)
    conn.execute(
        """UPDATE sm_requests SET expected_delivery_days=?, expected_delivery_at=?,
           eta_set_at=?, eta_reminder_sent_at=NULL, ordered_by_max_id=?, ordered_by_name=?,
           updated_at=? WHERE id=?""",
        (parsed_days, eta_at, now, actor_max_id, actor_name.strip(), now, request_id),
    )


def _enrich_request_eta(conn: sqlite3.Connection, result: dict) -> dict:
    days = result.get("expected_delivery_days")
    at = result.get("expected_delivery_at")
    if days is not None:
        try:
            days = int(days)
        except (TypeError, ValueError):
            days = None
    if at is not None:
        try:
            at = float(at)
        except (TypeError, ValueError):
            at = None
    result["expected_delivery_days"] = days
    result["expected_delivery_at"] = at
    result.pop("eta_display_line", None)
    result.pop("eta_early_days", None)
    result.pop("eta_date_label", None)
    result.pop("eta_label", None)
    result.pop("eta_days_remaining", None)
    result.pop("eta_today", None)
    result.pop("eta_overdue", None)
    result.pop("eta_days_overdue", None)

    status = str(result.get("status") or "")
    items = result.get("items") or []
    total_remaining = sum(max(0.0, float(item.get("remaining") or 0)) for item in items)
    any_received = any(float(item.get("received") or 0) > 0 for item in items)
    received_at = result.get("received_at")
    if received_at is not None:
        try:
            received_at = float(received_at)
        except (TypeError, ValueError):
            received_at = None

    if status in {"received", "closed"} or (any_received and total_remaining <= 0):
        display_at = received_at
        if not display_at and status in {"received", "closed"}:
            try:
                display_at = float(result.get("updated_at") or 0) or None
            except (TypeError, ValueError):
                display_at = None
        if display_at:
            label = f"Принято на базе {datetime.fromtimestamp(display_at).strftime('%d.%m.%Y')}"
        else:
            label = "Принято на базе"
        if at and display_at and display_at < at - 3600:
            early_days = max(1, int((at - display_at + 86399) // 86400))
            result["eta_early_days"] = early_days
            label += f" · на {early_days} дн. раньше"
        result["eta_display_line"] = label
    elif status == "partially_received" and total_remaining > 0 and at:
        date_label = datetime.fromtimestamp(at).strftime("%d.%m.%Y")
        remaining_sec = at - time.time()
        if remaining_sec > 86400:
            days_left = max(1, int((remaining_sec + 86399) // 86400))
            result["eta_display_line"] = f"Ожидаем остаток ~{date_label} · через {days_left} дн."
        elif remaining_sec > 0:
            result["eta_display_line"] = f"Ожидаем остаток ~{date_label} · сегодня"
        else:
            overdue = max(1, int((-remaining_sec + 86399) // 86400))
            result["eta_display_line"] = f"Остаток просрочен · {overdue} дн."
            result["eta_overdue"] = True
    elif at and status in {"in_transit", "partially_received", "accepted"}:
        result["eta_date_label"] = datetime.fromtimestamp(at).strftime("%d.%m.%Y")
        remaining_sec = at - time.time()
        if remaining_sec > 86400:
            result["eta_days_remaining"] = max(1, int((remaining_sec + 86399) // 86400))
            result["eta_label"] = f"через {result['eta_days_remaining']} дн."
        elif remaining_sec > 0:
            result["eta_today"] = True
            result["eta_days_remaining"] = 0
            result["eta_label"] = "сегодня"
        else:
            result["eta_overdue"] = True
            result["eta_days_overdue"] = max(1, int((-remaining_sec + 86399) // 86400))
            result["eta_label"] = f"просрочено {result['eta_days_overdue']} дн."
        result["eta_display_line"] = f"Ожидаем ~{result['eta_date_label']}"
        if result.get("eta_label"):
            result["eta_display_line"] += f" · {result['eta_label']}"

    row = conn.execute(
        """SELECT d.supplier_id, s.name supplier_name, d.created_by_max_id, d.created_by_name
           FROM sm_deliveries d
           LEFT JOIN sm_suppliers s ON s.id=d.supplier_id
           WHERE d.request_id=? ORDER BY d.created_at DESC, d.id DESC LIMIT 1""",
        (int(result["id"]),),
    ).fetchone()
    if row:
        if row["supplier_name"]:
            result["supplier_name"] = row["supplier_name"]
            result["supplier_id"] = row["supplier_id"]
        if not result.get("ordered_by_name") and row["created_by_name"]:
            result["ordered_by_name"] = row["created_by_name"]
            result["ordered_by_max_id"] = row["created_by_max_id"]
    return result


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


def _start_of_day_ts(dt: datetime) -> float:
    return datetime(dt.year, dt.month, dt.day).timestamp()


def _parse_document_date(value: Any) -> float:
    if value is None or value == "":
        return _start_of_day_ts(datetime.now())
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        return _start_of_day_ts(datetime.fromtimestamp(ts))
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return _start_of_day_ts(datetime.strptime(text, fmt))
        except ValueError:
            continue
    raise ValueError("Некорректная дата прихода")


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


def _cleanup_merge_artifacts(conn: sqlite3.Connection) -> None:
    receipt_cols = _columns(conn, "sm_receipts")
    if "merge_parts_json" in receipt_cols:
        conn.execute("UPDATE sm_receipts SET merge_parts_json='[]'")
    if "merged_into_receipt_id" in receipt_cols:
        conn.execute("UPDATE sm_receipts SET merged_into_receipt_id=NULL")
    if "merged_at" in receipt_cols:
        conn.execute("UPDATE sm_receipts SET merged_at=NULL")
    if "merged_by_max_id" in receipt_cols:
        conn.execute("UPDATE sm_receipts SET merged_by_max_id=NULL")
    if "merged_by_name" in receipt_cols:
        conn.execute("UPDATE sm_receipts SET merged_by_name=''")
    conn.execute("DROP TABLE IF EXISTS sm_receipt_merges")
    conn.execute(
        "DELETE FROM sm_audit_log WHERE action IN ('receipt_merge', 'receipt_unmerge')"
    )


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
        _add_column(conn, "sm_requests", "expected_delivery_days INTEGER")
        _add_column(conn, "sm_requests", "expected_delivery_at REAL")
        _add_column(conn, "sm_requests", "eta_set_at REAL")
        _add_column(conn, "sm_requests", "eta_reminder_sent_at REAL")
        _add_column(conn, "sm_requests", "ordered_by_max_id INTEGER")
        _add_column(conn, "sm_requests", "ordered_by_name TEXT NOT NULL DEFAULT ''")
        _add_column(conn, "sm_requests", "received_at REAL")
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
            CREATE TABLE IF NOT EXISTS sm_access_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                max_id INTEGER NOT NULL,
                display_name TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                assigned_role TEXT,
                created_at REAL NOT NULL,
                resolved_at REAL,
                resolved_by_max_id INTEGER,
                resolved_by_name TEXT NOT NULL DEFAULT '',
                notify_messages_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_sm_access_requests_max_status
                ON sm_access_requests(max_id, status, created_at DESC);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_sm_access_requests_one_pending
                ON sm_access_requests(max_id) WHERE status='pending';
            """
        )
        _add_column(conn, "sm_receipts", "payment_status TEXT NOT NULL DEFAULT 'pending'")
        _add_column(conn, "sm_receipts", "total_amount REAL NOT NULL DEFAULT 0")
        _add_column(conn, "sm_receipts", "priced_at REAL")
        _add_column(conn, "sm_receipts", "priced_by_max_id INTEGER")
        _add_column(conn, "sm_receipts", "priced_by_name TEXT NOT NULL DEFAULT ''")
        _add_column(conn, "sm_receipts", "sent_to_manager_at REAL")
        _add_column(conn, "sm_receipts", "sent_by_max_id INTEGER")
        _add_column(conn, "sm_receipts", "sent_by_name TEXT NOT NULL DEFAULT ''")
        _add_column(conn, "sm_receipts", "cancelled_at REAL")
        _add_column(conn, "sm_receipts", "cancelled_by_max_id INTEGER")
        _add_column(conn, "sm_receipts", "cancelled_by_name TEXT NOT NULL DEFAULT ''")
        _add_column(conn, "sm_receipts", "cancel_reason TEXT NOT NULL DEFAULT ''")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sm_receipt_lines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                receipt_id INTEGER NOT NULL,
                movement_id INTEGER,
                material_id INTEGER NOT NULL,
                quantity REAL NOT NULL DEFAULT 0,
                quantity_unit TEXT NOT NULL DEFAULT 'шт',
                billing_quantity REAL NOT NULL DEFAULT 0,
                billing_unit TEXT NOT NULL DEFAULT 'шт',
                unit_price REAL NOT NULL DEFAULT 0,
                line_total REAL NOT NULL DEFAULT 0,
                FOREIGN KEY(receipt_id) REFERENCES sm_receipts(id) ON DELETE CASCADE,
                FOREIGN KEY(material_id) REFERENCES sm_materials(id)
            );
            CREATE INDEX IF NOT EXISTS idx_sm_receipt_lines_receipt
                ON sm_receipt_lines(receipt_id);
            """
        )
        conn.execute(
            """INSERT INTO sm_receipt_lines(
                   receipt_id, movement_id, material_id, quantity, quantity_unit,
                   billing_quantity, billing_unit
               )
               SELECT o.receipt_id, o.id, o.material_id, o.quantity, m.unit,
                      o.quantity, m.unit
               FROM sm_stock_ops o
               JOIN sm_materials m ON m.id=o.material_id
               WHERE o.receipt_id IS NOT NULL AND o.op_type='receipt'
                 AND NOT EXISTS (
                     SELECT 1 FROM sm_receipt_lines rl
                     WHERE rl.movement_id=o.id
                 )"""
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
        conn.execute(
            "UPDATE sm_requests SET received_at=updated_at "
            "WHERE status IN ('received','closed') AND received_at IS NULL"
        )
        _add_column(conn, "sm_receipts", "document_date REAL")
        conn.execute(
            "UPDATE sm_receipts SET document_date=created_at WHERE document_date IS NULL"
        )
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version < 10:
            _cleanup_merge_artifacts(conn)
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
    document_date: Any = None,
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
        doc_ts = _parse_document_date(document_date)
        receipt_cur = conn.execute(
            """INSERT INTO sm_receipts(
                   site_id,supplier_id,actor_max_id,actor_name,note,
                   idempotency_key,created_at,document_date
               ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                site_id, supplier_id, actor_max_id, actor_name.strip(),
                note.strip(), idempotency_key, now, doc_ts,
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
            conn.execute(
                """INSERT INTO sm_receipt_lines(
                       receipt_id, movement_id, material_id, quantity, quantity_unit,
                       billing_quantity, billing_unit
                   ) VALUES(?,?,?,?,?,?,?)""",
                (
                    receipt_id,
                    movement_id,
                    material_id,
                    quantity,
                    material["unit"],
                    quantity,
                    material["unit"],
                ),
            )
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
            "document_date": doc_ts,
            "payment_status": "pending",
            "total_amount": 0,
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


def _default_site_id(sites: list[dict]) -> int:
    for site in sites:
        if str(site.get("name") or "").strip().lower() == "грузовой":
            return int(site["id"])
    return int(sites[0]["id"]) if sites else 0


def _get_receipt_lines(conn: sqlite3.Connection, receipt_id: int) -> list[dict]:
    rows = conn.execute(
        """SELECT rl.*, m.name material_name
           FROM sm_receipt_lines rl
           JOIN sm_materials m ON m.id=rl.material_id
           WHERE rl.receipt_id=?
           ORDER BY rl.id""",
        (int(receipt_id),),
    ).fetchall()
    return [dict(row) for row in rows]


def _decorate_receipt(result: dict, lines: list[dict]) -> dict:
    decorated = dict(result)
    decorated["items"] = lines or decorated.get("items") or []
    decorated["payment_status"] = str(decorated.get("payment_status") or "pending")
    decorated["total_amount"] = round(float(decorated.get("total_amount") or 0), 2)
    decorated["is_cancelled"] = bool(decorated.get("cancelled_at"))
    doc_ts = float(decorated.get("document_date") or decorated.get("created_at") or 0)
    created_ts = float(decorated.get("created_at") or 0)
    decorated["document_date"] = doc_ts
    decorated["document_date_label"] = (
        datetime.fromtimestamp(doc_ts).strftime("%d.%m.%Y") if doc_ts else ""
    )
    decorated["recorded_at_label"] = (
        datetime.fromtimestamp(created_ts).strftime("%d.%m.%Y %H:%M") if created_ts else ""
    )
    return decorated


def _receipt_is_cancelled(receipt: dict) -> bool:
    return bool(receipt.get("cancelled_at")) or receipt.get("payment_status") == "cancelled"


def _ensure_receipt_editable(receipt: dict) -> None:
    if _receipt_is_cancelled(receipt):
        raise ValueError("Приход отменён и больше не редактируется")


def find_similar_receipt_today(
    *,
    site_id: int,
    supplier_id: int,
    exclude_receipt_id: int | None = None,
) -> dict | None:
    with _connect() as conn:
        rows = conn.execute(
            """SELECT r.id, r.created_at, r.site_id, r.supplier_id, s.name site_name,
                      sup.name supplier_name, r.actor_name, r.payment_status
               FROM sm_receipts r
               JOIN sm_sites s ON s.id=r.site_id
               JOIN sm_suppliers sup ON sup.id=r.supplier_id
               WHERE r.site_id=? AND r.supplier_id=?
                 AND r.cancelled_at IS NULL
                 AND COALESCE(r.payment_status, 'pending') != 'sent'
               ORDER BY r.created_at DESC, r.id DESC""",
            (int(site_id), int(supplier_id)),
        ).fetchall()
    today = datetime.now().date()
    for row in rows:
        receipt_id = int(row["id"])
        if exclude_receipt_id and receipt_id == int(exclude_receipt_id):
            continue
        if datetime.fromtimestamp(float(row["created_at"])).date() == today:
            return dict(row)
    return None


def _get_receipt_conn(conn: sqlite3.Connection, receipt_id: int) -> dict:
    row = conn.execute(
        """SELECT r.*, s.name site_name, sup.name supplier_name
           FROM sm_receipts r
           JOIN sm_sites s ON s.id=r.site_id
           JOIN sm_suppliers sup ON sup.id=r.supplier_id
           WHERE r.id=?""",
        (int(receipt_id),),
    ).fetchone()
    if not row:
        raise ValueError("Приход не найден")
    lines = _get_receipt_lines(conn, receipt_id)
    if not lines:
        movements = conn.execute(
            """SELECT o.id movement_id, o.material_id, o.quantity, m.name material_name,
                      m.unit material_unit
               FROM sm_stock_ops o
               JOIN sm_materials m ON m.id=o.material_id
               WHERE o.receipt_id=? AND o.op_type='receipt'
               ORDER BY o.id""",
            (int(receipt_id),),
        ).fetchall()
        lines = [dict(item) for item in movements]
    return _decorate_receipt(dict(row), lines)


def get_receipt(receipt_id: int) -> dict:
    with _connect() as conn:
        return _get_receipt_conn(conn, int(receipt_id))


def edit_receipt(
    *,
    receipt_id: int,
    supplier_id: int,
    items: list[dict],
    note: str = "",
    document_date: Any = None,
    actor_max_id: int | None,
    actor_name: str,
) -> dict:
    if not isinstance(items, list) or not items:
        raise ValueError("Добавьте хотя бы одну позицию прихода")
    combined: dict[int, float] = {}
    for item in items:
        material_id = int(item["material_id"])
        combined[material_id] = round(combined.get(material_id, 0) + _qty(item.get("quantity")), 3)
    if not combined or any(qty <= 0 for qty in combined.values()):
        raise ValueError("Количество каждой позиции должно быть больше нуля")

    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        before = _get_receipt_conn(conn, receipt_id)
        _ensure_receipt_editable(before)
        site_id = int(before["site_id"])
        supplier = _require(conn, "sm_suppliers", supplier_id, "Поставщик")
        for material_id in combined:
            _require(conn, "sm_materials", material_id, "Материал")

        old_items = {
            int(item["material_id"]): item for item in before.get("items") or []
        }
        all_materials = set(old_items) | set(combined)
        for material_id in all_materials:
            old_qty = float(old_items.get(material_id, {}).get("quantity") or 0)
            new_qty = float(combined.get(material_id) or 0)
            delta = round(new_qty - old_qty, 3)
            if delta >= 0:
                continue
            balance = _balance(conn, site_id, material_id)
            if balance + delta < -1e-9:
                material = _require(conn, "sm_materials", material_id, "Материал")
                raise ValueError(
                    f"Недостаточно остатка для уменьшения: {_material(material)['name']}"
                )

        conn.execute(
            "UPDATE sm_receipts SET supplier_id=?, note=? WHERE id=?",
            (supplier_id, note.strip(), int(receipt_id)),
        )
        if document_date is not None:
            doc_ts = _parse_document_date(document_date)
            conn.execute(
                "UPDATE sm_receipts SET document_date=? WHERE id=?",
                (doc_ts, int(receipt_id)),
            )

        for material_id, item in old_items.items():
            movement_id = int(item["movement_id"])
            new_qty = float(combined.get(material_id) or 0)
            if new_qty <= 0:
                conn.execute("DELETE FROM sm_stock_ops WHERE id=?", (movement_id,))
                conn.execute("DELETE FROM sm_receipt_lines WHERE movement_id=?", (movement_id,))
                continue
            conn.execute(
                """UPDATE sm_stock_ops
                   SET quantity=?, quantity_delta=?, supplier_id=?, note=?, actor_max_id=?, actor_name=?
                   WHERE id=?""",
                (
                    new_qty,
                    new_qty,
                    supplier_id,
                    note.strip(),
                    actor_max_id,
                    actor_name.strip(),
                    movement_id,
                ),
            )

        for material_id, new_qty in combined.items():
            if material_id in old_items:
                continue
            movement_id = _movement(
                conn,
                site_id=site_id,
                material_id=material_id,
                supplier_id=supplier_id,
                op_type="receipt",
                quantity=new_qty,
                delta=new_qty,
                actor_max_id=actor_max_id,
                actor_name=actor_name,
                note=note,
                receipt_id=int(receipt_id),
            )
            material = _require(conn, "sm_materials", material_id, "Материал")
            conn.execute(
                """INSERT INTO sm_receipt_lines(
                       receipt_id, movement_id, material_id, quantity, quantity_unit,
                       billing_quantity, billing_unit
                   ) VALUES(?,?,?,?,?,?,?)""",
                (
                    receipt_id,
                    movement_id,
                    material_id,
                    new_qty,
                    _material(material)["unit"],
                    new_qty,
                    _material(material)["unit"],
                ),
            )

        conn.execute("DELETE FROM sm_receipt_lines WHERE receipt_id=? AND movement_id IS NOT NULL "
                     "AND movement_id NOT IN (SELECT id FROM sm_stock_ops WHERE receipt_id=?)",
                     (int(receipt_id), int(receipt_id)))
        for row in conn.execute(
            """SELECT o.id movement_id, o.material_id, o.quantity, m.unit material_unit
               FROM sm_stock_ops o
               JOIN sm_materials m ON m.id=o.material_id
               WHERE o.receipt_id=? AND o.op_type='receipt'""",
            (int(receipt_id),),
        ).fetchall():
            existing = conn.execute(
                "SELECT id FROM sm_receipt_lines WHERE receipt_id=? AND movement_id=?",
                (int(receipt_id), int(row["movement_id"])),
            ).fetchone()
            if existing:
                conn.execute(
                    """UPDATE sm_receipt_lines
                       SET quantity=?, billing_quantity=?, quantity_unit=?, billing_unit=?
                       WHERE id=?""",
                    (
                        float(row["quantity"]),
                        float(row["quantity"]),
                        row["material_unit"],
                        row["material_unit"],
                        int(existing["id"]),
                    ),
                )
        conn.execute(
            """UPDATE sm_receipts
               SET payment_status='pending', total_amount=0,
                   priced_at=NULL, priced_by_max_id=NULL, priced_by_name='',
                   sent_to_manager_at=NULL, sent_by_max_id=NULL, sent_by_name=''
               WHERE id=?""",
            (int(receipt_id),),
        )

        after = _get_receipt_conn(conn, receipt_id)
        after["supplier"] = _supplier(supplier)
        _audit(
            conn,
            actor_max_id,
            actor_name,
            "receipt_edit",
            "receipt",
            receipt_id,
            {"before": before, "after": after},
        )
        conn.commit()
        return after


def cancel_receipt(
    *,
    receipt_id: int,
    reason: str,
    actor_max_id: int | None,
    actor_name: str,
    idempotency_key: str | None = None,
) -> dict:
    note = str(reason or "").strip()
    if not note:
        raise ValueError("Укажите причину отмены")
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cached = _cached(conn, idempotency_key, "receipt_cancel")
        if cached:
            return cached
        before = _get_receipt_conn(conn, int(receipt_id))
        _ensure_receipt_editable(before)
        site_id = int(before["site_id"])
        movements = conn.execute(
            """SELECT id, material_id, quantity, quantity_delta
               FROM sm_stock_ops
               WHERE receipt_id=? AND op_type='receipt'
               ORDER BY id""",
            (int(receipt_id),),
        ).fetchall()
        if not movements:
            raise ValueError("У прихода нет движений для отмены")
        reversed_items: list[dict] = []
        cancel_note = f"Отмена прихода №{receipt_id}: {note}"
        for movement in movements:
            movement_id = int(movement["id"])
            if conn.execute(
                "SELECT 1 FROM sm_stock_ops WHERE related_op_id=? AND op_type='reversal'",
                (movement_id,),
            ).fetchone():
                continue
            material_id = int(movement["material_id"])
            qty = float(movement["quantity"])
            delta = -float(movement["quantity_delta"])
            balance = _balance(conn, site_id, material_id)
            if balance + delta < -1e-9:
                material = _require(conn, "sm_materials", material_id, "Материал")
                raise ValueError(
                    f"Нельзя отменить: {material['name']} уже списан со склада "
                    f"(осталось {_audit_qty_label(balance)}, в приходе {_audit_qty_label(qty)})"
                )
            reversal_id = _movement(
                conn,
                site_id=site_id,
                material_id=material_id,
                supplier_id=before.get("supplier_id"),
                op_type="reversal",
                quantity=abs(delta),
                delta=delta,
                actor_max_id=actor_max_id,
                actor_name=actor_name,
                note=cancel_note,
                related_op_id=movement_id,
                receipt_id=int(receipt_id),
            )
            material_row = _require(conn, "sm_materials", material_id, "Материал")
            reversed_items.append(
                {
                    "movement_id": movement_id,
                    "reversal_id": reversal_id,
                    "material_id": material_id,
                    "material_name": material_row["name"],
                    "material_unit": material_row["unit"],
                    "quantity": qty,
                }
            )
        now = time.time()
        conn.execute(
            """UPDATE sm_receipts
               SET cancelled_at=?, cancelled_by_max_id=?, cancelled_by_name=?,
                   cancel_reason=?, payment_status='cancelled'
               WHERE id=?""",
            (now, actor_max_id, actor_name.strip(), note, int(receipt_id)),
        )
        result = _get_receipt_conn(conn, int(receipt_id))
        result["reversed_items"] = reversed_items
        _audit(
            conn,
            actor_max_id,
            actor_name,
            "receipt_cancel",
            "receipt",
            receipt_id,
            {
                "before": before,
                "reason": note,
                "items": reversed_items,
                "site_name": before.get("site_name"),
                "supplier_name": before.get("supplier_name"),
            },
        )
        _cache(conn, idempotency_key, "receipt_cancel", result)
        conn.commit()
        return result


def list_payment_receipts(
    *,
    site_id: int | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict]:
    clauses, params = [], []
    if site_id is not None:
        clauses.append("r.site_id=?")
        params.append(int(site_id))
    if status:
        clauses.append("r.payment_status=?")
        params.append(str(status))
    clauses.append("r.cancelled_at IS NULL")
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with _connect() as conn:
        rows = conn.execute(
            f"""SELECT r.id, r.site_id, s.name site_name, r.supplier_id, sup.name supplier_name,
                       r.actor_name, r.note, r.created_at, r.document_date, r.payment_status,
                       r.total_amount, r.priced_by_name, r.sent_to_manager_at, r.cancelled_at
                FROM sm_receipts r
                JOIN sm_sites s ON s.id=r.site_id
                JOIN sm_suppliers sup ON sup.id=r.supplier_id
                {where}
                ORDER BY r.created_at DESC, r.id DESC
                LIMIT ?""",
            (*params, max(1, min(int(limit), 100))),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            lines = _get_receipt_lines(conn, int(row["id"]))
            item = _decorate_receipt(item, lines)
            item["is_cancelled"] = bool(item.get("cancelled_at"))
            item["total_amount"] = round(float(item.get("total_amount") or 0), 2)
            result.append(item)
    return result


def price_receipt(
    *,
    receipt_id: int,
    lines: list[dict],
    actor_max_id: int | None,
    actor_name: str,
) -> dict:
    if not lines:
        raise ValueError("Укажите цены по позициям")
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = _get_receipt_conn(conn, receipt_id)
        _ensure_receipt_editable(current)
        status = str(current.get("payment_status") or "pending")
        if status != "pending":
            raise ValueError("Цены по этому приходу уже сохранены")
        total = 0.0
        for payload in lines:
            line_id = payload.get("id")
            movement_id = payload.get("movement_id")
            unit_price = round(float(payload.get("unit_price") or 0), 2)
            if unit_price < 0:
                raise ValueError("Цена не может быть отрицательной")
            billing_quantity = _qty(payload.get("billing_quantity", payload.get("quantity")))
            billing_unit = str(payload.get("billing_unit") or payload.get("quantity_unit") or "шт").strip()
            line_total = round(billing_quantity * unit_price, 2)
            if line_id is not None:
                row = conn.execute(
                    "SELECT id FROM sm_receipt_lines WHERE id=? AND receipt_id=?",
                    (int(line_id), int(receipt_id)),
                ).fetchone()
            elif movement_id is not None:
                row = conn.execute(
                    "SELECT id FROM sm_receipt_lines WHERE movement_id=? AND receipt_id=?",
                    (int(movement_id), int(receipt_id)),
                ).fetchone()
            else:
                raise ValueError("Укажите id или movement_id позиции")
            if not row:
                raise ValueError("Позиция прихода не найдена")
            conn.execute(
                """UPDATE sm_receipt_lines
                   SET billing_quantity=?, billing_unit=?, unit_price=?, line_total=?
                   WHERE id=?""",
                (billing_quantity, billing_unit, unit_price, line_total, int(row["id"])),
            )
            total += line_total
        now = time.time()
        conn.execute(
            """UPDATE sm_receipts
               SET payment_status='priced', total_amount=?, priced_at=?, priced_by_max_id=?,
                   priced_by_name=?, sent_to_manager_at=NULL, sent_by_max_id=NULL, sent_by_name=''
               WHERE id=?""",
            (round(total, 2), now, actor_max_id, actor_name.strip(), int(receipt_id)),
        )
        result = _get_receipt_conn(conn, receipt_id)
        _audit(
            conn,
            actor_max_id,
            actor_name,
            "receipt_price",
            "receipt",
            receipt_id,
            {"total_amount": round(total, 2), "lines": len(lines)},
        )
        conn.commit()
        return result


def send_receipt_to_manager(
    *,
    receipt_id: int,
    actor_max_id: int | None,
    actor_name: str,
) -> dict:
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        receipt = _get_receipt_conn(conn, receipt_id)
        _ensure_receipt_editable(receipt)
        if receipt.get("payment_status") != "priced":
            raise ValueError("Сначала сохраните цены по всем позициям")
        if float(receipt.get("total_amount") or 0) <= 0:
            raise ValueError("Итоговая сумма должна быть больше нуля")
        now = time.time()
        conn.execute(
            """UPDATE sm_receipts
               SET payment_status='sent', sent_to_manager_at=?, sent_by_max_id=?, sent_by_name=?
               WHERE id=?""",
            (now, actor_max_id, actor_name.strip(), int(receipt_id)),
        )
        result = _get_receipt_conn(conn, receipt_id)
        _audit(
            conn,
            actor_max_id,
            actor_name,
            "receipt_send_manager",
            "receipt",
            receipt_id,
            {"total_amount": result.get("total_amount")},
        )
        conn.commit()
        return result


def list_audit_log(
    *,
    limit: int = 100,
    offset: int = 0,
    entity_type: str | None = None,
) -> dict:
    limit, offset = max(1, min(int(limit), 200)), max(0, int(offset))
    clauses, params = [], []
    if entity_type:
        clauses.append("entity_type=?")
        params.append(entity_type)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with _connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) n FROM sm_audit_log{where}", params).fetchone()["n"]
        rows = conn.execute(
            f"""SELECT id, actor_max_id, actor_name, action, entity_type, entity_id,
                       details_json, created_at
                FROM sm_audit_log{where}
                ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?""",
            (*params, limit, offset),
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        try:
            item["details"] = json.loads(item.pop("details_json") or "{}")
        except json.JSONDecodeError:
            item["details"] = {}
        items.append(item)
        item["description"] = describe_audit_entry(item)
    return {"items": items, "total": int(total), "limit": limit, "offset": offset}


AUDIT_ACTION_LABELS = {
    "receipt": "Приход",
    "receipt_batch": "Приход партией",
    "receipt_edit": "Правка прихода",
    "receipt_cancel": "Отмена прихода",
    "receipt_price": "Цены прихода",
    "receipt_send_manager": "Отправка руководителю",
    "issue": "Расход",
    "transfer": "Перемещение",
    "inventory_adjustment": "Корректировка",
    "reversal": "Сторно",
    "request_create": "Заявка",
    "request_transition": "Статус заявки",
    "request_eta_update": "Срок поставки",
    "delivery_create": "Заказ у поставщика",
    "delivery_receive": "Поставка на базу",
    "role_update": "Роль",
    "site_material_minimum": "Минимум",
    "material_update": "Материал",
    "supplier_price_update": "Цена поставщика",
}

AUDIT_ENTITY_LABELS = {
    "receipt": "Приход",
    "request": "Заявка",
    "delivery": "Заказ",
    "movement": "Движение",
    "transfer": "Перемещение",
    "material": "Материал",
    "role": "Роль",
    "supplier": "Поставщик",
    "site": "Площадка",
}

AUDIT_STATUS_LABELS = {
    "submitted": "отправлена",
    "accepted": "принята",
    "in_transit": "в пути",
    "partially_received": "частично на базе",
    "received": "полностью на базе",
    "closed": "закрыта",
    "rejected": "отклонена",
    "cancelled": "отменена",
}

ROLE_LABELS = {
    "master": "Мастер",
    "supply": "Снабжение",
    "manager": "Руководитель",
    "admin": "Администратор",
}


def _audit_qty_label(value: Any) -> str:
    number = round(float(value or 0), 3)
    text = str(int(number)) if number.is_integer() else f"{number:.3f}".rstrip("0").rstrip(".")
    return text


def _audit_entity_label(entity_type: str | None, entity_id: str | None) -> str:
    title = AUDIT_ENTITY_LABELS.get(str(entity_type or ""), str(entity_type or "Объект"))
    if entity_id:
        return f"{title} №{entity_id}"
    return title


def _audit_material_lines(items: Iterable[dict]) -> list[str]:
    lines: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = (
            item.get("material_name")
            or ((item.get("material") or {}).get("name") if isinstance(item.get("material"), dict) else None)
            or "Материал"
        )
        qty = item.get("quantity") or item.get("ordered") or item.get("billing_quantity") or 0
        unit = (
            item.get("material_unit") or item.get("quantity_unit") or item.get("unit")
            or ((item.get("material") or {}).get("unit") if isinstance(item.get("material"), dict) else None)
            or ""
        )
        lines.append(f"• {name}: {_audit_qty_label(qty)} {unit}".rstrip())
    return lines


def _audit_receipt_diff(before: dict, after: dict) -> list[str]:
    before_items = {
        int(item.get("material_id") or 0): item
        for item in (before.get("items") or [])
        if isinstance(item, dict) and item.get("material_id") is not None
    }
    after_items = {
        int(item.get("material_id") or 0): item
        for item in (after.get("items") or [])
        if isinstance(item, dict) and item.get("material_id") is not None
    }
    lines: list[str] = []
    for material_id, after_item in after_items.items():
        name = after_item.get("material_name") or "Материал"
        qty_after = after_item.get("quantity") or after_item.get("billing_quantity") or 0
        unit = after_item.get("material_unit") or after_item.get("quantity_unit") or ""
        if material_id in before_items:
            qty_before = before_items[material_id].get("quantity") or 0
            if abs(float(qty_before) - float(qty_after)) > 1e-9:
                lines.append(
                    f"• {name}: {_audit_qty_label(qty_before)} → {_audit_qty_label(qty_after)} {unit}".rstrip()
                )
        else:
            lines.append(f"• + {name}: {_audit_qty_label(qty_after)} {unit}".rstrip())
    for material_id, before_item in before_items.items():
        if material_id not in after_items:
            name = before_item.get("material_name") or "Материал"
            qty = before_item.get("quantity") or 0
            unit = before_item.get("material_unit") or before_item.get("quantity_unit") or ""
            lines.append(f"• − {name}: {_audit_qty_label(qty)} {unit}".rstrip())
    return lines


def describe_audit_entry(item: dict) -> dict:
    action = str(item.get("action") or "")
    details = item.get("details") or {}
    if not isinstance(details, dict):
        details = {}
    lines: list[str] = []

    if action == "receipt_edit":
        before, after = details.get("before") or {}, details.get("after") or {}
        site = after.get("site_name") or before.get("site_name")
        supplier = after.get("supplier_name") or before.get("supplier_name")
        if site:
            lines.append(f"Площадка: {site}")
        if supplier:
            lines.append(f"Поставщик: {supplier}")
        lines.extend(_audit_receipt_diff(before, after))
    elif action == "receipt_cancel":
        if details.get("site_name"):
            lines.append(f"Площадка: {details['site_name']}")
        if details.get("supplier_name"):
            lines.append(f"Поставщик: {details['supplier_name']}")
        if details.get("reason"):
            lines.append(f"Причина: {details['reason']}")
        lines.extend(_audit_material_lines(details.get("items") or []))
    elif action in {"receipt", "receipt_batch"}:
        site = details.get("site_name") or ((details.get("site") or {}).get("name") if isinstance(details.get("site"), dict) else None)
        supplier = details.get("supplier_name") or ((details.get("supplier") or {}).get("name") if isinstance(details.get("supplier"), dict) else None)
        if site:
            lines.append(f"Площадка: {site}")
        if supplier:
            lines.append(f"Поставщик: {supplier}")
        lines.extend(_audit_material_lines(details.get("items") or []))
    elif action == "receipt_price":
        if details.get("total_amount") is not None:
            lines.append(f"Итого: {round(float(details['total_amount']), 2):.2f} ₽")
        if details.get("lines") is not None:
            lines.append(f"Позиций с ценой: {details['lines']}")
    elif action == "receipt_send_manager":
        if details.get("total_amount") is not None:
            lines.append(f"Отправлено на оплату: {round(float(details['total_amount']), 2):.2f} ₽")
    elif action == "request_create":
        if details.get("site_name"):
            lines.append(f"Площадка: {details['site_name']}")
        if details.get("comment"):
            lines.append(f"Комментарий: {details['comment']}")
        lines.extend(_audit_material_lines(details.get("items") or []))
    elif action == "request_transition":
        old = AUDIT_STATUS_LABELS.get(str(details.get("from") or ""), details.get("from") or "—")
        new = AUDIT_STATUS_LABELS.get(str(details.get("to") or ""), details.get("to") or "—")
        lines.append(f"Было: {old} → стало: {new}")
        if details.get("supply_comment"):
            lines.append(f"Комментарий: {details['supply_comment']}")
    elif action == "request_eta_update":
        if details.get("from_days") is not None:
            lines.append(f"Было: {details['from_days']} дн.")
        if details.get("to_days") is not None:
            lines.append(f"Стало: {details['to_days']} дн.")
    elif action == "delivery_create":
        if details.get("supplier_name"):
            lines.append(f"Поставщик: {details['supplier_name']}")
        if details.get("note"):
            lines.append(f"Примечание: {details['note']}")
        lines.extend(_audit_material_lines(details.get("items") or []))
    elif action == "delivery_receive":
        if details.get("note"):
            lines.append(f"Комментарий: {details['note']}")
        if details.get("early_delivery"):
            lines.append("Досрочная поставка")
        received = details.get("items") or []
        if received:
            lines.append("Принято на базу:")
            lines.extend(_audit_material_lines(received))
    elif action == "material_update":
        before, after = details.get("before") or {}, details.get("after") or {}
        for field, label in (("name", "Название"), ("unit", "Единица"), ("min_level", "Минимум")):
            if before.get(field) != after.get(field):
                lines.append(f"{label}: {before.get(field)!s} → {after.get(field)!s}")
        if before.get("active") != after.get("active"):
            lines.append("Статус: " + ("включён" if after.get("active") else "отключён"))
    elif action == "site_material_minimum":
        if details.get("site", {}).get("name"):
            lines.append(f"Площадка: {details['site']['name']}")
        material = details.get("material") or {}
        if material.get("name"):
            lines.append(f"Материал: {material['name']}")
        if details.get("min_level") is not None:
            lines.append(f"Минимум: {_audit_qty_label(details['min_level'])}")
    elif action == "role_update":
        if details.get("max_id") is not None:
            lines.append(f"MAX id: {details['max_id']}")
        if details.get("role"):
            lines.append(f"Роль: {ROLE_LABELS.get(details['role'], details['role'])}")
        site_ids = details.get("site_ids") or []
        if site_ids:
            lines.append(f"Площадки: {', '.join(str(x) for x in site_ids)}")
        else:
            lines.append("Площадки: все")
        lines.append("Статус: " + ("активна" if details.get("active", True) else "отключена"))
    elif action == "issue":
        if details.get("material_name"):
            lines.append(f"Материал: {details['material_name']}")
        if details.get("quantity") is not None:
            unit = details.get("material_unit") or details.get("unit") or ""
            lines.append(f"Количество: {_audit_qty_label(details['quantity'])} {unit}".rstrip())
        if details.get("note"):
            lines.append(f"Примечание: {details['note']}")
    elif action == "transfer":
        if details.get("from_site_name") or details.get("to_site_name"):
            lines.append(
                f"Маршрут: {details.get('from_site_name') or '?'} → {details.get('to_site_name') or '?'}"
            )
        if details.get("material_name"):
            lines.append(f"Материал: {details['material_name']}")
        if details.get("quantity") is not None:
            unit = details.get("material_unit") or ""
            lines.append(f"Количество: {_audit_qty_label(details['quantity'])} {unit}".rstrip())
    elif action == "inventory_adjustment":
        if details.get("material_name"):
            lines.append(f"Материал: {details['material_name']}")
        if details.get("quantity_delta") is not None:
            lines.append(f"Изменение: {_audit_qty_label(details['quantity_delta'])}")
        if details.get("note"):
            lines.append(f"Причина: {details['note']}")
    elif action == "supplier_price_update":
        if details.get("supplier_name"):
            lines.append(f"Поставщик: {details['supplier_name']}")
        for field in ("price", "unit_price", "amount", "value"):
            if details.get(field) is not None:
                lines.append(f"Значение: {details[field]}")
                break

    return {
        "action_label": AUDIT_ACTION_LABELS.get(action, action.replace("_", " ")),
        "entity_label": _audit_entity_label(item.get("entity_type"), item.get("entity_id")),
        "lines": lines,
    }


def get_audit_entry(entry_id: int) -> dict:
    with _connect() as conn:
        row = conn.execute(
            """SELECT id, actor_max_id, actor_name, action, entity_type, entity_id,
                      details_json, created_at
               FROM sm_audit_log WHERE id=?""",
            (int(entry_id),),
        ).fetchone()
        if not row:
            raise ValueError("Запись журнала не найдена")
        item = dict(row)
        try:
            item["details"] = json.loads(item.pop("details_json") or "{}")
        except json.JSONDecodeError:
            item["details"] = {}
        item["description"] = describe_audit_entry(item)
        return item


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


_MOVEMENT_JOINS = """
FROM sm_stock_ops o
JOIN sm_sites s ON s.id=o.site_id
JOIN sm_materials m ON m.id=o.material_id
LEFT JOIN sm_suppliers sup ON sup.id=o.supplier_id
LEFT JOIN sm_requests r ON r.id=o.request_id
LEFT JOIN sm_delivery_items di ON di.id=o.delivery_item_id
LEFT JOIN sm_deliveries d ON d.id=di.delivery_id
LEFT JOIN sm_receipts rc ON rc.id=o.receipt_id
"""

_MOVEMENT_SELECT = f"""
SELECT o.*, s.name site_name, m.name material_name, m.unit material_unit,
       sup.name supplier_name,
       r.requested_by_name, r.requested_by_max_id,
       d.created_by_name supply_by_name, d.id delivery_ref_id,
       rc.actor_name receipt_actor_name
{_MOVEMENT_JOINS}
"""


def _batch_items_for_movement(conn: sqlite3.Connection, row: sqlite3.Row) -> list[dict]:
    receipt_id = row["receipt_id"]
    if receipt_id:
        siblings = conn.execute(
            """SELECT o.id movement_id, o.material_id, o.quantity, m.name material_name,
                      m.unit material_unit
               FROM sm_stock_ops o JOIN sm_materials m ON m.id=o.material_id
               WHERE o.receipt_id=? ORDER BY o.id""",
            (int(receipt_id),),
        ).fetchall()
        return [dict(item) for item in siblings]
    delivery_item_id = row["delivery_item_id"]
    if delivery_item_id:
        delivery = conn.execute(
            "SELECT delivery_id FROM sm_delivery_items WHERE id=?", (int(delivery_item_id),)
        ).fetchone()
        if delivery:
            items = conn.execute(
                """SELECT di.id delivery_item_id, ri.material_id, m.name material_name,
                          m.unit material_unit, di.quantity
                   FROM sm_delivery_items di
                   JOIN sm_request_items ri ON ri.id=di.request_item_id
                   JOIN sm_materials m ON m.id=ri.material_id
                   WHERE di.delivery_id=? ORDER BY di.id""",
                (int(delivery["delivery_id"]),),
            ).fetchall()
            return [dict(item) for item in items]
    return []


def _serialize_movement(conn: sqlite3.Connection, row: sqlite3.Row, *, detailed: bool = False) -> dict:
    item = dict(row)
    item["received_by_name"] = (
        item.get("actor_name")
        or item.get("receipt_actor_name")
        or ""
    ).strip()
    item["ordered_by_name"] = (item.get("supply_by_name") or item.get("requested_by_name") or "").strip()
    if detailed:
        item["batch_items"] = _batch_items_for_movement(conn, row)
    else:
        item.pop("batch_items", None)
    for key in ("receipt_actor_name", "supply_by_name", "requested_by_max_id", "delivery_ref_id"):
        item.pop(key, None)
    return item


def get_movement(movement_id: int, *, detailed: bool = True) -> dict:
    with _connect() as conn:
        row = conn.execute(
            f"{_MOVEMENT_SELECT} WHERE o.id=?", (int(movement_id),)
        ).fetchone()
        if not row:
            raise ValueError("Движение не найдено")
        return _serialize_movement(conn, row, detailed=detailed)


def list_movements(*, site_id: int | None = None, material_id: int | None = None,
                   op_type: str | None = None, request_id: int | None = None,
                   limit: int = 50, offset: int = 0, detailed: bool = False) -> dict:
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
            f"""{_MOVEMENT_SELECT} {where}
                ORDER BY o.created_at DESC,o.id DESC LIMIT ? OFFSET ?""",
            (*params, limit, offset),
        ).fetchall()
        include_batch = detailed or material_id is not None
        items = [_serialize_movement(conn, row, detailed=include_batch) for row in rows]
    return {"items": items, "total": int(total), "limit": limit, "offset": offset}


def create_request(*, site_id: int, material_id: int | None = None, quantity: Any = None,
                   items: list[dict] | None = None, urgency: str = "plan",
                   actor_max_id: int | None, actor_name: str, comment: str = "",
                   status: str = "submitted", idempotency_key: str | None = None,
                   confirm_duplicate: bool = False) -> dict:
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
            existing = _find_open_request_conn(conn, int(site_id), mid)
            if existing and not confirm_duplicate:
                status_label = {
                    "draft": "Черновик", "submitted": "Новая", "accepted": "Принята",
                    "in_transit": "В пути", "partially_received": "Принята частично",
                    "received": "Получена",
                }.get(str(existing.get("status") or ""), str(existing.get("status") or "в работе"))
                raise ValueError(
                    f"По этому материалу уже есть заявка №{existing['id']} ({status_label}). "
                    "Подтвердите создание повторной заявки."
                )
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
    return _enrich_request_eta(conn, result)


def get_request(request_id: int) -> dict:
    with _connect() as conn:
        return _get_request(conn, int(request_id))


_TIMELINE_STATUS = {
    "submitted": "отправлена",
    "accepted": "принята",
    "in_transit": "в пути",
    "partially_received": "частично на базе",
    "received": "полностью на базе",
    "closed": "закрыта",
    "rejected": "отклонена",
    "cancelled": "отменена",
}


def _timeline_entry(action: str, details: dict) -> tuple[str, str]:
    if action == "request_create":
        return "Заявка создана", ""
    if action == "request_transition":
        status = _TIMELINE_STATUS.get(str(details.get("to") or ""), str(details.get("to") or ""))
        return f"Статус: {status}", ""
    if action == "request_eta_update":
        days = details.get("to_days")
        return "Срок поставки изменён", f"→ {days} дн." if days else ""
    if action == "delivery_create":
        supplier = details.get("supplier_name") or ""
        return "Заказ у поставщика", supplier
    if action == "delivery_receive":
        return "Поставка на базу", ""
    return action, ""


def get_request_timeline(request_id: int) -> list[dict]:
    request_id = int(request_id)
    with _connect() as conn:
        delivery_ids = [
            str(row["id"])
            for row in conn.execute(
                "SELECT id FROM sm_deliveries WHERE request_id=? ORDER BY id",
                (request_id,),
            )
        ]
        clauses = ["(entity_type='request' AND entity_id=?)"]
        params: list[Any] = [str(request_id)]
        if delivery_ids:
            placeholders = ",".join("?" * len(delivery_ids))
            clauses.append(f"(entity_type='delivery' AND entity_id IN ({placeholders}))")
            params.extend(delivery_ids)
        rows = conn.execute(
            f"""SELECT actor_max_id, actor_name, action, details_json, created_at
                FROM sm_audit_log
                WHERE {" OR ".join(clauses)}
                ORDER BY created_at ASC, id ASC""",
            params,
        ).fetchall()
        timeline: list[dict] = []
        for row in rows:
            try:
                details = json.loads(row["details_json"] or "{}")
            except json.JSONDecodeError:
                details = {}
            if not isinstance(details, dict):
                details = {}
            label, detail = _timeline_entry(str(row["action"]), details)
            timeline.append(
                {
                    "created_at": float(row["created_at"]),
                    "action": str(row["action"]),
                    "label": label,
                    "detail": detail,
                    "actor_name": str(row["actor_name"] or ""),
                    "actor_max_id": row["actor_max_id"],
                }
            )
        return timeline


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


def find_open_request_for_material(*, site_id: int, material_id: int) -> dict | None:
    with _connect() as conn:
        return _find_open_request_conn(conn, int(site_id), int(material_id))


def _find_open_request_conn(
    conn: sqlite3.Connection, site_id: int, material_id: int
) -> dict | None:
    row = conn.execute(
        """SELECT r.id, r.status, r.created_at, r.urgency, r.comment
           FROM sm_requests r
           JOIN sm_request_items i ON i.request_id=r.id
           WHERE r.site_id=? AND i.material_id=?
             AND r.status NOT IN ('closed','rejected','cancelled')
           ORDER BY r.updated_at DESC, r.id DESC
           LIMIT 1""",
        (int(site_id), int(material_id)),
    ).fetchone()
    return dict(row) if row else None


def transition_request(*, request_id: int, new_status: str, actor_role: str,
                       actor_max_id: int | None, actor_name: str,
                       comment: str = "", expected_delivery_days: int | None = None,
                       idempotency_key: str | None = None) -> dict:
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
        if new_status == "in_transit":
            if expected_delivery_days is None:
                raise ValueError("Укажите срок поставки в днях")
            _apply_request_eta(
                conn, request_id, expected_delivery_days,
                actor_max_id=actor_max_id, actor_name=actor_name, now=now,
            )
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


def update_request_eta(*, request_id: int, expected_delivery_days: int,
                       actor_role: str, actor_max_id: int | None, actor_name: str,
                       idempotency_key: str | None = None) -> dict:
    if actor_role not in {"supply", "admin"}:
        raise ValueError("Недостаточно прав для изменения срока")
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cached = _cached(conn, idempotency_key, "request_eta_update")
        if cached:
            return cached
        current = _get_request(conn, request_id)
        if current["status"] not in {"in_transit", "partially_received"}:
            raise ValueError("Срок можно менять только для заявок в пути")
        now = time.time()
        old_days = current.get("expected_delivery_days")
        _apply_request_eta(
            conn, request_id, expected_delivery_days,
            actor_max_id=actor_max_id, actor_name=actor_name, now=now,
        )
        result = _get_request(conn, request_id)
        _audit(conn, actor_max_id, actor_name, "request_eta_update", "request", request_id,
               {"from_days": old_days, "to_days": result.get("expected_delivery_days")})
        _cache(conn, idempotency_key, "request_eta_update", result)
        conn.commit()
        return result


def list_eta_reminders_due() -> list[dict]:
    now = time.time()
    with _connect() as conn:
        ids = [
            int(row["id"])
            for row in conn.execute(
                """SELECT id FROM sm_requests
                   WHERE status IN ('in_transit','partially_received')
                     AND expected_delivery_at IS NOT NULL
                     AND expected_delivery_at > ?
                     AND expected_delivery_at <= ?
                     AND eta_reminder_sent_at IS NULL""",
                (now, now + 86400),
            )
        ]
        return [_get_request(conn, request_id) for request_id in ids]


def mark_eta_reminder_sent(request_id: int) -> None:
    now = time.time()
    with _connect() as conn:
        conn.execute(
            "UPDATE sm_requests SET eta_reminder_sent_at=? WHERE id=?",
            (now, int(request_id)),
        )
        conn.commit()


def create_delivery(*, request_id: int, items: list[dict], supplier_id: int | None,
                    actor_max_id: int | None, actor_name: str, note: str = "",
                    expected_delivery_days: int | None = None,
                    idempotency_key: str | None = None) -> dict:
    if not items:
        raise ValueError("Добавьте позиции заказа")
    if expected_delivery_days is None:
        raise ValueError("Укажите срок поставки в днях")
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cached = _cached(conn, idempotency_key, "delivery_create")
        if cached:
            return cached
        request_doc = _get_request(conn, request_id)
        if request_doc["status"] not in {"accepted", "in_transit", "partially_received"}:
            raise ValueError("Заказ у поставщика недоступен в текущем статусе заявки")
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
        _apply_request_eta(
            conn, request_id, expected_delivery_days,
            actor_max_id=actor_max_id, actor_name=actor_name, now=now,
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
        raise ValueError("Заказ не найден")
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


def _is_early_delivery(request_doc: dict, *, now: float | None = None) -> bool:
    at = request_doc.get("expected_delivery_at")
    if at is None:
        return False
    try:
        eta_at = float(at)
    except (TypeError, ValueError):
        return False
    now = now or time.time()
    return now + 86400 < eta_at


def receive_delivery(*, delivery_id: int, items: list[dict] | None,
                     actor_max_id: int | None, actor_name: str, note: str = "",
                     confirm_early: bool = False,
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
                raise ValueError("Принятое количество превышает заказ")
            parsed.append((source, qty))
        request_doc = _get_request(conn, delivery["request_id"])
        if _is_early_delivery(request_doc) and not confirm_early:
            eta_label = request_doc.get("eta_date_label") or request_doc.get("eta_display_line") or "позже срока"
            raise ValueError(
                f"Поставка раньше срока (ожидали ~{eta_label}). "
                "Подтвердите приём на базу или проверьте отправку."
            )
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
        now = time.time()
        if all_received:
            conn.execute(
                """UPDATE sm_requests SET status=?, updated_at=?, received_at=?,
                   eta_reminder_sent_at=? WHERE id=?""",
                (request_status, now, now, now, delivery["request_id"]),
            )
        else:
            conn.execute(
                "UPDATE sm_requests SET status=?, updated_at=? WHERE id=?",
                (request_status, now, delivery["request_id"]),
            )
        result = _get_delivery(conn, delivery_id)
        result["request"] = _get_request(conn, delivery["request_id"])
        result["received_now"] = received_now
        audit_details: dict[str, Any] = {"items": received_now, "note": note.strip()}
        if _is_early_delivery(request_doc, now=now):
            audit_details["early_delivery"] = True
            if request_doc.get("expected_delivery_at"):
                audit_details["expected_delivery_at"] = request_doc["expected_delivery_at"]
        _audit(conn, actor_max_id, actor_name, "delivery_receive", "delivery", delivery_id,
               audit_details)
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
                           ("manager", "MATERIALS_MANAGER_MAX_IDS"),
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
    return {"roles": roles,
            "role": ("admin" if "admin" in roles else
                     "manager" if "manager" in roles else
                     roles[0] if roles else None),
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


def list_roles(*, include_inactive: bool = False) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """SELECT id, max_id, role, active, created_at, updated_at
               FROM sm_roles ORDER BY active DESC, role, max_id"""
        ).fetchall()
        result: list[dict] = []
        for row in rows:
            if not include_inactive and not int(row["active"]):
                continue
            site_ids = [
                int(item["site_id"])
                for item in conn.execute(
                    "SELECT site_id FROM sm_role_sites WHERE role_id=? ORDER BY site_id",
                    (int(row["id"]),),
                ).fetchall()
            ]
            result.append({
                "id": int(row["id"]),
                "max_id": int(row["max_id"]),
                "role": str(row["role"]),
                "active": bool(row["active"]),
                "site_ids": site_ids,
                "created_at": float(row["created_at"] or 0),
                "updated_at": float(row["updated_at"] or 0),
            })
    return result


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


ACCESS_REQUEST_ROLES = frozenset({"master", "supply", "manager"})


def _access_request_row(row) -> dict:
    notify: dict = {}
    raw = row["notify_messages_json"] if row["notify_messages_json"] else "{}"
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            notify = parsed
    except json.JSONDecodeError:
        notify = {}
    return {
        "id": int(row["id"]),
        "max_id": int(row["max_id"]),
        "display_name": str(row["display_name"] or ""),
        "status": str(row["status"] or ""),
        "assigned_role": row["assigned_role"],
        "created_at": float(row["created_at"]),
        "resolved_at": row["resolved_at"],
        "resolved_by_max_id": row["resolved_by_max_id"],
        "resolved_by_name": str(row["resolved_by_name"] or ""),
        "notify_messages": notify,
    }


def get_access_request_status(max_id: int) -> dict:
    if get_access(max_id)["roles"]:
        return {"status": "granted"}
    with _connect() as conn:
        row = conn.execute(
            """SELECT * FROM sm_access_requests
               WHERE max_id=?
               ORDER BY id DESC LIMIT 1""",
            (int(max_id),),
        ).fetchone()
    if not row:
        return {"status": "none"}
    item = _access_request_row(row)
    status = str(item.get("status") or "")
    if status == "pending":
        return {
            "status": "pending",
            "request_id": item["id"],
            "display_name": item["display_name"],
        }
    if status == "rejected":
        return {"status": "rejected"}
    if status == "approved":
        return {
            "status": "approved",
            "assigned_role": item.get("assigned_role"),
        }
    return {"status": "none"}


def submit_access_request(*, max_id: int, display_name: str) -> tuple[bool, str, dict]:
    if get_access(max_id)["roles"]:
        return False, "У вас уже есть доступ к Склад Мастер", {}
    status = get_access_request_status(max_id)
    if status.get("status") == "pending":
        return False, "Заявка уже отправлена — ждите решения администратора", status
    if status.get("status") == "rejected":
        return False, "Доступ отклонён администратором", status
    now = time.time()
    name = (display_name or "Пользователь MAX").strip()[:120]
    with _connect() as conn:
        try:
            cur = conn.execute(
                """INSERT INTO sm_access_requests
                   (max_id, display_name, status, created_at, notify_messages_json)
                   VALUES (?,?,?,?,?)""",
                (int(max_id), name, "pending", now, "{}"),
            )
        except sqlite3.IntegrityError:
            status = get_access_request_status(max_id)
            return False, "Заявка уже отправлена — ждите решения администратора", status
        request_id = int(cur.lastrowid)
        row = conn.execute(
            "SELECT * FROM sm_access_requests WHERE id=?", (request_id,)
        ).fetchone()
    return True, "Заявка отправлена администратору", _access_request_row(row)


def save_access_request_notify_messages(request_id: int, messages: dict[int, str]) -> None:
    payload = {str(k): str(v) for k, v in messages.items() if v}
    with _connect() as conn:
        conn.execute(
            "UPDATE sm_access_requests SET notify_messages_json=? WHERE id=?",
            (json.dumps(payload, ensure_ascii=False), int(request_id)),
        )


def get_pending_access_request(max_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            """SELECT * FROM sm_access_requests
               WHERE max_id=? AND status='pending'
               ORDER BY id DESC LIMIT 1""",
            (int(max_id),),
        ).fetchone()
    return _access_request_row(row) if row else None


def resolve_access_request(
    *,
    max_id: int,
    action: str,
    actor_max_id: int,
    actor_name: str,
) -> tuple[bool, str, dict | None]:
    action = (action or "").strip().lower()
    if action not in ACCESS_REQUEST_ROLES | {"reject"}:
        return False, "Неизвестное действие", None
    pending = get_pending_access_request(max_id)
    if not pending:
        return False, "already_processed", None
    now = time.time()
    actor = (actor_name or "Администратор").strip()[:120]
    if action == "reject":
        with _connect() as conn:
            conn.execute(
                """UPDATE sm_access_requests
                   SET status='rejected', resolved_at=?, resolved_by_max_id=?,
                       resolved_by_name=?, assigned_role=NULL
                   WHERE id=? AND status='pending'""",
                (now, int(actor_max_id), actor, int(pending["id"])),
            )
        pending["status"] = "rejected"
        pending["resolved_at"] = now
        pending["resolved_by_max_id"] = int(actor_max_id)
        pending["resolved_by_name"] = actor
        return True, "Отклонено", pending
    set_role(
        max_id=int(max_id),
        role=action,
        site_ids=None,
        active=True,
        actor_max_id=int(actor_max_id),
        actor_name=actor,
    )
    with _connect() as conn:
        conn.execute(
            """UPDATE sm_access_requests
               SET status='approved', assigned_role=?, resolved_at=?,
                   resolved_by_max_id=?, resolved_by_name=?
               WHERE id=? AND status='pending'""",
            (action, now, int(actor_max_id), actor, int(pending["id"])),
        )
    pending["status"] = "approved"
    pending["assigned_role"] = action
    pending["resolved_at"] = now
    pending["resolved_by_max_id"] = int(actor_max_id)
    pending["resolved_by_name"] = actor
    return True, "approved", pending


def dashboard(*, site_id: int | None = None) -> dict:
    sites, materials, suppliers = list_sites(), list_materials(), list_suppliers()
    default_site_id = _default_site_id(sites)
    selected = site_id or default_site_id
    stock = site_stock(selected) if selected else []
    summaries = []
    for site in sites:
        items = site_stock(site["id"])
        summaries.append({"site_id": site["id"], "site_name": site["name"],
                          "material_count": len(items),
                          "critical_count": sum(x["status"] == "critical" for x in items),
                          "warning_count": sum(x["status"] == "warning" for x in items)})
    return {"sites": sites, "materials": materials, "suppliers": suppliers,
            "selected_site_id": selected, "default_site_id": default_site_id,
            "default_site_name": "Грузовой", "stock": stock, "site_summaries": summaries,
            "requests": list_requests(site_id=selected or None, limit=20),
            "summary": {"site_count": len(sites), "material_count": len(materials),
                        "critical_count": sum(x["status"] == "critical" for x in stock),
                        "warning_count": sum(x["status"] == "warning" for x in stock)}}
