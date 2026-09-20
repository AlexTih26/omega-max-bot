"""Постоянный реестр отгрузок РУМЕКС.

Хранилище намеренно отделено от действующей JSON-очереди диспетчерской.
Подключение текущей панели к этому реестру будет выполнено отдельным этапом,
после проверки бизнес-переходов и миграции данных.
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "rumex_registry.db"
FLEET_REGISTRY_PATH = Path(__file__).resolve().parent.parent / "data" / "drivers_registry.json"
SCHEMA_VERSION = 8

PRODUCT_NAME = "Блок 9,7/8,8"
BLOCK_CODES = ("A", "B", "C", "D", "E", "F", "K")
TEST_SHIPMENT_BLOCK_COUNTS = (3, 4, 5, 6)
DEFAULT_PROFILE_CODE = "rumex-8102-v1"
CONFIRMATION_SOURCES = (
    "counterparty_card",
    "legacy_tn",
    "kontur",
)

# Значения перенесены из предварительно согласованных фотографий. До их
# подтверждения ответственным сотрудником они помечены как предварительные.
DEFAULT_BLOCK_TYPES = (
    ("A", 7780),
    ("B", 7900),
    ("C", 8080),
    ("D", 8080),
    ("E", 7900),
    ("F", 7780),
    ("K", 3850),
)

SHIPMENT_STATUSES = (
    "awaiting_er",
    "er_sent",
    "er_confirmed",
    "tn_ready",
    "issued",
    "departed",
    "cancelled",
)
DOCUMENT_STATUSES = (
    "draft",
    "waiting_er_confirmation",
    "sent_to_kontur",
    "confirmed",
    "ready",
    "issued",
    "cancelled",
)


def _connect() -> sqlite3.Connection:
    """Открыть соединение с настройками, обязательными для каждого запроса."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _now() -> float:
    return time.time()


def _timezone() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv("RUMEX_REGISTRY_TIMEZONE", "Asia/Irkutsk"))
    except Exception:
        return ZoneInfo("Asia/Irkutsk")


def _current_registry_year() -> int:
    return datetime.now(_timezone()).year


def _check_year(year: int) -> int:
    try:
        parsed = int(year)
    except (TypeError, ValueError):
        raise ValueError("Год номера должен быть числом") from None
    if not 2000 <= parsed <= 9999:
        raise ValueError("Год номера должен быть в диапазоне от 2000 до 9999")
    return parsed


def _json(value: Mapping[str, Any] | None = None) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _seed_reference_data(conn: sqlite3.Connection, *, now: float) -> None:
    """Добавить исходные справочники, не перезаписывая отредактированные данные."""
    organizations = (
        (
            "ООО «Омега-М»",
            "143405, Московская область, г.о. Красногорск,\nг. Красногорск, ул. Почтовая, д. 3",
            "5406829253",
            "502401001",
            "1235400005301",
            "Грузоотправитель и грузополучатель утверждённой ТТН РУМЕКС",
        ),
        (
            "ООО «РУМЕКС»",
            "364030, Чеченская Республика, г.о. город Грозный,\nг. Грозный, р-н Байсангуровский, ул. Сайханова, двлд. 222",
            "9728126848",
            "201001001",
            "1247700186029",
            "Заказчик услуг по организации перевозки утверждённой ТТН РУМЕКС",
        ),
    )
    for name, legal_address, inn, kpp, ogrn, note in organizations:
        conn.execute(
            """INSERT INTO organizations (
                   name, legal_address, inn, kpp, ogrn, note, active, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                   legal_address = excluded.legal_address,
                   inn = excluded.inn,
                   kpp = excluded.kpp,
                   ogrn = excluded.ogrn,
                   note = excluded.note,
                   active = 1,
                   updated_at = excluded.updated_at""",
            (name, legal_address, inn, kpp, ogrn, note, now, now),
        )

    org_ids = {
        str(row["name"]): int(row["id"])
        for row in conn.execute(
            "SELECT id, name FROM organizations WHERE name IN (?, ?)",
            ("ООО «Омега-М»", "ООО «РУМЕКС»"),
        )
    }
    omega_id = org_ids["ООО «Омега-М»"]
    rumex_id = org_ids["ООО «РУМЕКС»"]
    conn.execute(
            """INSERT INTO document_profiles (
                code, title, template_reference, sender_organization_id,
                organizer_organization_id, recipient_organization_id,
                pickup_location, delivery_location, delivery_station_code, active, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                title = excluded.title,
                template_reference = excluded.template_reference,
                sender_organization_id = excluded.sender_organization_id,
                organizer_organization_id = excluded.organizer_organization_id,
                recipient_organization_id = excluded.recipient_organization_id,
                pickup_location = excluded.pickup_location,
                delivery_location = excluded.delivery_location,
                delivery_station_code = excluded.delivery_station_code,
                active = 1,
                updated_at = excluded.updated_at""",
            (
                DEFAULT_PROFILE_CODE,
                "Транспортная накладная РУМЕКС, утверждённый бланк",
                "fotonych-bot/rumex_templates/ТТН РУМЕКС — утверждённый шаблон.xlsx",
                omega_id,
                rumex_id,
                omega_id,
                "Завод по производству тоннельной обделки на восточном\n"
                "портале Северомуйского тоннеля пгт. Северомуйск, расположенный "
                "Республика Бурятия, Муйский р-н, ГП «Северомуйское», пгт. Северомуйск",
                "ж/д станция Таксимо",
                "90440",
                now,
                now,
        ),
    )

    for code, weight_kg in DEFAULT_BLOCK_TYPES:
        conn.execute(
            """INSERT INTO block_types (
                   code, product_name, nominal_weight_kg, weight_confirmed,
                   source_note, active, created_at, updated_at
               ) VALUES (?, ?, ?, 0, ?, 1, ?, ?)
               ON CONFLICT(code) DO NOTHING""",
            (
                code,
                PRODUCT_NAME,
                weight_kg,
                "Предварительное значение по фотографиям; требуется подтверждение.",
                now,
                now,
            ),
        )


def init_rumex_registry_db() -> None:
    """Создать или безопасно открыть отдельную SQLite-базу реестра РУМЕКС."""
    now = _now()
    with _connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS organizations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                legal_address TEXT NOT NULL DEFAULT '',
                inn TEXT NOT NULL DEFAULT '',
                kpp TEXT NOT NULL DEFAULT '',
                ogrn TEXT NOT NULL DEFAULT '',
                contact_name TEXT NOT NULL DEFAULT '',
                contact_phone TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS document_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                template_reference TEXT NOT NULL DEFAULT '',
                sender_organization_id INTEGER NOT NULL,
                organizer_organization_id INTEGER NOT NULL,
                recipient_organization_id INTEGER NOT NULL,
                pickup_location TEXT NOT NULL DEFAULT '',
                pickup_station_code TEXT NOT NULL DEFAULT '',
                delivery_location TEXT NOT NULL DEFAULT '',
                delivery_station_code TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
                valid_from REAL,
                valid_to REAL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY (sender_organization_id) REFERENCES organizations(id) ON DELETE RESTRICT,
                FOREIGN KEY (organizer_organization_id) REFERENCES organizations(id) ON DELETE RESTRICT,
                FOREIGN KEY (recipient_organization_id) REFERENCES organizations(id) ON DELETE RESTRICT
            );

            CREATE TABLE IF NOT EXISTS carriers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                legal_address TEXT NOT NULL DEFAULT '',
                inn TEXT NOT NULL DEFAULT '',
                kpp TEXT NOT NULL DEFAULT '',
                ogrn TEXT NOT NULL DEFAULT '',
                confirmation_source TEXT NOT NULL DEFAULT '',
                confirmation_reference TEXT NOT NULL DEFAULT '',
                contact_name TEXT NOT NULL DEFAULT '',
                contact_phone TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS drivers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                phone TEXT NOT NULL DEFAULT '',
                max_user_id INTEGER,
                carrier_id INTEGER,
                active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE (full_name, phone),
                FOREIGN KEY (carrier_id) REFERENCES carriers(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS vehicles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plate TEXT NOT NULL,
                plate_normalized TEXT NOT NULL UNIQUE,
                model TEXT NOT NULL DEFAULT '',
                carrier_id INTEGER,
                active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                FOREIGN KEY (carrier_id) REFERENCES carriers(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS driver_vehicle_assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                driver_id INTEGER NOT NULL,
                vehicle_id INTEGER NOT NULL,
                valid_from REAL NOT NULL,
                valid_to REAL,
                created_at REAL NOT NULL,
                UNIQUE (driver_id, vehicle_id, valid_from),
                FOREIGN KEY (driver_id) REFERENCES drivers(id) ON DELETE RESTRICT,
                FOREIGN KEY (vehicle_id) REFERENCES vehicles(id) ON DELETE RESTRICT
            );

            -- Документная проекция источника физического парка. Она читается
            -- напрямую из drivers_registry.json и никогда не пишет обратно в
            -- него, в admin_fleet или в контур Таксимо.
            CREATE TABLE IF NOT EXISTS document_fleet_vehicles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plate_tail TEXT NOT NULL UNIQUE CHECK (length(trim(plate_tail)) > 0),
                full_plate TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL DEFAULT '',
                source_driver_name TEXT NOT NULL DEFAULT '',
                source_max_user_id INTEGER,
                source_active INTEGER NOT NULL DEFAULT 0 CHECK (source_active IN (0, 1)),
                source_present INTEGER NOT NULL DEFAULT 1 CHECK (source_present IN (0, 1)),
                source_payload_json TEXT NOT NULL DEFAULT '{}',
                source_imported_at REAL NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_rumex_document_fleet_available
                ON document_fleet_vehicles(source_present, source_active, plate_tail);

            -- Каждое подтверждение бухгалтером создаёт новую неизменяемую
            -- ревизию. Поэтому прежние реквизиты и водитель сохраняются для
            -- истории уже оформленных документов.
            CREATE TABLE IF NOT EXISTS document_vehicle_bindings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_vehicle_id INTEGER NOT NULL,
                carrier_id INTEGER NOT NULL,
                status TEXT NOT NULL CHECK (status = 'confirmed'),
                plate_tail_snapshot TEXT NOT NULL,
                full_plate_snapshot TEXT NOT NULL DEFAULT '',
                model_snapshot TEXT NOT NULL DEFAULT '',
                driver_full_name TEXT NOT NULL,
                driver_license_number TEXT NOT NULL DEFAULT '',
                vehicle_snapshot_json TEXT NOT NULL DEFAULT '{}',
                carrier_snapshot_json TEXT NOT NULL DEFAULT '{}',
                checked_at REAL NOT NULL,
                confirmed_by TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                FOREIGN KEY (document_vehicle_id) REFERENCES document_fleet_vehicles(id) ON DELETE RESTRICT,
                FOREIGN KEY (carrier_id) REFERENCES carriers(id) ON DELETE RESTRICT
            );
            CREATE INDEX IF NOT EXISTS idx_rumex_document_vehicle_bindings_current
                ON document_vehicle_bindings(document_vehicle_id, id DESC);

            CREATE TRIGGER IF NOT EXISTS document_vehicle_bindings_no_update
            BEFORE UPDATE ON document_vehicle_bindings
            BEGIN
                SELECT RAISE(ABORT, 'Подтверждённые документные связи нельзя изменять');
            END;

            CREATE TRIGGER IF NOT EXISTS document_vehicle_bindings_no_delete
            BEFORE DELETE ON document_vehicle_bindings
            BEGIN
                SELECT RAISE(ABORT, 'Подтверждённые документные связи нельзя удалять');
            END;

            -- Отдельный тестовый поток документов. Он не связан внешними
            -- ключами с боевой очередью, выездами, вагонами или Таксимо.
            CREATE TABLE IF NOT EXISTS test_shipments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                registry_number TEXT NOT NULL UNIQUE,
                registry_year INTEGER NOT NULL CHECK (registry_year BETWEEN 2000 AND 9999),
                registry_sequence INTEGER NOT NULL CHECK (registry_sequence > 0),
                status TEXT NOT NULL CHECK (status IN (
                    'awaiting_accountant_review', 'requires_correction',
                    'awaiting_er_sent', 'documents_ready', 'documents_handed_to_driver'
                )),
                revision_number INTEGER NOT NULL DEFAULT 1 CHECK (revision_number > 0),
                document_vehicle_binding_id INTEGER NOT NULL,
                document_snapshot_json TEXT NOT NULL DEFAULT '{}',
                dispatcher_max_user_id INTEGER NOT NULL,
                dispatcher_name TEXT NOT NULL,
                loaded_at REAL NOT NULL,
                accountant_name TEXT NOT NULL DEFAULT '',
                accountant_reviewed_at REAL,
                er_required INTEGER NOT NULL DEFAULT 1 CHECK (er_required IN (0, 1)),
                kontur_reference TEXT NOT NULL DEFAULT '',
                kontur_sent_at REAL,
                documents_ready_at REAL,
                ttn_number TEXT NOT NULL DEFAULT '',
                ttn_year INTEGER,
                ttn_sequence INTEGER,
                ttn_assigned_at REAL,
                ttn_printed_at REAL,
                ttn_printed_by_name TEXT NOT NULL DEFAULT '',
                printed_by_max_user_id INTEGER,
                printed_by_name TEXT NOT NULL DEFAULT '',
                printed_confirmed_at REAL,
                correction_reason TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE (registry_year, registry_sequence),
                FOREIGN KEY (document_vehicle_binding_id) REFERENCES document_vehicle_bindings(id) ON DELETE RESTRICT
            );
            CREATE INDEX IF NOT EXISTS idx_rumex_test_shipments_status_created
                ON test_shipments(status, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_rumex_test_shipments_tail
                ON test_shipments(document_vehicle_binding_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS test_shipment_revisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                test_shipment_id INTEGER NOT NULL,
                revision_number INTEGER NOT NULL CHECK (revision_number > 0),
                revision_kind TEXT NOT NULL CHECK (revision_kind IN (
                    'submitted', 'returned_for_correction', 'resubmitted'
                )),
                payload_json TEXT NOT NULL DEFAULT '{}',
                actor_kind TEXT NOT NULL,
                actor_id TEXT NOT NULL DEFAULT '',
                actor_name TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                UNIQUE (test_shipment_id, revision_number, revision_kind),
                FOREIGN KEY (test_shipment_id) REFERENCES test_shipments(id) ON DELETE RESTRICT
            );
            CREATE INDEX IF NOT EXISTS idx_rumex_test_revisions_shipment
                ON test_shipment_revisions(test_shipment_id, revision_number, id);

            CREATE TABLE IF NOT EXISTS test_shipment_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                test_shipment_id INTEGER NOT NULL,
                revision_number INTEGER NOT NULL CHECK (revision_number > 0),
                sort_order INTEGER NOT NULL CHECK (sort_order > 0),
                block_type_code TEXT NOT NULL,
                block_number TEXT NOT NULL CHECK (length(trim(block_number)) > 0),
                product_name TEXT NOT NULL,
                weight_kg INTEGER NOT NULL CHECK (weight_kg > 0),
                created_at REAL NOT NULL,
                UNIQUE (test_shipment_id, revision_number, sort_order),
                UNIQUE (test_shipment_id, revision_number, block_type_code, block_number),
                FOREIGN KEY (test_shipment_id) REFERENCES test_shipments(id) ON DELETE RESTRICT,
                FOREIGN KEY (block_type_code) REFERENCES block_types(code) ON DELETE RESTRICT
            );
            CREATE INDEX IF NOT EXISTS idx_rumex_test_items_block
                ON test_shipment_items(block_type_code, block_number, created_at DESC);

            CREATE TRIGGER IF NOT EXISTS test_shipment_revisions_no_update
            BEFORE UPDATE ON test_shipment_revisions
            BEGIN
                SELECT RAISE(ABORT, 'Ревизии тестовой погрузки нельзя изменять');
            END;
            CREATE TRIGGER IF NOT EXISTS test_shipment_revisions_no_delete
            BEFORE DELETE ON test_shipment_revisions
            BEGIN
                SELECT RAISE(ABORT, 'Ревизии тестовой погрузки нельзя удалять');
            END;
            CREATE TRIGGER IF NOT EXISTS test_shipment_items_no_update
            BEFORE UPDATE ON test_shipment_items
            BEGIN
                SELECT RAISE(ABORT, 'Блоки тестовой погрузки нельзя изменять');
            END;
            CREATE TRIGGER IF NOT EXISTS test_shipment_items_no_delete
            BEFORE DELETE ON test_shipment_items
            BEGIN
                SELECT RAISE(ABORT, 'Блоки тестовой погрузки нельзя удалять');
            END;

            CREATE TABLE IF NOT EXISTS test_shipment_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                test_shipment_id INTEGER NOT NULL,
                document_kind TEXT NOT NULL CHECK (document_kind IN ('ER', 'TN')),
                registry_number TEXT NOT NULL,
                display_suffix TEXT NOT NULL CHECK (display_suffix IN ('ЭР', 'ТТН')),
                status TEXT NOT NULL CHECK (status IN (
                    'waiting_review', 'draft', 'not_required', 'sent_to_kontur', 'ready', 'issued'
                )),
                external_reference TEXT NOT NULL DEFAULT '',
                render_version TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE (test_shipment_id, document_kind),
                FOREIGN KEY (test_shipment_id) REFERENCES test_shipments(id) ON DELETE RESTRICT
            );
            CREATE INDEX IF NOT EXISTS idx_rumex_test_documents_number
                ON test_shipment_documents(registry_number, document_kind);

            CREATE TABLE IF NOT EXISTS test_ttn_number_sequences (
                ttn_year INTEGER PRIMARY KEY CHECK (ttn_year BETWEEN 2000 AND 9999),
                last_sequence INTEGER NOT NULL CHECK (last_sequence >= 0),
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS rumex_test_shipment_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                test_shipment_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                actor_kind TEXT NOT NULL DEFAULT '',
                actor_id TEXT NOT NULL DEFAULT '',
                actor_name TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                occurred_at REAL NOT NULL,
                FOREIGN KEY (test_shipment_id) REFERENCES test_shipments(id) ON DELETE RESTRICT
            );
            CREATE INDEX IF NOT EXISTS idx_rumex_test_events_shipment_at
                ON rumex_test_shipment_events(test_shipment_id, occurred_at, id);
            CREATE TRIGGER IF NOT EXISTS rumex_test_shipment_events_no_update
            BEFORE UPDATE ON rumex_test_shipment_events
            BEGIN
                SELECT RAISE(ABORT, 'События тестовой погрузки нельзя изменять');
            END;
            CREATE TRIGGER IF NOT EXISTS rumex_test_shipment_events_no_delete
            BEFORE DELETE ON rumex_test_shipment_events
            BEGIN
                SELECT RAISE(ABORT, 'События тестовой погрузки нельзя удалять');
            END;

            CREATE TABLE IF NOT EXISTS block_types (
                code TEXT PRIMARY KEY CHECK (code IN ('A', 'B', 'C', 'D', 'E', 'F', 'K')),
                product_name TEXT NOT NULL DEFAULT 'Блок 9,7/8,8',
                nominal_weight_kg INTEGER NOT NULL CHECK (nominal_weight_kg > 0),
                weight_confirmed INTEGER NOT NULL DEFAULT 0 CHECK (weight_confirmed IN (0, 1)),
                source_note TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS registry_number_sequences (
                registry_year INTEGER PRIMARY KEY CHECK (registry_year BETWEEN 2000 AND 9999),
                last_sequence INTEGER NOT NULL CHECK (last_sequence >= 0),
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS shipments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                registry_number TEXT NOT NULL UNIQUE,
                registry_year INTEGER NOT NULL CHECK (registry_year BETWEEN 2000 AND 9999),
                registry_sequence INTEGER NOT NULL CHECK (registry_sequence > 0),
                status TEXT NOT NULL CHECK (status IN (
                    'awaiting_er', 'er_sent', 'er_confirmed', 'tn_ready', 'issued', 'departed', 'cancelled'
                )),
                document_profile_id INTEGER NOT NULL,
                profile_snapshot_json TEXT NOT NULL DEFAULT '{}',
                carrier_id INTEGER,
                driver_id INTEGER,
                vehicle_id INTEGER,
                carrier_snapshot_json TEXT NOT NULL DEFAULT '{}',
                driver_snapshot_json TEXT NOT NULL DEFAULT '{}',
                vehicle_snapshot_json TEXT NOT NULL DEFAULT '{}',
                dispatcher_max_user_id INTEGER,
                dispatcher_name TEXT NOT NULL DEFAULT '',
                loaded_at REAL,
                er_confirmed_at REAL,
                documents_issued_at REAL,
                departed_at REAL,
                issued_copy_count INTEGER NOT NULL DEFAULT 0 CHECK (issued_copy_count BETWEEN 0 AND 4),
                note TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE (registry_year, registry_sequence),
                FOREIGN KEY (document_profile_id) REFERENCES document_profiles(id) ON DELETE RESTRICT,
                FOREIGN KEY (carrier_id) REFERENCES carriers(id) ON DELETE RESTRICT,
                FOREIGN KEY (driver_id) REFERENCES drivers(id) ON DELETE RESTRICT,
                FOREIGN KEY (vehicle_id) REFERENCES vehicles(id) ON DELETE RESTRICT
            );
            CREATE INDEX IF NOT EXISTS idx_rumex_shipments_status_created
                ON shipments(status, created_at DESC);

            CREATE TABLE IF NOT EXISTS shipment_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shipment_id INTEGER NOT NULL,
                sort_order INTEGER NOT NULL CHECK (sort_order > 0),
                block_type_code TEXT NOT NULL,
                block_number TEXT NOT NULL CHECK (length(trim(block_number)) > 0),
                product_name TEXT NOT NULL,
                weight_kg INTEGER NOT NULL CHECK (weight_kg > 0),
                created_at REAL NOT NULL,
                UNIQUE (shipment_id, sort_order),
                UNIQUE (shipment_id, block_type_code, block_number),
                FOREIGN KEY (shipment_id) REFERENCES shipments(id) ON DELETE RESTRICT,
                FOREIGN KEY (block_type_code) REFERENCES block_types(code) ON DELETE RESTRICT
            );
            CREATE INDEX IF NOT EXISTS idx_rumex_shipment_items_block
                ON shipment_items(block_type_code, block_number);

            CREATE TABLE IF NOT EXISTS shipment_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shipment_id INTEGER NOT NULL,
                document_kind TEXT NOT NULL CHECK (document_kind IN ('ER', 'TN')),
                registry_number TEXT NOT NULL,
                display_suffix TEXT NOT NULL CHECK (display_suffix IN ('ЭР', 'ТТН')),
                status TEXT NOT NULL CHECK (status IN (
                    'draft', 'waiting_er_confirmation', 'sent_to_kontur',
                    'confirmed', 'ready', 'issued', 'cancelled'
                )),
                external_reference TEXT NOT NULL DEFAULT '',
                file_path TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                UNIQUE (shipment_id, document_kind),
                FOREIGN KEY (shipment_id) REFERENCES shipments(id) ON DELETE RESTRICT
            );
            CREATE INDEX IF NOT EXISTS idx_rumex_documents_number
                ON shipment_documents(registry_number, document_kind);

            CREATE TABLE IF NOT EXISTS rumex_audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shipment_id INTEGER,
                event_type TEXT NOT NULL,
                actor_kind TEXT NOT NULL DEFAULT '',
                actor_id TEXT NOT NULL DEFAULT '',
                actor_name TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                occurred_at REAL NOT NULL,
                FOREIGN KEY (shipment_id) REFERENCES shipments(id) ON DELETE RESTRICT
            );
            CREATE INDEX IF NOT EXISTS idx_rumex_audit_shipment_at
                ON rumex_audit_events(shipment_id, occurred_at, id);

            CREATE TRIGGER IF NOT EXISTS rumex_audit_events_no_update
            BEFORE UPDATE ON rumex_audit_events
            BEGIN
                SELECT RAISE(ABORT, 'События аудита РУМЕКС нельзя изменять');
            END;

            CREATE TRIGGER IF NOT EXISTS rumex_audit_events_no_delete
            BEFORE DELETE ON rumex_audit_events
            BEGIN
                SELECT RAISE(ABORT, 'События аудита РУМЕКС нельзя удалять');
            END;

            CREATE TABLE IF NOT EXISTS accountant_login_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL DEFAULT '',
                success INTEGER NOT NULL CHECK (success IN (0, 1)),
                failure_reason TEXT NOT NULL DEFAULT '',
                remote_address TEXT NOT NULL DEFAULT '',
                user_agent TEXT NOT NULL DEFAULT '',
                attempted_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_rumex_accountant_login_attempts
                ON accountant_login_attempts(username, attempted_at DESC);

            CREATE TABLE IF NOT EXISTS accountant_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_hash TEXT NOT NULL UNIQUE,
                username TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                last_seen_at REAL NOT NULL,
                revoked_at REAL,
                remote_address TEXT NOT NULL DEFAULT '',
                user_agent TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_rumex_accountant_sessions_expiry
                ON accountant_sessions(expires_at, revoked_at);

            CREATE TABLE IF NOT EXISTS test_dispatcher_login_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL DEFAULT '',
                success INTEGER NOT NULL CHECK (success IN (0, 1)),
                failure_reason TEXT NOT NULL DEFAULT '',
                remote_address TEXT NOT NULL DEFAULT '',
                user_agent TEXT NOT NULL DEFAULT '',
                attempted_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_rumex_test_dispatcher_login_attempts
                ON test_dispatcher_login_attempts(username, attempted_at DESC);

            CREATE TABLE IF NOT EXISTS test_dispatcher_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_hash TEXT NOT NULL UNIQUE,
                username TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                last_seen_at REAL NOT NULL,
                revoked_at REAL,
                remote_address TEXT NOT NULL DEFAULT '',
                user_agent TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_rumex_test_dispatcher_sessions_expiry
                ON test_dispatcher_sessions(expires_at, revoked_at);

            CREATE TABLE IF NOT EXISTS rumex_dispatcher_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                full_name TEXT NOT NULL,
                max_user_id INTEGER,
                password_hash TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                last_password_issued_at REAL,
                last_activity_at REAL
            );
            CREATE INDEX IF NOT EXISTS idx_rumex_dispatcher_accounts_active
                ON rumex_dispatcher_accounts(active, username);

            CREATE TABLE IF NOT EXISTS rumex_admin_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                full_name TEXT NOT NULL,
                max_user_id INTEGER NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'admin' CHECK (role IN ('admin')),
                active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS rumex_admin_login_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL DEFAULT '',
                max_user_id INTEGER,
                success INTEGER NOT NULL CHECK (success IN (0, 1)),
                failure_reason TEXT NOT NULL DEFAULT '',
                remote_address TEXT NOT NULL DEFAULT '',
                user_agent TEXT NOT NULL DEFAULT '',
                attempted_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_rumex_admin_login_attempts
                ON rumex_admin_login_attempts(max_user_id, attempted_at DESC);
            CREATE TABLE IF NOT EXISTS rumex_admin_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_hash TEXT NOT NULL UNIQUE,
                username TEXT NOT NULL,
                max_user_id INTEGER NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                last_seen_at REAL NOT NULL,
                revoked_at REAL,
                remote_address TEXT NOT NULL DEFAULT '',
                user_agent TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_rumex_admin_sessions_expiry
                ON rumex_admin_sessions(expires_at, revoked_at);

            CREATE TABLE IF NOT EXISTS rumex_vehicle_shipment_blocks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plate_tail TEXT NOT NULL UNIQUE,
                reason TEXT NOT NULL,
                blocked_by TEXT NOT NULL,
                blocked_at REAL NOT NULL,
                unblocked_by TEXT NOT NULL DEFAULT '',
                unblocked_at REAL
            );
            CREATE TABLE IF NOT EXISTS rumex_management_audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                actor_name TEXT NOT NULL,
                subject_type TEXT NOT NULL DEFAULT '',
                subject_id TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                occurred_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_rumex_management_audit_events
                ON rumex_management_audit_events(occurred_at DESC, id DESC);
            """
        )
        _add_column_if_missing(
            conn,
            "carriers",
            "confirmation_source TEXT NOT NULL DEFAULT ''",
        )
        _add_column_if_missing(
            conn,
            "carriers",
            "confirmation_reference TEXT NOT NULL DEFAULT ''",
        )
        _add_column_if_missing(
            conn,
            "test_shipments",
            "dispatcher_identity_kind TEXT NOT NULL DEFAULT 'max'",
        )
        _add_column_if_missing(
            conn,
            "test_shipments",
            "dispatcher_identity_id TEXT NOT NULL DEFAULT ''",
        )
        _add_column_if_missing(
            conn,
            "test_shipments",
            "printed_by_identity_kind TEXT NOT NULL DEFAULT ''",
        )
        _add_column_if_missing(
            conn,
            "test_shipments",
            "printed_by_identity_id TEXT NOT NULL DEFAULT ''",
        )
        _add_column_if_missing(
            conn,
            "document_vehicle_bindings",
            "driver_license_number TEXT NOT NULL DEFAULT ''",
        )
        _add_column_if_missing(conn, "test_shipments", "ttn_number TEXT NOT NULL DEFAULT ''")
        _add_column_if_missing(conn, "test_shipments", "ttn_year INTEGER")
        _add_column_if_missing(conn, "test_shipments", "ttn_sequence INTEGER")
        _add_column_if_missing(conn, "test_shipments", "ttn_assigned_at REAL")
        _add_column_if_missing(conn, "test_shipments", "ttn_printed_at REAL")
        _add_column_if_missing(
            conn,
            "test_shipments",
            "ttn_printed_by_name TEXT NOT NULL DEFAULT ''",
        )
        conn.execute(
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_rumex_test_shipments_ttn_number
               ON test_shipments(ttn_number) WHERE ttn_number <> ''"""
        )
        conn.executescript(
            """
            CREATE TRIGGER IF NOT EXISTS test_shipments_ttn_number_no_change
            BEFORE UPDATE OF ttn_number, ttn_year, ttn_sequence, ttn_assigned_at ON test_shipments
            WHEN OLD.ttn_number <> ''
            BEGIN
                SELECT RAISE(ABORT, 'Выданный номер ТТН нельзя изменять');
            END;

            CREATE TRIGGER IF NOT EXISTS test_shipments_issued_ttn_no_delete
            BEFORE DELETE ON test_shipments
            WHEN OLD.ttn_number <> ''
            BEGIN
                SELECT RAISE(ABORT, 'Выданную ТТН нельзя удалить');
            END;
            """
        )
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        _seed_reference_data(conn, now=now)


def _add_column_if_missing(conn: sqlite3.Connection, table: str, definition: str) -> None:
    """Добавить поле в уже созданную локальную БД без пересоздания таблицы."""
    column = definition.split(maxsplit=1)[0]
    columns = {
        str(row["name"])
        for row in conn.execute(f"PRAGMA table_info({table})")
    }
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")


def list_block_types(*, active_only: bool = False) -> list[dict[str, Any]]:
    """Вернуть редактируемый справочник типов блоков с весом в кг и тоннах."""
    init_rumex_registry_db()
    where = "WHERE active = 1" if active_only else ""
    with _connect() as conn:
        rows = conn.execute(
            f"""SELECT code, product_name, nominal_weight_kg, weight_confirmed,
                       source_note, active, created_at, updated_at
                FROM block_types {where} ORDER BY code"""
        ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["weight_confirmed"] = bool(item["weight_confirmed"])
        item["active"] = bool(item["active"])
        item["nominal_weight_tonnes"] = item["nominal_weight_kg"] / 1000
        result.append(item)
    return result


def _carrier_from_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["active"] = bool(result["active"])
    return result


def list_carriers(*, active_only: bool = False) -> list[dict[str, Any]]:
    """Вернуть перевозчиков из ручного справочника без каких-либо догадок."""
    init_rumex_registry_db()
    where = "WHERE active = 1" if active_only else ""
    with _connect() as conn:
        rows = conn.execute(
            f"""SELECT id, name, legal_address, inn, kpp, ogrn,
                       confirmation_source, confirmation_reference,
                       contact_name, contact_phone, note, active, created_at, updated_at
                FROM carriers {where}
                ORDER BY active DESC, name COLLATE NOCASE, id"""
        ).fetchall()
    return [_carrier_from_row(row) for row in rows]


def _required_carrier_text(value: Any, label: str, *, max_length: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"Укажите {label}")
    if len(text) > max_length:
        raise ValueError(f"Поле «{label}» не должно быть длиннее {max_length} символов")
    return text


def _optional_carrier_text(value: Any, label: str, *, max_length: int) -> str:
    text = str(value or "").strip()
    if len(text) > max_length:
        raise ValueError(f"Поле «{label}» не должно быть длиннее {max_length} символов")
    return text


def _carrier_payload(
    *,
    name: Any,
    inn: Any,
    kpp: Any,
    legal_address: Any,
    confirmation_source: Any,
    confirmation_reference: Any,
    ogrn: Any,
    contact_name: Any,
    contact_phone: Any,
    note: Any,
    active: bool,
) -> dict[str, Any]:
    """Проверить поля, которые бухгалтер переносит из подтверждённого источника."""
    normalized_inn = _required_carrier_text(inn, "ИНН", max_length=12)
    if not re.fullmatch(r"\d{10}|\d{12}", normalized_inn):
        raise ValueError("ИНН должен состоять из 10 или 12 цифр")
    normalized_kpp = _required_carrier_text(kpp, "КПП", max_length=9)
    if not re.fullmatch(r"\d{9}", normalized_kpp):
        raise ValueError("КПП должен состоять из 9 цифр")
    source = str(confirmation_source or "").strip()
    if source not in CONFIRMATION_SOURCES:
        raise ValueError("Выберите подтверждённый источник реквизитов")
    return {
        "name": _required_carrier_text(name, "официальное наименование", max_length=300),
        "inn": normalized_inn,
        "kpp": normalized_kpp,
        "legal_address": _required_carrier_text(legal_address, "юридический адрес", max_length=1000),
        "confirmation_source": source,
        "confirmation_reference": _required_carrier_text(
            confirmation_reference,
            "реквизит подтверждающего источника",
            max_length=500,
        ),
        "ogrn": _optional_carrier_text(ogrn, "ОГРН", max_length=15),
        "contact_name": _optional_carrier_text(contact_name, "контактное лицо", max_length=200),
        "contact_phone": _optional_carrier_text(contact_phone, "телефон", max_length=80),
        "note": _optional_carrier_text(note, "примечание", max_length=1000),
        "active": bool(active),
    }


def save_carrier(
    *,
    name: Any,
    inn: Any,
    kpp: Any,
    legal_address: Any,
    confirmation_source: Any,
    confirmation_reference: Any,
    actor_name: str,
    carrier_id: int | None = None,
    ogrn: Any = "",
    contact_name: Any = "",
    contact_phone: Any = "",
    note: Any = "",
    active: bool = True,
    occurred_at: float | None = None,
) -> dict[str, Any]:
    """Создать или изменить вручную подтверждённую карточку перевозчика.

    Данные никогда не формируются по машине, водителю или их названию: все
    обязательные юридические поля и источник их подтверждения приходят только
    из формы бухгалтера.
    """
    payload = _carrier_payload(
        name=name,
        inn=inn,
        kpp=kpp,
        legal_address=legal_address,
        confirmation_source=confirmation_source,
        confirmation_reference=confirmation_reference,
        ogrn=ogrn,
        contact_name=contact_name,
        contact_phone=contact_phone,
        note=note,
        active=active,
    )
    actor = (actor_name or "").strip()
    if not actor:
        raise ValueError("Не определён администратор справочника")
    if carrier_id is not None:
        try:
            carrier_id = int(carrier_id)
        except (TypeError, ValueError):
            raise ValueError("Некорректный номер перевозчика") from None
        if carrier_id <= 0:
            raise ValueError("Некорректный номер перевозчика")

    init_rumex_registry_db()
    now = float(occurred_at) if occurred_at is not None else _now()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            duplicate = conn.execute(
                """SELECT id FROM carriers
                   WHERE (name = ? OR inn = ?) AND (? IS NULL OR id != ?)""",
                (payload["name"], payload["inn"], carrier_id, carrier_id),
            ).fetchone()
            if duplicate is not None:
                raise ValueError("Перевозчик с таким наименованием или ИНН уже есть в справочнике")

            if carrier_id is None:
                cursor = conn.execute(
                    """INSERT INTO carriers (
                           name, legal_address, inn, kpp, ogrn,
                           confirmation_source, confirmation_reference,
                           contact_name, contact_phone, note, active, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        payload["name"],
                        payload["legal_address"],
                        payload["inn"],
                        payload["kpp"],
                        payload["ogrn"],
                        payload["confirmation_source"],
                        payload["confirmation_reference"],
                        payload["contact_name"],
                        payload["contact_phone"],
                        payload["note"],
                        int(payload["active"]),
                        now,
                        now,
                    ),
                )
                saved_id = int(cursor.lastrowid)
                _append_audit_event(
                    conn,
                    shipment_id=None,
                    event_type="carrier_created",
                    actor_kind="registry_admin",
                    actor_id=actor,
                    actor_name=actor,
                    payload={"carrier_id": saved_id, "carrier": payload},
                    occurred_at=now,
                )
            else:
                before_row = conn.execute("SELECT * FROM carriers WHERE id = ?", (carrier_id,)).fetchone()
                if before_row is None:
                    raise ValueError("Перевозчик не найден")
                before = _carrier_from_row(before_row)
                changed = {
                    field: {"before": before[field], "after": value}
                    for field, value in payload.items()
                    if before[field] != value
                }
                saved_id = carrier_id
                if changed:
                    conn.execute(
                        """UPDATE carriers SET
                               name = ?, legal_address = ?, inn = ?, kpp = ?, ogrn = ?,
                               confirmation_source = ?, confirmation_reference = ?,
                               contact_name = ?, contact_phone = ?, note = ?, active = ?, updated_at = ?
                           WHERE id = ?""",
                        (
                            payload["name"],
                            payload["legal_address"],
                            payload["inn"],
                            payload["kpp"],
                            payload["ogrn"],
                            payload["confirmation_source"],
                            payload["confirmation_reference"],
                            payload["contact_name"],
                            payload["contact_phone"],
                            payload["note"],
                            int(payload["active"]),
                            now,
                            saved_id,
                        ),
                    )
                    _append_audit_event(
                        conn,
                        shipment_id=None,
                        event_type="carrier_updated",
                        actor_kind="registry_admin",
                        actor_id=actor,
                        actor_name=actor,
                        payload={"carrier_id": saved_id, "changes": changed},
                        occurred_at=now,
                    )
            row = conn.execute("SELECT * FROM carriers WHERE id = ?", (saved_id,)).fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    if row is None:  # Защита от повреждённой БД; штатно недостижимо.
        raise RuntimeError("Не удалось прочитать сохранённого перевозчика")
    return _carrier_from_row(row)


def save_block_type(
    code: str,
    *,
    nominal_weight_kg: int,
    product_name: str = PRODUCT_NAME,
    weight_confirmed: bool = False,
    source_note: str = "",
    active: bool = True,
) -> dict[str, Any]:
    """Создать или обновить тип блока в справочнике.

    Используется только для утверждения или уточнения нормативного веса. Уже
    созданные позиции отгрузок сохраняют собственный снимок веса.
    """
    normalized_code = (code or "").strip().upper()
    if normalized_code not in BLOCK_CODES:
        raise ValueError("Недопустимая буква блока")
    try:
        weight = int(nominal_weight_kg)
    except (TypeError, ValueError):
        raise ValueError("Вес блока укажите целым числом килограммов") from None
    if weight <= 0:
        raise ValueError("Вес блока должен быть больше нуля")
    name = (product_name or "").strip()
    if not name:
        raise ValueError("Укажите наименование ТМЦ")

    init_rumex_registry_db()
    now = _now()
    with _connect() as conn:
        conn.execute(
            """INSERT INTO block_types (
                   code, product_name, nominal_weight_kg, weight_confirmed,
                   source_note, active, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(code) DO UPDATE SET
                   product_name = excluded.product_name,
                   nominal_weight_kg = excluded.nominal_weight_kg,
                   weight_confirmed = excluded.weight_confirmed,
                   source_note = excluded.source_note,
                   active = excluded.active,
                   updated_at = excluded.updated_at""",
            (
                normalized_code,
                name,
                weight,
                int(bool(weight_confirmed)),
                (source_note or "").strip(),
                int(bool(active)),
                now,
                now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM block_types WHERE code = ?", (normalized_code,)
        ).fetchone()
    result = dict(row)
    result["weight_confirmed"] = bool(result["weight_confirmed"])
    result["active"] = bool(result["active"])
    result["nominal_weight_tonnes"] = result["nominal_weight_kg"] / 1000
    return result


def _profile_snapshot(conn: sqlite3.Connection, profile_id: int) -> dict[str, Any]:
    row = conn.execute(
        """SELECT p.id, p.code, p.title, p.template_reference,
                  p.pickup_location, p.pickup_station_code,
                  p.delivery_location, p.delivery_station_code,
                  sender.name AS sender_name, sender.legal_address AS sender_legal_address,
                  sender.inn AS sender_inn, sender.kpp AS sender_kpp,
                  organizer.name AS organizer_name,
                  organizer.legal_address AS organizer_legal_address,
                  organizer.inn AS organizer_inn, organizer.kpp AS organizer_kpp,
                  recipient.name AS recipient_name,
                  recipient.legal_address AS recipient_legal_address,
                  recipient.inn AS recipient_inn, recipient.kpp AS recipient_kpp
           FROM document_profiles AS p
           JOIN organizations AS sender ON sender.id = p.sender_organization_id
           JOIN organizations AS organizer ON organizer.id = p.organizer_organization_id
           JOIN organizations AS recipient ON recipient.id = p.recipient_organization_id
           WHERE p.id = ? AND p.active = 1""",
        (profile_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Профиль транспортной накладной не найден или отключён")
    return dict(row)


def _default_profile_id(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT id FROM document_profiles WHERE code = ? AND active = 1",
        (DEFAULT_PROFILE_CODE,),
    ).fetchone()
    if row is None:
        raise ValueError("Не найден профиль транспортной накладной РУМЕКС")
    return int(row["id"])


def _validate_catalog_reference(
    conn: sqlite3.Connection, table: str, value: int | None, label: str
) -> None:
    if value is None:
        return
    row = conn.execute(f"SELECT id FROM {table} WHERE id = ? AND active = 1", (value,)).fetchone()
    if row is None:
        raise ValueError(f"{label} не найден или отключён в справочнике")


def _catalog_snapshot(
    conn: sqlite3.Connection, table: str, value: int | None
) -> dict[str, Any]:
    if value is None:
        return {}
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (value,)).fetchone()
    if row is None:  # Ссылка была проверена непосредственно перед этим вызовом.
        raise ValueError("Справочная запись не найдена")
    return dict(row)


def _reserve_number_in_transaction(
    conn: sqlite3.Connection, *, registry_year: int, now: float
) -> tuple[int, str]:
    """Выдать следующий номер внутри уже начатой короткой IMMEDIATE-транзакции."""
    row = conn.execute(
        "SELECT last_sequence FROM registry_number_sequences WHERE registry_year = ?",
        (registry_year,),
    ).fetchone()
    if row is None:
        sequence = 1
        conn.execute(
            """INSERT INTO registry_number_sequences (registry_year, last_sequence, updated_at)
               VALUES (?, ?, ?)""",
            (registry_year, sequence, now),
        )
    else:
        sequence = int(row["last_sequence"]) + 1
        conn.execute(
            """UPDATE registry_number_sequences
               SET last_sequence = ?, updated_at = ?
               WHERE registry_year = ?""",
            (sequence, now, registry_year),
        )
    return sequence, f"РМ-{registry_year}-{sequence:06d}"


def _test_ttn_year_for_loaded_at(loaded_at: float) -> int:
    """Вернуть календарный год фактической погрузки в рабочем часовом поясе."""
    return datetime.fromtimestamp(loaded_at, _timezone()).year


def _reserve_test_ttn_number_in_transaction(
    conn: sqlite3.Connection, *, ttn_year: int, now: float
) -> tuple[int, str]:
    """Выдать необратимый номер утверждённой ТТН в текущей IMMEDIATE-транзакции."""
    row = conn.execute(
        "SELECT last_sequence FROM test_ttn_number_sequences WHERE ttn_year = ?",
        (ttn_year,),
    ).fetchone()
    if row is None:
        sequence = 1
        conn.execute(
            """INSERT INTO test_ttn_number_sequences (ttn_year, last_sequence, updated_at)
               VALUES (?, ?, ?)""",
            (ttn_year, sequence, now),
        )
    else:
        sequence = int(row["last_sequence"]) + 1
        conn.execute(
            """UPDATE test_ttn_number_sequences
               SET last_sequence = ?, updated_at = ?
               WHERE ttn_year = ?""",
            (sequence, now, ttn_year),
        )
    return sequence, f"ТТН №РМ-{ttn_year}-{sequence:06d}"


def _normalized_items(
    conn: sqlite3.Connection, items: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for raw in items:
        code = str(raw.get("block_type_code") or raw.get("letter") or "").strip().upper()
        number = str(raw.get("block_number") or raw.get("number") or "").strip()
        if not code or not number:
            raise ValueError("Для каждого блока укажите букву и номер изделия")
        if (code, number) in seen:
            raise ValueError(f"Блок {code}{number} добавлен дважды")
        block_type = conn.execute(
            """SELECT code, product_name, nominal_weight_kg
               FROM block_types WHERE code = ? AND active = 1""",
            (code,),
        ).fetchone()
        if block_type is None:
            raise ValueError(f"Тип блока {code} не найден или отключён")
        seen.add((code, number))
        normalized.append(
            {
                "block_type_code": code,
                "block_number": number,
                "product_name": str(block_type["product_name"]),
                "weight_kg": int(block_type["nominal_weight_kg"]),
            }
        )
    if not normalized:
        raise ValueError("Добавьте хотя бы один блок")
    return normalized


def _append_audit_event(
    conn: sqlite3.Connection,
    *,
    shipment_id: int | None,
    event_type: str,
    actor_kind: str,
    actor_id: str,
    actor_name: str,
    payload: Mapping[str, Any] | None,
    occurred_at: float,
) -> None:
    conn.execute(
        """INSERT INTO rumex_audit_events (
               shipment_id, event_type, actor_kind, actor_id, actor_name, payload_json, occurred_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            shipment_id,
            event_type,
            actor_kind,
            actor_id,
            actor_name,
            _json(payload),
            occurred_at,
        ),
    )


def _normalize_plate_tail(value: Any) -> str:
    tail = str(value or "").strip()
    if not re.fullmatch(r"\d{1,12}", tail):
        raise ValueError("Хвост машины должен состоять из цифр")
    return tail


def _source_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "да"}


def _source_max_user_id(value: Any) -> int | None:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _load_document_fleet_source(source_path: Path) -> list[dict[str, Any]]:
    """Прочитать только документный снимок физического парка.

    Функция намеренно не импортирует ``admin_fleet``: тот модуль синхронизирует
    Таксимо, а здесь допускается исключительно read-only чтение JSON-файла.
    """
    try:
        raw = json.loads(source_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError("Исходный реестр машин супер-админа не найден") from None
    except (OSError, json.JSONDecodeError):
        raise ValueError("Не удалось прочитать исходный реестр машин супер-админа") from None
    if not isinstance(raw, Mapping) or not isinstance(raw.get("drivers"), list):
        raise ValueError("Исходный реестр машин супер-админа имеет неверный формат")

    vehicles: list[dict[str, Any]] = []
    seen_tails: set[str] = set()
    for item in raw["drivers"]:
        if not isinstance(item, Mapping):
            continue
        tail = _normalize_plate_tail(item.get("plate_tail"))
        if tail in seen_tails:
            raise ValueError(f"В исходном реестре повторяется хвост машины {tail}")
        seen_tails.add(tail)
        snapshot = {
            "plate_tail": tail,
            "full_plate": str(item.get("taksimo_plate") or "").strip()[:100],
            "model": str(item.get("vehicle") or "").strip()[:300],
            "source_driver_name": str(item.get("name") or "").strip()[:200],
            "source_max_user_id": _source_max_user_id(item.get("max_user_id")),
            "source_active": _source_bool(item.get("active")),
        }
        vehicles.append(snapshot)
    if not vehicles:
        raise ValueError("В исходном реестре машин нет доступных записей")
    return vehicles


def _binding_from_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["carrier_snapshot"] = json.loads(result.pop("carrier_snapshot_json"))
    result["vehicle_snapshot"] = json.loads(result.pop("vehicle_snapshot_json"))
    return result


def _document_vehicle_from_row(
    row: sqlite3.Row, binding: sqlite3.Row | None = None
) -> dict[str, Any]:
    result = dict(row)
    result["source_active"] = bool(result["source_active"])
    result["source_present"] = bool(result["source_present"])
    result["source_payload"] = json.loads(result.pop("source_payload_json"))
    result["document_binding"] = _binding_from_row(binding) if binding is not None else None
    return result


def _current_document_vehicle_binding(
    conn: sqlite3.Connection, document_vehicle_id: int
) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT * FROM document_vehicle_bindings
           WHERE document_vehicle_id = ? ORDER BY id DESC LIMIT 1""",
        (document_vehicle_id,),
    ).fetchone()


def import_document_fleet_snapshot(
    *,
    accountant_name: str,
    source_path: Path | None = None,
    occurred_at: float | None = None,
) -> list[dict[str, Any]]:
    """Снять одностороннюю документную копию машин супер-админа.

    Импорт обновляет лишь текущую проекцию в ``rumex_registry.db``. Исходный
    JSON, физический парк и связанные контуры не изменяются.
    """
    actor = (accountant_name or "").strip()
    if not actor:
        raise ValueError("Не определён бухгалтер")
    path = Path(source_path) if source_path is not None else FLEET_REGISTRY_PATH
    vehicles = _load_document_fleet_source(path)
    now = float(occurred_at) if occurred_at is not None else _now()

    init_rumex_registry_db()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                """UPDATE document_fleet_vehicles
                   SET source_present = 0, updated_at = ?
                   WHERE source_present = 1""",
                (now,),
            )
            for vehicle in vehicles:
                source_payload = {
                    "plate_tail": vehicle["plate_tail"],
                    "full_plate": vehicle["full_plate"],
                    "model": vehicle["model"],
                    "source_driver_name": vehicle["source_driver_name"],
                    "source_max_user_id": vehicle["source_max_user_id"],
                    "source_active": vehicle["source_active"],
                }
                conn.execute(
                    """INSERT INTO document_fleet_vehicles (
                           plate_tail, full_plate, model, source_driver_name,
                           source_max_user_id, source_active, source_present,
                           source_payload_json, source_imported_at, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
                       ON CONFLICT(plate_tail) DO UPDATE SET
                           full_plate = excluded.full_plate,
                           model = excluded.model,
                           source_driver_name = excluded.source_driver_name,
                           source_max_user_id = excluded.source_max_user_id,
                           source_active = excluded.source_active,
                           source_present = 1,
                           source_payload_json = excluded.source_payload_json,
                           source_imported_at = excluded.source_imported_at,
                           updated_at = excluded.updated_at""",
                    (
                        vehicle["plate_tail"],
                        vehicle["full_plate"],
                        vehicle["model"],
                        vehicle["source_driver_name"],
                        vehicle["source_max_user_id"],
                        int(vehicle["source_active"]),
                        _json(source_payload),
                        now,
                        now,
                        now,
                    ),
                )
            _append_audit_event(
                conn,
                shipment_id=None,
                event_type="document_fleet_imported",
                actor_kind="registry_admin",
                actor_id=actor,
                actor_name=actor,
                payload={
                    "source": str(path),
                    "vehicle_count": len(vehicles),
                    "plate_tails": [vehicle["plate_tail"] for vehicle in vehicles],
                },
                occurred_at=now,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return list_document_fleet_vehicles()


def list_document_fleet_vehicles(*, include_missing: bool = False) -> list[dict[str, Any]]:
    """Вернуть текущую документную проекцию парка с последней связью бухгалтера."""
    init_rumex_registry_db()
    where = "" if include_missing else "WHERE source_present = 1"
    with _connect() as conn:
        rows = conn.execute(
            f"""SELECT * FROM document_fleet_vehicles {where}
                ORDER BY source_active DESC, plate_tail"""
        ).fetchall()
        vehicles = [
            _document_vehicle_from_row(
                row,
                _current_document_vehicle_binding(conn, int(row["id"])),
            )
            for row in rows
        ]
        for vehicle in vehicles:
            block = conn.execute(
                "SELECT * FROM rumex_vehicle_shipment_blocks WHERE plate_tail = ? AND unblocked_at IS NULL",
                (vehicle["plate_tail"],),
            ).fetchone()
            vehicle["shipment_block"] = dict(block) if block is not None else None
        return vehicles


def get_document_fleet_vehicle(plate_tail: Any) -> dict[str, Any] | None:
    """Найти машину по хвосту, не обращаясь к рабочей диспетчерской."""
    tail = _normalize_plate_tail(plate_tail)
    init_rumex_registry_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM document_fleet_vehicles WHERE plate_tail = ?", (tail,)
        ).fetchone()
        if row is None:
            return None
        return _document_vehicle_from_row(
            row,
            _current_document_vehicle_binding(conn, int(row["id"])),
        )


def list_document_vehicle_binding_history(plate_tail: Any) -> list[dict[str, Any]]:
    """Вернуть все неизменяемые подтверждения документной связи машины."""
    tail = _normalize_plate_tail(plate_tail)
    init_rumex_registry_db()
    with _connect() as conn:
        rows = conn.execute(
            """SELECT binding.* FROM document_vehicle_bindings AS binding
               JOIN document_fleet_vehicles AS vehicle ON vehicle.id = binding.document_vehicle_id
               WHERE vehicle.plate_tail = ? ORDER BY binding.id DESC""",
            (tail,),
        ).fetchall()
    return [_binding_from_row(row) for row in rows]


def confirm_document_vehicle_binding(
    *,
    plate_tail: Any,
    carrier_id: int,
    driver_full_name: Any,
    driver_license_number: Any,
    accountant_name: str,
    note: Any = "",
    checked_at: float | None = None,
) -> dict[str, Any]:
    """Зафиксировать новую подтверждённую бухгалтером документную связь.

    Поля физической машины здесь не принимаются: полный номер и модель всегда
    берутся из read-only проекции супер-админа в момент подтверждения.
    """
    tail = _normalize_plate_tail(plate_tail)
    actor = (accountant_name or "").strip()
    if not actor:
        raise ValueError("Не определён бухгалтер")
    driver = _required_carrier_text(driver_full_name, "ФИО водителя", max_length=200)
    driver_license = _required_carrier_text(
        driver_license_number, "номер водительского удостоверения", max_length=100
    )
    binding_note = _optional_carrier_text(note, "примечание", max_length=1000)
    try:
        parsed_carrier_id = int(carrier_id)
    except (TypeError, ValueError):
        raise ValueError("Некорректный номер перевозчика") from None
    if parsed_carrier_id <= 0:
        raise ValueError("Некорректный номер перевозчика")

    init_rumex_registry_db()
    now = float(checked_at) if checked_at is not None else _now()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            vehicle = conn.execute(
                "SELECT * FROM document_fleet_vehicles WHERE plate_tail = ?", (tail,)
            ).fetchone()
            if vehicle is None or not bool(vehicle["source_present"]):
                raise ValueError("Машина не импортирована из физического парка супер-админа")
            if not bool(vehicle["source_active"]):
                raise ValueError("Машина отключена в физическом парке супер-админа")
            carrier = conn.execute(
                "SELECT * FROM carriers WHERE id = ? AND active = 1", (parsed_carrier_id,)
            ).fetchone()
            if carrier is None:
                raise ValueError("Перевозчик не найден или отключён в справочнике")

            vehicle_snapshot = {
                "plate_tail": str(vehicle["plate_tail"]),
                "full_plate": str(vehicle["full_plate"]),
                "model": str(vehicle["model"]),
                "source_driver_name": str(vehicle["source_driver_name"]),
                "source_max_user_id": vehicle["source_max_user_id"],
            }
            carrier_snapshot = _carrier_from_row(carrier)
            cursor = conn.execute(
                """INSERT INTO document_vehicle_bindings (
                       document_vehicle_id, carrier_id, status, plate_tail_snapshot,
                       full_plate_snapshot, model_snapshot, driver_full_name, driver_license_number,
                       vehicle_snapshot_json, carrier_snapshot_json, checked_at,
                       confirmed_by, note, created_at
                   ) VALUES (?, ?, 'confirmed', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    int(vehicle["id"]),
                    parsed_carrier_id,
                    tail,
                    vehicle_snapshot["full_plate"],
                    vehicle_snapshot["model"],
                    driver,
                    driver_license,
                    _json(vehicle_snapshot),
                    _json(carrier_snapshot),
                    now,
                    actor,
                    binding_note,
                    now,
                ),
            )
            binding_id = int(cursor.lastrowid)
            _append_audit_event(
                conn,
                shipment_id=None,
                event_type="document_vehicle_binding_confirmed",
                actor_kind="registry_admin",
                actor_id=actor,
                actor_name=actor,
                payload={
                    "binding_id": binding_id,
                    "plate_tail": tail,
                    "carrier_id": parsed_carrier_id,
                    "driver_full_name": driver,
                    "driver_license_number": driver_license,
                },
                occurred_at=now,
            )
            binding = conn.execute(
                "SELECT * FROM document_vehicle_bindings WHERE id = ?", (binding_id,)
            ).fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    if binding is None:  # Защита от повреждённой БД; штатно недостижимо.
        raise RuntimeError("Не удалось прочитать подтверждённую документную связь")
    return _binding_from_row(binding)


def _test_shipment_timestamp(value: Any, label: str) -> float:
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} укажите датой и временем") from None
    if not math.isfinite(timestamp) or timestamp <= 0:
        raise ValueError(f"{label} укажите датой и временем")
    return timestamp


def _test_shipment_id(value: Any) -> int:
    try:
        shipment_id = int(value)
    except (TypeError, ValueError):
        raise ValueError("Некорректный номер тестовой погрузки") from None
    if shipment_id <= 0:
        raise ValueError("Некорректный номер тестовой погрузки")
    return shipment_id


def _append_test_shipment_event(
    conn: sqlite3.Connection,
    *,
    test_shipment_id: int,
    event_type: str,
    actor_kind: str,
    actor_id: str,
    actor_name: str,
    payload: Mapping[str, Any] | None,
    occurred_at: float,
) -> None:
    conn.execute(
        """INSERT INTO rumex_test_shipment_events (
               test_shipment_id, event_type, actor_kind, actor_id, actor_name,
               payload_json, occurred_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            test_shipment_id,
            event_type,
            actor_kind,
            actor_id,
            actor_name,
            _json(payload),
            occurred_at,
        ),
    )


def _test_document_snapshot(
    conn: sqlite3.Connection, binding: sqlite3.Row
) -> dict[str, Any]:
    profile = _profile_snapshot(conn, _default_profile_id(conn))
    vehicle = json.loads(str(binding["vehicle_snapshot_json"]))
    carrier = json.loads(str(binding["carrier_snapshot_json"]))
    return {
        "document_profile": profile,
        "vehicle": vehicle,
        "carrier": carrier,
        "driver": {
            "full_name": str(binding["driver_full_name"]),
            "license_number": str(binding["driver_license_number"]),
        },
        "document_vehicle_binding_id": int(binding["id"]),
        "document_binding_checked_at": float(binding["checked_at"]),
        "document_binding_confirmed_by": str(binding["confirmed_by"]),
    }


def _insert_test_shipment_items(
    conn: sqlite3.Connection,
    *,
    test_shipment_id: int,
    revision_number: int,
    items: list[dict[str, Any]],
    created_at: float,
) -> None:
    for sort_order, item in enumerate(items, start=1):
        conn.execute(
            """INSERT INTO test_shipment_items (
                   test_shipment_id, revision_number, sort_order, block_type_code,
                   block_number, product_name, weight_kg, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                test_shipment_id,
                revision_number,
                sort_order,
                item["block_type_code"],
                item["block_number"],
                item["product_name"],
                item["weight_kg"],
                created_at,
            ),
        )


def _test_revision_payload(*, loaded_at: float, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "loaded_at": loaded_at,
        "items": items,
        "total_weight_kg": sum(int(item["weight_kg"]) for item in items),
    }


def _append_test_revision(
    conn: sqlite3.Connection,
    *,
    test_shipment_id: int,
    revision_number: int,
    revision_kind: str,
    payload: Mapping[str, Any],
    actor_kind: str,
    actor_id: str,
    actor_name: str,
    created_at: float,
) -> None:
    conn.execute(
        """INSERT INTO test_shipment_revisions (
               test_shipment_id, revision_number, revision_kind, payload_json,
               actor_kind, actor_id, actor_name, created_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            test_shipment_id,
            revision_number,
            revision_kind,
            _json(payload),
            actor_kind,
            actor_id,
            actor_name,
            created_at,
        ),
    )


def _test_shipment_from_row(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    shipment_id = int(result["id"])
    revision_number = int(result["revision_number"])
    result["er_required"] = bool(result["er_required"])
    result["document_snapshot"] = json.loads(result.pop("document_snapshot_json"))
    result["items"] = [
        dict(item)
        for item in conn.execute(
            """SELECT * FROM test_shipment_items
               WHERE test_shipment_id = ? AND revision_number = ? ORDER BY sort_order""",
            (shipment_id, revision_number),
        )
    ]
    result["total_weight_kg"] = sum(int(item["weight_kg"]) for item in result["items"])
    result["documents"] = [
        dict(document)
        for document in conn.execute(
            """SELECT *, registry_number || ' · ' || display_suffix AS display_number
               FROM test_shipment_documents
               WHERE test_shipment_id = ? ORDER BY document_kind""",
            (shipment_id,),
        )
    ]
    result["revisions"] = []
    for revision in conn.execute(
        """SELECT * FROM test_shipment_revisions
           WHERE test_shipment_id = ? ORDER BY revision_number, id""",
        (shipment_id,),
    ):
        revision_data = dict(revision)
        revision_data["payload"] = json.loads(revision_data.pop("payload_json"))
        result["revisions"].append(revision_data)
    result["events"] = []
    for event in conn.execute(
        """SELECT * FROM rumex_test_shipment_events
           WHERE test_shipment_id = ? ORDER BY occurred_at, id""",
        (shipment_id,),
    ):
        event_data = dict(event)
        event_data["payload"] = json.loads(event_data.pop("payload_json"))
        result["events"].append(event_data)
    result["is_new_for_accountant"] = not any(
        str(event.get("actor_kind") or "").startswith("accountant")
        for event in result["events"]
    )
    return result


def get_test_shipment(test_shipment_id: Any) -> dict[str, Any] | None:
    """Вернуть тестовую погрузку, документы, ревизии и неизменяемую историю."""
    shipment_id = _test_shipment_id(test_shipment_id)
    init_rumex_registry_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM test_shipments WHERE id = ?", (shipment_id,)).fetchone()
        return _test_shipment_from_row(conn, row) if row is not None else None


def list_test_shipments(*, limit: int = 100) -> list[dict[str, Any]]:
    """Вернуть отдельный реестр тестовых погрузок, не касаясь рабочего рейса."""
    try:
        parsed_limit = int(limit)
    except (TypeError, ValueError):
        parsed_limit = 100
    parsed_limit = max(1, min(parsed_limit, 200))
    init_rumex_registry_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM test_shipments ORDER BY created_at DESC, id DESC LIMIT ?",
            (parsed_limit,),
        ).fetchall()
        return [_test_shipment_from_row(conn, row) for row in rows]


def _current_confirmed_binding_for_tail(
    conn: sqlite3.Connection, plate_tail: str
) -> sqlite3.Row:
    vehicle = conn.execute(
        """SELECT * FROM document_fleet_vehicles
           WHERE plate_tail = ? AND source_present = 1 AND source_active = 1""",
        (plate_tail,),
    ).fetchone()
    if vehicle is None:
        raise ValueError("Машина не найдена среди активных машин физического парка")
    block = conn.execute(
        "SELECT reason FROM rumex_vehicle_shipment_blocks WHERE plate_tail = ? AND unblocked_at IS NULL",
        (plate_tail,),
    ).fetchone()
    if block is not None:
        raise ValueError("Машина заблокирована для новых погрузок РУМЕКС: " + str(block["reason"]))
    binding = _current_document_vehicle_binding(conn, int(vehicle["id"]))
    if binding is None:
        raise ValueError("Для машины нет подтверждённой бухгалтером документной карточки")
    if not str(binding["driver_license_number"] or "").strip():
        raise ValueError(
            "В подтверждённой карточке машины нет номера водительского удостоверения. "
            "Бухгалтер должен подтвердить новую карточку машины."
        )
    return binding


def _test_dispatcher_actor(
    *,
    dispatcher_max_user_id: Any,
    dispatcher_name: Any,
    dispatcher_identity_kind: Any = "max",
    dispatcher_identity_id: Any = "",
) -> tuple[int, str, str, str]:
    """Нормализовать исполнителя из MAX или отдельной парольной сессии."""
    kind = str(dispatcher_identity_kind or "").strip().lower()
    dispatcher = _required_carrier_text(dispatcher_name, "имя диспетчера", max_length=200)
    if kind == "max":
        try:
            max_user_id = int(dispatcher_max_user_id)
        except (TypeError, ValueError):
            raise ValueError("Не определён диспетчер MAX") from None
        if max_user_id <= 0:
            raise ValueError("Не определён диспетчер MAX")
        identity_id = str(dispatcher_identity_id or max_user_id).strip()
        if identity_id != str(max_user_id):
            raise ValueError("Некорректная учётная запись диспетчера MAX")
        return max_user_id, kind, identity_id, dispatcher
    if kind == "password":
        identity_id = _required_carrier_text(
            dispatcher_identity_id, "учётную запись диспетчера", max_length=200
        )
        return 0, kind, identity_id, dispatcher
    raise ValueError("Неизвестный способ входа диспетчера")


def create_test_shipment(
    *,
    plate_tail: Any,
    block_count: Any,
    items: Iterable[Mapping[str, Any]],
    dispatcher_max_user_id: int,
    dispatcher_name: str,
    loaded_at: Any,
    dispatcher_identity_kind: str = "max",
    dispatcher_identity_id: str = "",
    registry_year: int | None = None,
    created_at: float | None = None,
) -> dict[str, Any]:
    """Зафиксировать тестовую погрузку после факта загрузки.

    Машина, водитель и реквизиты берутся только из последней подтверждённой
    бухгалтером карточки. Эта операция не вызывает никакие боевые функции.
    """
    tail = _normalize_plate_tail(plate_tail)
    try:
        expected_count = int(block_count)
    except (TypeError, ValueError):
        raise ValueError("Укажите число блоков") from None
    if expected_count not in TEST_SHIPMENT_BLOCK_COUNTS:
        raise ValueError("Для ТТН укажите от 3 до 6 блоков")
    dispatcher_id, dispatcher_kind, dispatcher_identity, dispatcher = _test_dispatcher_actor(
        dispatcher_max_user_id=dispatcher_max_user_id,
        dispatcher_name=dispatcher_name,
        dispatcher_identity_kind=dispatcher_identity_kind,
        dispatcher_identity_id=dispatcher_identity_id,
    )
    loaded_timestamp = _test_shipment_timestamp(loaded_at, "Фактическое время погрузки")
    now = float(created_at) if created_at is not None else _now()
    year = _check_year(registry_year if registry_year is not None else _current_registry_year())

    init_rumex_registry_db()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            binding = _current_confirmed_binding_for_tail(conn, tail)
            normalized_items = _normalized_items(conn, items)
            if len(normalized_items) != expected_count:
                raise ValueError("Число блоков не совпадает с количеством введённых блоков")
            document_snapshot = _test_document_snapshot(conn, binding)
            sequence, registry_number = _reserve_number_in_transaction(
                conn, registry_year=year, now=now
            )
            cursor = conn.execute(
                """INSERT INTO test_shipments (
                       registry_number, registry_year, registry_sequence, status, revision_number,
                       document_vehicle_binding_id, document_snapshot_json,
                       dispatcher_max_user_id, dispatcher_identity_kind, dispatcher_identity_id,
                       dispatcher_name, loaded_at, created_at, updated_at
                   ) VALUES (?, ?, ?, 'awaiting_accountant_review', 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    registry_number,
                    year,
                    sequence,
                    int(binding["id"]),
                    _json(document_snapshot),
                    dispatcher_id,
                    dispatcher_kind,
                    dispatcher_identity,
                    dispatcher,
                    loaded_timestamp,
                    now,
                    now,
                ),
            )
            shipment_id = int(cursor.lastrowid)
            _insert_test_shipment_items(
                conn,
                test_shipment_id=shipment_id,
                revision_number=1,
                items=normalized_items,
                created_at=now,
            )
            payload = _test_revision_payload(loaded_at=loaded_timestamp, items=normalized_items)
            _append_test_revision(
                conn,
                test_shipment_id=shipment_id,
                revision_number=1,
                revision_kind="submitted",
                payload=payload,
                actor_kind="dispatcher_" + dispatcher_kind,
                actor_id=dispatcher_identity,
                actor_name=dispatcher,
                created_at=now,
            )
            _append_test_shipment_event(
                conn,
                test_shipment_id=shipment_id,
                event_type="test_shipment_loaded",
                actor_kind="dispatcher_" + dispatcher_kind,
                actor_id=dispatcher_identity,
                actor_name=dispatcher,
                payload={
                    "registry_number": registry_number,
                    "registry_year": year,
                    "registry_sequence": sequence,
                    "plate_tail": tail,
                    "revision_number": 1,
                    "items_count": len(normalized_items),
                    "total_weight_kg": payload["total_weight_kg"],
                },
                occurred_at=now,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    shipment = get_test_shipment(shipment_id)
    if shipment is None:  # Защита от повреждённой БД; штатно недостижимо.
        raise RuntimeError("Не удалось прочитать созданную тестовую погрузку")
    return shipment


def _test_shipment_for_update(conn: sqlite3.Connection, shipment_id: int) -> sqlite3.Row:
    shipment = conn.execute("SELECT * FROM test_shipments WHERE id = ?", (shipment_id,)).fetchone()
    if shipment is None:
        raise ValueError("Тестовая погрузка не найдена")
    return shipment


def return_test_shipment_for_correction(
    test_shipment_id: Any,
    *,
    accountant_name: str,
    reason: Any,
    occurred_at: float | None = None,
) -> dict[str, Any]:
    """Вернуть погрузку диспетчеру, сохраняя уже поданную ревизию."""
    shipment_id = _test_shipment_id(test_shipment_id)
    actor = _required_carrier_text(accountant_name, "имя бухгалтера", max_length=200)
    correction_reason = _required_carrier_text(reason, "причину возврата", max_length=1000)
    now = float(occurred_at) if occurred_at is not None else _now()
    init_rumex_registry_db()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            shipment = _test_shipment_for_update(conn, shipment_id)
            if str(shipment["status"]) != "awaiting_accountant_review":
                raise ValueError("Вернуть на исправление можно только погрузку, ожидающую проверки")
            revision_number = int(shipment["revision_number"])
            conn.execute(
                """UPDATE test_shipments
                   SET status = 'requires_correction', correction_reason = ?, updated_at = ?
                   WHERE id = ?""",
                (correction_reason, now, shipment_id),
            )
            _append_test_revision(
                conn,
                test_shipment_id=shipment_id,
                revision_number=revision_number,
                revision_kind="returned_for_correction",
                payload={"reason": correction_reason},
                actor_kind="accountant",
                actor_id=actor,
                actor_name=actor,
                created_at=now,
            )
            _append_test_shipment_event(
                conn,
                test_shipment_id=shipment_id,
                event_type="test_shipment_returned_for_correction",
                actor_kind="accountant",
                actor_id=actor,
                actor_name=actor,
                payload={"reason": correction_reason, "revision_number": revision_number},
                occurred_at=now,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    result = get_test_shipment(shipment_id)
    if result is None:
        raise RuntimeError("Не удалось прочитать тестовую погрузку после возврата")
    return result


def resubmit_test_shipment(
    test_shipment_id: Any,
    *,
    block_count: Any,
    items: Iterable[Mapping[str, Any]],
    loaded_at: Any,
    dispatcher_max_user_id: int,
    dispatcher_name: str,
    dispatcher_identity_kind: str = "max",
    dispatcher_identity_id: str = "",
    occurred_at: float | None = None,
) -> dict[str, Any]:
    """Добавить новую ревизию возвращённой погрузки без изменения старой."""
    shipment_id = _test_shipment_id(test_shipment_id)
    try:
        expected_count = int(block_count)
    except (TypeError, ValueError):
        raise ValueError("Укажите число блоков") from None
    if expected_count not in TEST_SHIPMENT_BLOCK_COUNTS:
        raise ValueError("Для ТТН укажите от 3 до 6 блоков")
    dispatcher_id, dispatcher_kind, dispatcher_identity, dispatcher = _test_dispatcher_actor(
        dispatcher_max_user_id=dispatcher_max_user_id,
        dispatcher_name=dispatcher_name,
        dispatcher_identity_kind=dispatcher_identity_kind,
        dispatcher_identity_id=dispatcher_identity_id,
    )
    loaded_timestamp = _test_shipment_timestamp(loaded_at, "Фактическое время погрузки")
    now = float(occurred_at) if occurred_at is not None else _now()
    init_rumex_registry_db()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            shipment = _test_shipment_for_update(conn, shipment_id)
            if str(shipment["status"]) != "requires_correction":
                raise ValueError("Исправить можно только погрузку, возвращённую бухгалтером")
            normalized_items = _normalized_items(conn, items)
            if len(normalized_items) != expected_count:
                raise ValueError("Число блоков не совпадает с количеством введённых блоков")
            revision_number = int(shipment["revision_number"]) + 1
            payload = _test_revision_payload(loaded_at=loaded_timestamp, items=normalized_items)
            conn.execute(
                """UPDATE test_shipments SET
                       status = 'awaiting_accountant_review', revision_number = ?,
                       dispatcher_max_user_id = ?, dispatcher_identity_kind = ?,
                       dispatcher_identity_id = ?, dispatcher_name = ?, loaded_at = ?,
                       correction_reason = '', updated_at = ?
                    WHERE id = ?""",
                (
                    revision_number,
                    dispatcher_id,
                    dispatcher_kind,
                    dispatcher_identity,
                    dispatcher,
                    loaded_timestamp,
                    now,
                    shipment_id,
                ),
            )
            _insert_test_shipment_items(
                conn,
                test_shipment_id=shipment_id,
                revision_number=revision_number,
                items=normalized_items,
                created_at=now,
            )
            _append_test_revision(
                conn,
                test_shipment_id=shipment_id,
                revision_number=revision_number,
                revision_kind="resubmitted",
                payload=payload,
                actor_kind="dispatcher_" + dispatcher_kind,
                actor_id=dispatcher_identity,
                actor_name=dispatcher,
                created_at=now,
            )
            _append_test_shipment_event(
                conn,
                test_shipment_id=shipment_id,
                event_type="test_shipment_resubmitted",
                actor_kind="dispatcher_" + dispatcher_kind,
                actor_id=dispatcher_identity,
                actor_name=dispatcher,
                payload={
                    "revision_number": revision_number,
                    "items_count": len(normalized_items),
                    "total_weight_kg": payload["total_weight_kg"],
                    "loaded_at": loaded_timestamp,
                },
                occurred_at=now,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    result = get_test_shipment(shipment_id)
    if result is None:
        raise RuntimeError("Не удалось прочитать тестовую погрузку после исправления")
    return result


def review_test_shipment(
    test_shipment_id: Any,
    *,
    accountant_name: str,
    er_required: bool,
    occurred_at: float | None = None,
) -> dict[str, Any]:
    """Подтвердить погрузку бухгалтером и подготовить комплект документов."""
    shipment_id = _test_shipment_id(test_shipment_id)
    actor = _required_carrier_text(accountant_name, "имя бухгалтера", max_length=200)
    if not isinstance(er_required, bool):
        raise ValueError("Признак необходимости ЭР должен быть логическим")
    now = float(occurred_at) if occurred_at is not None else _now()
    init_rumex_registry_db()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            shipment = _test_shipment_for_update(conn, shipment_id)
            if str(shipment["status"]) != "awaiting_accountant_review":
                raise ValueError("Проверить можно только погрузку, ожидающую бухгалтера")
            if str(shipment["ttn_number"] or ""):
                raise ValueError("Для этой погрузки уже выдан неизменяемый номер ТТН")
            snapshot = json.loads(str(shipment["document_snapshot_json"]))
            license_number = str((snapshot.get("driver") or {}).get("license_number") or "").strip()
            if not license_number:
                raise ValueError(
                    "В снимке документа нет номера водительского удостоверения. "
                    "Подтвердите новую связь машины у Бухгалтера 1 и создайте новую погрузку."
                )
            items_count = int(
                conn.execute(
                    """SELECT COUNT(*) AS count FROM test_shipment_items
                       WHERE test_shipment_id = ? AND revision_number = ?""",
                    (shipment_id, int(shipment["revision_number"])),
                ).fetchone()["count"]
            )
            if items_count not in TEST_SHIPMENT_BLOCK_COUNTS:
                raise ValueError("Утверждённая ТТН доступна только для погрузки от 3 до 6 блоков")
            ttn_year = _test_ttn_year_for_loaded_at(float(shipment["loaded_at"]))
            ttn_sequence, ttn_number = _reserve_test_ttn_number_in_transaction(
                conn, ttn_year=ttn_year, now=now
            )
            next_status = "awaiting_er_sent" if er_required else "documents_ready"
            conn.execute(
                """UPDATE test_shipments SET
                       status = ?, accountant_name = ?, accountant_reviewed_at = ?,
                       er_required = ?, documents_ready_at = ?, ttn_number = ?, ttn_year = ?,
                       ttn_sequence = ?, ttn_assigned_at = ?, updated_at = ?
                    WHERE id = ?""",
                (
                    next_status,
                    actor,
                    now,
                    int(er_required),
                    None if er_required else now,
                    ttn_number,
                    ttn_year,
                    ttn_sequence,
                    now,
                    now,
                    shipment_id,
                ),
            )
            documents = (
                (shipment_id, "ER", str(shipment["registry_number"]), "ЭР", "draft" if er_required else "not_required", now, now),
                (shipment_id, "TN", ttn_number, "ТТН", "draft" if er_required else "ready", now, now),
            )
            conn.executemany(
                """INSERT INTO test_shipment_documents (
                       test_shipment_id, document_kind, registry_number, display_suffix,
                       status, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                documents,
            )
            _append_test_shipment_event(
                conn,
                test_shipment_id=shipment_id,
                event_type="test_shipment_reviewed",
                actor_kind="accountant",
                actor_id=actor,
                actor_name=actor,
                payload={
                    "er_required": er_required,
                    "status": next_status,
                    "ttn_number": ttn_number,
                    "ttn_year": ttn_year,
                    "ttn_sequence": ttn_sequence,
                },
                occurred_at=now,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    result = get_test_shipment(shipment_id)
    if result is None:
        raise RuntimeError("Не удалось прочитать тестовую погрузку после проверки")
    return result


def mark_test_er_sent_to_kontur(
    test_shipment_id: Any,
    *,
    accountant_name: str,
    external_reference: Any = "",
    occurred_at: float | None = None,
) -> dict[str, Any]:
    """Ручная отметка бухгалтера об отправке ЭР в Контур без API-интеграции."""
    shipment_id = _test_shipment_id(test_shipment_id)
    actor = _required_carrier_text(accountant_name, "имя бухгалтера", max_length=200)
    reference = _optional_carrier_text(external_reference, "реквизит Контура", max_length=500)
    now = float(occurred_at) if occurred_at is not None else _now()
    init_rumex_registry_db()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            shipment = _test_shipment_for_update(conn, shipment_id)
            status = str(shipment["status"])
            if status == "documents_ready":
                conn.commit()
            elif status != "awaiting_er_sent":
                raise ValueError("Расписку можно отметить отправленной только после проверки погрузки")
            else:
                conn.execute(
                    """UPDATE test_shipments SET
                           status = 'documents_ready', accountant_name = ?, kontur_reference = ?,
                           kontur_sent_at = ?, documents_ready_at = ?, updated_at = ?
                       WHERE id = ?""",
                    (actor, reference, now, now, now, shipment_id),
                )
                conn.execute(
                    """UPDATE test_shipment_documents
                       SET status = 'sent_to_kontur', external_reference = ?, updated_at = ?
                       WHERE test_shipment_id = ? AND document_kind = 'ER'""",
                    (reference, now, shipment_id),
                )
                conn.execute(
                    """UPDATE test_shipment_documents SET status = 'ready', updated_at = ?
                       WHERE test_shipment_id = ? AND document_kind = 'TN'""",
                    (now, shipment_id),
                )
                _append_test_shipment_event(
                    conn,
                    test_shipment_id=shipment_id,
                    event_type="test_er_sent_to_kontur_documents_opened",
                    actor_kind="accountant",
                    actor_id=actor,
                    actor_name=actor,
                    payload={"external_reference": reference},
                    occurred_at=now,
                )
                conn.commit()
        except Exception:
            conn.rollback()
            raise
    result = get_test_shipment(shipment_id)
    if result is None:
        raise RuntimeError("Не удалось прочитать тестовую погрузку после отметки Контура")
    return result


def confirm_test_documents_handed_to_driver(
    test_shipment_id: Any,
    *,
    dispatcher_max_user_id: int,
    dispatcher_name: str,
    dispatcher_identity_kind: str = "max",
    dispatcher_identity_id: str = "",
    occurred_at: float | None = None,
) -> dict[str, Any]:
    """Зафиксировать обязательное подтверждение печати и передачи водителю."""
    shipment_id = _test_shipment_id(test_shipment_id)
    dispatcher_id, dispatcher_kind, dispatcher_identity, dispatcher = _test_dispatcher_actor(
        dispatcher_max_user_id=dispatcher_max_user_id,
        dispatcher_name=dispatcher_name,
        dispatcher_identity_kind=dispatcher_identity_kind,
        dispatcher_identity_id=dispatcher_identity_id,
    )
    now = float(occurred_at) if occurred_at is not None else _now()
    init_rumex_registry_db()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            shipment = _test_shipment_for_update(conn, shipment_id)
            if str(shipment["status"]) == "documents_handed_to_driver":
                conn.commit()
            elif str(shipment["status"]) != "documents_ready":
                raise ValueError("Подтвердить передачу можно только после готовности документов")
            else:
                conn.execute(
                    """UPDATE test_shipments SET
                           status = 'documents_handed_to_driver', printed_by_max_user_id = ?,
                           printed_by_identity_kind = ?, printed_by_identity_id = ?,
                           printed_by_name = ?, printed_confirmed_at = ?, updated_at = ?
                        WHERE id = ?""",
                    (
                        dispatcher_id,
                        dispatcher_kind,
                        dispatcher_identity,
                        dispatcher,
                        now,
                        now,
                        shipment_id,
                    ),
                )
                conn.execute(
                    """UPDATE test_shipment_documents SET status = 'issued', updated_at = ?
                       WHERE test_shipment_id = ? AND status IN ('ready', 'sent_to_kontur')""",
                    (now, shipment_id),
                )
                _append_test_shipment_event(
                    conn,
                    test_shipment_id=shipment_id,
                    event_type="test_documents_handed_to_driver",
                    actor_kind="dispatcher_" + dispatcher_kind,
                    actor_id=dispatcher_identity,
                    actor_name=dispatcher,
                    payload={"printed_confirmed_at": now},
                    occurred_at=now,
                )
                conn.commit()
        except Exception:
            conn.rollback()
            raise
    result = get_test_shipment(shipment_id)
    if result is None:
        raise RuntimeError("Не удалось прочитать тестовую погрузку после передачи документов")
    return result


def mark_test_ttn_downloaded(
    test_shipment_id: Any,
    *,
    dispatcher_max_user_id: int,
    dispatcher_name: str,
    dispatcher_identity_kind: str = "max",
    dispatcher_identity_id: str = "",
    occurred_at: float | None = None,
) -> dict[str, Any]:
    """Отметить первое скачивание ТТН диспетчером, не подтверждая передачу водителю."""
    shipment_id = _test_shipment_id(test_shipment_id)
    dispatcher_id, dispatcher_kind, dispatcher_identity, dispatcher = _test_dispatcher_actor(
        dispatcher_max_user_id=dispatcher_max_user_id,
        dispatcher_name=dispatcher_name,
        dispatcher_identity_kind=dispatcher_identity_kind,
        dispatcher_identity_id=dispatcher_identity_id,
    )
    now = float(occurred_at) if occurred_at is not None else _now()
    init_rumex_registry_db()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            shipment = _test_shipment_for_update(conn, shipment_id)
            if str(shipment["status"]) not in {"documents_ready", "documents_handed_to_driver"}:
                raise ValueError("Скачать ТТН можно только после открытия документов")
            if shipment["ttn_printed_at"] is None:
                conn.execute(
                    """UPDATE test_shipments SET ttn_printed_at = ?, ttn_printed_by_name = ?,
                           updated_at = ? WHERE id = ?""",
                    (now, dispatcher, now, shipment_id),
                )
                _append_test_shipment_event(
                    conn,
                    test_shipment_id=shipment_id,
                    event_type="test_ttn_downloaded",
                    actor_kind="dispatcher_" + dispatcher_kind,
                    actor_id=dispatcher_identity,
                    actor_name=dispatcher,
                    payload={"ttn_printed_at": now, "dispatcher_max_user_id": dispatcher_id},
                    occurred_at=now,
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    result = get_test_shipment(shipment_id)
    if result is None:
        raise RuntimeError("Не удалось прочитать тестовую погрузку после скачивания ТТН")
    return result


def list_test_block_history(block_type_code: Any, block_number: Any, *, limit: int = 100) -> list[dict[str, Any]]:
    """Вернуть вечную историю изделия по паре «буква + индивидуальный номер»."""
    code = str(block_type_code or "").strip().upper()
    number = str(block_number or "").strip()
    if code not in BLOCK_CODES or not number:
        raise ValueError("Укажите букву и номер блока")
    try:
        parsed_limit = int(limit)
    except (TypeError, ValueError):
        parsed_limit = 100
    parsed_limit = max(1, min(parsed_limit, 200))
    init_rumex_registry_db()
    with _connect() as conn:
        rows = conn.execute(
            """SELECT item.*, shipment.registry_number, shipment.status,
                      shipment.loaded_at, shipment.dispatcher_name, shipment.revision_number
               FROM test_shipment_items AS item
               JOIN test_shipments AS shipment ON shipment.id = item.test_shipment_id
               WHERE item.block_type_code = ? AND item.block_number = ?
               ORDER BY item.created_at DESC, item.id DESC LIMIT ?""",
            (code, number, parsed_limit),
        ).fetchall()
    return [dict(row) for row in rows]


def create_shipment(
    *,
    items: Iterable[Mapping[str, Any]],
    registry_year: int | None = None,
    document_profile_id: int | None = None,
    carrier_id: int | None = None,
    driver_id: int | None = None,
    vehicle_id: int | None = None,
    dispatcher_max_user_id: int | None = None,
    dispatcher_name: str = "",
    note: str = "",
    created_at: float | None = None,
) -> dict[str, Any]:
    """Подтвердить отгрузку и атомарно зарезервировать её официальный номер.

    В одной транзакции создаются отгрузка, позиции, ЭР и ТТН с одинаковым
    номером. ТТН остаётся заблокированной до подтверждения ЭР.
    """
    init_rumex_registry_db()
    now = float(created_at) if created_at is not None else _now()
    year = _check_year(registry_year if registry_year is not None else _current_registry_year())
    actor_id = str(dispatcher_max_user_id) if dispatcher_max_user_id is not None else ""
    actor_name = (dispatcher_name or "").strip()

    with _connect() as conn:
        # IMMEDIATE не допускает две параллельные выдачи одного номера, а сама
        # транзакция охватывает и номер, и создаваемую отгрузку.
        conn.execute("BEGIN IMMEDIATE")
        try:
            _validate_catalog_reference(conn, "carriers", carrier_id, "Перевозчик")
            _validate_catalog_reference(conn, "drivers", driver_id, "Водитель")
            _validate_catalog_reference(conn, "vehicles", vehicle_id, "Автомобиль")
            profile_id = document_profile_id or _default_profile_id(conn)
            profile_snapshot = _profile_snapshot(conn, profile_id)
            carrier_snapshot = _catalog_snapshot(conn, "carriers", carrier_id)
            driver_snapshot = _catalog_snapshot(conn, "drivers", driver_id)
            vehicle_snapshot = _catalog_snapshot(conn, "vehicles", vehicle_id)
            normalized_items = _normalized_items(conn, items)
            sequence, registry_number = _reserve_number_in_transaction(
                conn, registry_year=year, now=now
            )
            cursor = conn.execute(
                """INSERT INTO shipments (
                       registry_number, registry_year, registry_sequence, status,
                       document_profile_id, profile_snapshot_json, carrier_id, driver_id, vehicle_id,
                       carrier_snapshot_json, driver_snapshot_json, vehicle_snapshot_json,
                       dispatcher_max_user_id, dispatcher_name, loaded_at, note, created_at, updated_at
                   ) VALUES (?, ?, ?, 'awaiting_er', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    registry_number,
                    year,
                    sequence,
                    profile_id,
                    _json(profile_snapshot),
                    carrier_id,
                    driver_id,
                    vehicle_id,
                    _json(carrier_snapshot),
                    _json(driver_snapshot),
                    _json(vehicle_snapshot),
                    dispatcher_max_user_id,
                    actor_name,
                    now,
                    (note or "").strip(),
                    now,
                    now,
                ),
            )
            shipment_id = int(cursor.lastrowid)
            for index, item in enumerate(normalized_items, start=1):
                conn.execute(
                    """INSERT INTO shipment_items (
                           shipment_id, sort_order, block_type_code, block_number,
                           product_name, weight_kg, created_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        shipment_id,
                        index,
                        item["block_type_code"],
                        item["block_number"],
                        item["product_name"],
                        item["weight_kg"],
                        now,
                    ),
                )
            conn.executemany(
                """INSERT INTO shipment_documents (
                       shipment_id, document_kind, registry_number, display_suffix,
                       status, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    (shipment_id, "ER", registry_number, "ЭР", "draft", now, now),
                    (
                        shipment_id,
                        "TN",
                        registry_number,
                        "ТТН",
                        "waiting_er_confirmation",
                        now,
                        now,
                    ),
                ),
            )
            _append_audit_event(
                conn,
                shipment_id=shipment_id,
                event_type="shipment_created",
                actor_kind="dispatcher",
                actor_id=actor_id,
                actor_name=actor_name,
                payload={
                    "registry_number": registry_number,
                    "registry_year": year,
                    "registry_sequence": sequence,
                    "items_count": len(normalized_items),
                },
                occurred_at=now,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    shipment = get_shipment(shipment_id)
    if shipment is None:  # Защита от повреждённой БД; штатно недостижимо.
        raise RuntimeError("Не удалось прочитать созданную отгрузку")
    return shipment


def get_shipment(shipment_id: int) -> dict[str, Any] | None:
    """Получить отгрузку вместе с её позициями, документами и историей."""
    init_rumex_registry_db()
    with _connect() as conn:
        shipment = conn.execute("SELECT * FROM shipments WHERE id = ?", (shipment_id,)).fetchone()
        if shipment is None:
            return None
        result = dict(shipment)
        result["items"] = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM shipment_items WHERE shipment_id = ? ORDER BY sort_order",
                (shipment_id,),
            )
        ]
        result["documents"] = [
            dict(row)
            for row in conn.execute(
                """SELECT *, registry_number || ' · ' || display_suffix AS display_number
                   FROM shipment_documents WHERE shipment_id = ? ORDER BY document_kind""",
                (shipment_id,),
            )
        ]
        result["audit_events"] = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM rumex_audit_events WHERE shipment_id = ? ORDER BY occurred_at, id",
                (shipment_id,),
            )
        ]
    return result


def list_shipments(*, limit: int = 100) -> list[dict[str, Any]]:
    """Вернуть отгрузки для бухгалтерского реестра, от новых к старым."""
    init_rumex_registry_db()
    try:
        parsed_limit = int(limit)
    except (TypeError, ValueError):
        parsed_limit = 100
    parsed_limit = max(1, min(parsed_limit, 200))
    with _connect() as conn:
        ids = [
            int(row["id"])
            for row in conn.execute(
                "SELECT id FROM shipments ORDER BY created_at DESC, id DESC LIMIT ?",
                (parsed_limit,),
            )
        ]
    return [shipment for shipment_id in ids if (shipment := get_shipment(shipment_id)) is not None]


def _shipment_for_update(conn: sqlite3.Connection, shipment_id: int) -> sqlite3.Row:
    shipment = conn.execute("SELECT * FROM shipments WHERE id = ?", (shipment_id,)).fetchone()
    if shipment is None:
        raise ValueError("Отгрузка не найдена")
    return shipment


def mark_er_sent(
    shipment_id: int,
    *,
    accountant_name: str,
    external_reference: str = "",
    occurred_at: float | None = None,
) -> dict[str, Any]:
    """Зафиксировать отправку ЭР бухгалтером в Контур.

    Отметка идемпотентна: повторный запрос не создаёт второе событие и не
    изменяет дату первой отправки.
    """
    init_rumex_registry_db()
    now = float(occurred_at) if occurred_at is not None else _now()
    actor = (accountant_name or "").strip()
    if not actor:
        raise ValueError("Не определён бухгалтер")

    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            shipment = _shipment_for_update(conn, shipment_id)
            status = str(shipment["status"])
            if status == "er_sent":
                conn.commit()
            elif status != "awaiting_er":
                raise ValueError("ЭР нельзя отправить в Контур в текущем статусе отгрузки")
            else:
                conn.execute(
                    "UPDATE shipments SET status = 'er_sent', updated_at = ? WHERE id = ?",
                    (now, shipment_id),
                )
                conn.execute(
                    """UPDATE shipment_documents
                       SET status = 'sent_to_kontur', external_reference = ?, updated_at = ?
                       WHERE shipment_id = ? AND document_kind = 'ER'""",
                    ((external_reference or "").strip(), now, shipment_id),
                )
                _append_audit_event(
                    conn,
                    shipment_id=shipment_id,
                    event_type="er_sent_to_kontur",
                    actor_kind="accountant",
                    actor_id=actor,
                    actor_name=actor,
                    payload={"external_reference": (external_reference or "").strip()},
                    occurred_at=now,
                )
                conn.commit()
        except Exception:
            conn.rollback()
            raise
    result = get_shipment(shipment_id)
    if result is None:
        raise RuntimeError("Не удалось прочитать отгрузку после отметки ЭР")
    return result


def mark_er_confirmed(
    shipment_id: int,
    *,
    accountant_name: str,
    external_reference: str = "",
    occurred_at: float | None = None,
) -> dict[str, Any]:
    """Зафиксировать подтверждение ЭР и открыть ТТН для диспетчера."""
    init_rumex_registry_db()
    now = float(occurred_at) if occurred_at is not None else _now()
    actor = (accountant_name or "").strip()
    if not actor:
        raise ValueError("Не определён бухгалтер")

    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            shipment = _shipment_for_update(conn, shipment_id)
            status = str(shipment["status"])
            if status == "tn_ready":
                conn.commit()
            elif status != "er_sent":
                raise ValueError("Подтверждение ЭР доступно только после отправки в Контур")
            else:
                external = (external_reference or "").strip()
                conn.execute(
                    """UPDATE shipments
                       SET status = 'tn_ready', er_confirmed_at = ?, updated_at = ?
                       WHERE id = ?""",
                    (now, now, shipment_id),
                )
                if external:
                    conn.execute(
                        """UPDATE shipment_documents
                           SET status = 'confirmed', external_reference = ?, updated_at = ?
                           WHERE shipment_id = ? AND document_kind = 'ER'""",
                        (external, now, shipment_id),
                    )
                else:
                    conn.execute(
                        """UPDATE shipment_documents
                           SET status = 'confirmed', updated_at = ?
                           WHERE shipment_id = ? AND document_kind = 'ER'""",
                        (now, shipment_id),
                    )
                conn.execute(
                    """UPDATE shipment_documents
                       SET status = 'ready', updated_at = ?
                       WHERE shipment_id = ? AND document_kind = 'TN'""",
                    (now, shipment_id),
                )
                _append_audit_event(
                    conn,
                    shipment_id=shipment_id,
                    event_type="er_confirmed_tn_opened",
                    actor_kind="accountant",
                    actor_id=actor,
                    actor_name=actor,
                    payload={"external_reference": external},
                    occurred_at=now,
                )
                conn.commit()
        except Exception:
            conn.rollback()
            raise
    result = get_shipment(shipment_id)
    if result is None:
        raise RuntimeError("Не удалось прочитать отгрузку после подтверждения ЭР")
    return result


def record_accountant_login_attempt(
    *,
    username: str = "",
    success: bool,
    failure_reason: str = "",
    remote_address: str = "",
    user_agent: str = "",
    attempted_at: float | None = None,
) -> None:
    """Сохранить попытку входа без записи PIN или сессионного токена."""
    init_rumex_registry_db()
    with _connect() as conn:
        conn.execute(
            """INSERT INTO accountant_login_attempts (
                   username, success, failure_reason, remote_address, user_agent, attempted_at
               ) VALUES (?, ?, ?, ?, ?, ?)""",
            (
                (username or "").strip()[:80],
                int(bool(success)),
                (failure_reason or "").strip()[:160],
                (remote_address or "").strip()[:120],
                (user_agent or "").strip()[:400],
                float(attempted_at) if attempted_at is not None else _now(),
            ),
        )


def failed_accountant_login_count(*, remote_address: str, since: float) -> int:
    """Количество неуспешных PIN-попыток с одного адреса после указанного времени."""
    init_rumex_registry_db()
    with _connect() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS count FROM accountant_login_attempts
               WHERE success = 0 AND remote_address = ? AND attempted_at >= ?""",
            ((remote_address or "").strip()[:120], float(since)),
        ).fetchone()
    return int(row["count"])


def create_accountant_session(
    *,
    token_hash: str,
    username: str,
    expires_at: float,
    remote_address: str = "",
    user_agent: str = "",
    created_at: float | None = None,
) -> None:
    """Сохранить хэш серверной сессии бухгалтера, не сам токен cookie."""
    if not token_hash or not username:
        raise ValueError("Для сессии требуется пользователь и хэш токена")
    init_rumex_registry_db()
    now = float(created_at) if created_at is not None else _now()
    with _connect() as conn:
        conn.execute(
            """INSERT INTO accountant_sessions (
                   token_hash, username, created_at, expires_at, last_seen_at, remote_address, user_agent
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                token_hash,
                username,
                now,
                float(expires_at),
                now,
                (remote_address or "").strip()[:120],
                (user_agent or "").strip()[:400],
            ),
        )


def accountant_session_username(
    *, token_hash: str, now: float | None = None, renewal_seconds: float | None = None
) -> str | None:
    """Вернуть пользователя действующей сессии и при необходимости продлить её."""
    if not token_hash:
        return None
    if renewal_seconds is not None and float(renewal_seconds) <= 0:
        raise ValueError("Срок продления сессии должен быть положительным")
    init_rumex_registry_db()
    current = float(now) if now is not None else _now()
    with _connect() as conn:
        row = conn.execute(
            """SELECT id, username FROM accountant_sessions
               WHERE token_hash = ? AND revoked_at IS NULL AND expires_at >= ?""",
            (token_hash, current),
        ).fetchone()
        if row is None:
            return None
        if renewal_seconds is None:
            conn.execute("UPDATE accountant_sessions SET last_seen_at = ? WHERE id = ?", (current, row["id"]))
        else:
            conn.execute(
                "UPDATE accountant_sessions SET last_seen_at = ?, expires_at = ? WHERE id = ?",
                (current, current + float(renewal_seconds), row["id"]),
            )
    return str(row["username"])


def revoke_accountant_session(*, token_hash: str, revoked_at: float | None = None) -> None:
    """Отозвать сессию при явном выходе из кабинета."""
    if not token_hash:
        return
    init_rumex_registry_db()
    with _connect() as conn:
        conn.execute(
            """UPDATE accountant_sessions SET revoked_at = ?
               WHERE token_hash = ? AND revoked_at IS NULL""",
            (float(revoked_at) if revoked_at is not None else _now(), token_hash),
        )


def record_test_dispatcher_login_attempt(
    *,
    username: str = "",
    success: bool,
    failure_reason: str = "",
    remote_address: str = "",
    user_agent: str = "",
    attempted_at: float | None = None,
) -> None:
    """Сохранить попытку парольного входа тестового диспетчера без пароля."""
    init_rumex_registry_db()
    with _connect() as conn:
        conn.execute(
            """INSERT INTO test_dispatcher_login_attempts (
                   username, success, failure_reason, remote_address, user_agent, attempted_at
               ) VALUES (?, ?, ?, ?, ?, ?)""",
            (
                (username or "").strip()[:80],
                int(bool(success)),
                (failure_reason or "").strip()[:160],
                (remote_address or "").strip()[:120],
                (user_agent or "").strip()[:400],
                float(attempted_at) if attempted_at is not None else _now(),
            ),
        )


def failed_test_dispatcher_login_count(*, remote_address: str, since: float) -> int:
    """Вернуть число неуспешных парольных попыток с одного адреса."""
    init_rumex_registry_db()
    with _connect() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS count FROM test_dispatcher_login_attempts
               WHERE success = 0 AND remote_address = ? AND attempted_at >= ?""",
            ((remote_address or "").strip()[:120], float(since)),
        ).fetchone()
    return int(row["count"])


def create_test_dispatcher_session(
    *,
    token_hash: str,
    username: str,
    expires_at: float,
    remote_address: str = "",
    user_agent: str = "",
    created_at: float | None = None,
) -> None:
    """Сохранить хэш отдельной сессии тестового диспетчера."""
    if not token_hash or not username:
        raise ValueError("Для сессии требуется пользователь и хэш токена")
    init_rumex_registry_db()
    now = float(created_at) if created_at is not None else _now()
    with _connect() as conn:
        conn.execute(
            """INSERT INTO test_dispatcher_sessions (
                   token_hash, username, created_at, expires_at, last_seen_at, remote_address, user_agent
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                token_hash,
                username,
                now,
                float(expires_at),
                now,
                (remote_address or "").strip()[:120],
                (user_agent or "").strip()[:400],
            ),
        )


def test_dispatcher_session_username(
    *, token_hash: str, now: float | None = None, renewal_seconds: float | None = None
) -> str | None:
    """Вернуть пользователя сессии тестового диспетчера и при необходимости продлить её."""
    if not token_hash:
        return None
    if renewal_seconds is not None and float(renewal_seconds) <= 0:
        raise ValueError("Срок продления сессии должен быть положительным")
    init_rumex_registry_db()
    current = float(now) if now is not None else _now()
    with _connect() as conn:
        row = conn.execute(
            """SELECT id, username FROM test_dispatcher_sessions
               WHERE token_hash = ? AND revoked_at IS NULL AND expires_at >= ?""",
            (token_hash, current),
        ).fetchone()
        if row is None:
            return None
        if renewal_seconds is None:
            conn.execute("UPDATE test_dispatcher_sessions SET last_seen_at = ? WHERE id = ?", (current, row["id"]))
        else:
            conn.execute(
                "UPDATE test_dispatcher_sessions SET last_seen_at = ?, expires_at = ? WHERE id = ?",
                (current, current + float(renewal_seconds), row["id"]),
            )
    return str(row["username"])


def revoke_test_dispatcher_session(*, token_hash: str, revoked_at: float | None = None) -> None:
    """Отозвать сессию тестового диспетчера при явном выходе."""
    if not token_hash:
        return
    init_rumex_registry_db()
    with _connect() as conn:
        conn.execute(
            """UPDATE test_dispatcher_sessions SET revoked_at = ?
               WHERE token_hash = ? AND revoked_at IS NULL""",
            (float(revoked_at) if revoked_at is not None else _now(), token_hash),
        )


def _management_audit(
    conn: sqlite3.Connection,
    *,
    event_type: str,
    actor_name: str,
    subject_type: str = "",
    subject_id: str = "",
    payload: Mapping[str, Any] | None = None,
    occurred_at: float | None = None,
) -> None:
    conn.execute(
        """INSERT INTO rumex_management_audit_events (
               event_type, actor_name, subject_type, subject_id, payload_json, occurred_at
           ) VALUES (?, ?, ?, ?, ?, ?)""",
        (
            event_type,
            (actor_name or "").strip()[:200],
            (subject_type or "").strip()[:80],
            (subject_id or "").strip()[:200],
            _json(dict(payload or {})),
            float(occurred_at) if occurred_at is not None else _now(),
        ),
    )


def _dispatcher_account_from_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result.pop("password_hash", None)
    result["active"] = bool(result["active"])
    return result


def list_rumex_dispatcher_accounts() -> list[dict[str, Any]]:
    """Вернуть управляемые учётные записи диспетчеров без хэшей паролей."""
    init_rumex_registry_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM rumex_dispatcher_accounts ORDER BY active DESC, full_name, username"
        ).fetchall()
        return [_dispatcher_account_from_row(row) for row in rows]


def rumex_dispatcher_account_count() -> int:
    init_rumex_registry_db()
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS count FROM rumex_dispatcher_accounts").fetchone()
    return int(row["count"])


def create_rumex_dispatcher_account(
    *, username: Any, full_name: Any, max_user_id: Any, password_hash: str, actor_name: str
) -> dict[str, Any]:
    """Создать отдельную учётную запись диспетчера РУМЕКС."""
    login = _required_carrier_text(username, "логин", max_length=80)
    name = _required_carrier_text(full_name, "ФИО", max_length=200)
    if not password_hash:
        raise ValueError("Не задан хэш пароля")
    max_id = _source_max_user_id(max_user_id)
    now = _now()
    init_rumex_registry_db()
    with _connect() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """INSERT INTO rumex_dispatcher_accounts (
                       username, full_name, max_user_id, password_hash, created_at, updated_at,
                       last_password_issued_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (login, name, max_id, password_hash, now, now, now),
            )
            _management_audit(
                conn, event_type="dispatcher_created", actor_name=actor_name,
                subject_type="dispatcher", subject_id=login,
                payload={"dispatcher_id": cursor.lastrowid, "max_user_id": max_id}, occurred_at=now,
            )
            conn.commit()
        except sqlite3.IntegrityError:
            conn.rollback()
            raise ValueError("Логин диспетчера или MAX ID уже используется") from None
        except Exception:
            conn.rollback()
            raise
    return get_rumex_dispatcher_account(login) or {}


def get_rumex_dispatcher_account(username: Any, *, include_hash: bool = False) -> dict[str, Any] | None:
    login = str(username or "").strip()
    if not login:
        return None
    init_rumex_registry_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM rumex_dispatcher_accounts WHERE username = ? COLLATE NOCASE", (login,)
        ).fetchone()
    if row is None:
        return None
    result = dict(row) if include_hash else _dispatcher_account_from_row(row)
    result["active"] = bool(result["active"])
    return result


def touch_rumex_dispatcher_activity(username: Any) -> None:
    login = str(username or "").strip()
    if not login:
        return
    init_rumex_registry_db()
    with _connect() as conn:
        conn.execute(
            "UPDATE rumex_dispatcher_accounts SET last_activity_at = ? WHERE username = ? COLLATE NOCASE",
            (_now(), login),
        )


def rumex_admin_access_by_max_user_id(max_user_id: Any) -> dict[str, Any] | None:
    parsed = _source_max_user_id(max_user_id)
    if parsed is None:
        return None
    init_rumex_registry_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT username, full_name, max_user_id, role, active FROM rumex_admin_accounts WHERE max_user_id = ?",
            (parsed,),
        ).fetchone()
    if row is None or not bool(row["active"]):
        return None
    result = dict(row)
    result["active"] = bool(result["active"])
    return result


def update_rumex_dispatcher_account(
    *, username: Any, full_name: Any, max_user_id: Any, active: bool, actor_name: str
) -> dict[str, Any]:
    login = _required_carrier_text(username, "логин", max_length=80)
    name = _required_carrier_text(full_name, "ФИО", max_length=200)
    if not isinstance(active, bool):
        raise ValueError("Статус учётной записи должен быть логическим")
    now = _now()
    init_rumex_registry_db()
    with _connect() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """UPDATE rumex_dispatcher_accounts
                   SET full_name = ?, max_user_id = ?, active = ?, updated_at = ?
                   WHERE username = ? COLLATE NOCASE""",
                (name, _source_max_user_id(max_user_id), int(active), now, login),
            )
            if cursor.rowcount != 1:
                raise ValueError("Диспетчер не найден")
            if not active:
                conn.execute(
                    "UPDATE test_dispatcher_sessions SET revoked_at = ? WHERE username = ? COLLATE NOCASE AND revoked_at IS NULL",
                    (now, login),
                )
            _management_audit(
                conn, event_type="dispatcher_updated", actor_name=actor_name,
                subject_type="dispatcher", subject_id=login,
                payload={"max_user_id": _source_max_user_id(max_user_id), "active": active}, occurred_at=now,
            )
            conn.commit()
        except sqlite3.IntegrityError:
            conn.rollback()
            raise ValueError("MAX ID уже используется другим диспетчером") from None
        except Exception:
            conn.rollback()
            raise
    return get_rumex_dispatcher_account(login) or {}


def reset_rumex_dispatcher_password(*, username: Any, password_hash: str, actor_name: str) -> None:
    login = _required_carrier_text(username, "логин", max_length=80)
    if not password_hash:
        raise ValueError("Не задан хэш пароля")
    now = _now()
    init_rumex_registry_db()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = conn.execute(
                """UPDATE rumex_dispatcher_accounts
                   SET password_hash = ?, updated_at = ?, last_password_issued_at = ?
                   WHERE username = ? COLLATE NOCASE""",
                (password_hash, now, now, login),
            )
            if cursor.rowcount != 1:
                raise ValueError("Диспетчер не найден")
            conn.execute(
                "UPDATE test_dispatcher_sessions SET revoked_at = ? WHERE username = ? COLLATE NOCASE AND revoked_at IS NULL",
                (now, login),
            )
            _management_audit(
                conn, event_type="dispatcher_password_reset", actor_name=actor_name,
                subject_type="dispatcher", subject_id=login, occurred_at=now,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def revoke_rumex_dispatcher_sessions(*, username: Any, actor_name: str) -> int:
    login = _required_carrier_text(username, "логин", max_length=80)
    now = _now()
    init_rumex_registry_db()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = conn.execute(
                "UPDATE test_dispatcher_sessions SET revoked_at = ? WHERE username = ? COLLATE NOCASE AND revoked_at IS NULL",
                (now, login),
            )
            _management_audit(
                conn, event_type="dispatcher_sessions_revoked", actor_name=actor_name,
                subject_type="dispatcher", subject_id=login,
                payload={"session_count": cursor.rowcount}, occurred_at=now,
            )
            conn.commit()
            return cursor.rowcount
        except Exception:
            conn.rollback()
            raise


def rumex_dispatcher_security_summary(username: Any) -> dict[str, Any]:
    login = _required_carrier_text(username, "логин", max_length=80)
    init_rumex_registry_db()
    with _connect() as conn:
        attempts = [
            dict(row) for row in conn.execute(
                """SELECT username, success, failure_reason, remote_address, attempted_at
                   FROM test_dispatcher_login_attempts WHERE username = ? COLLATE NOCASE
                   ORDER BY attempted_at DESC, id DESC LIMIT 30""", (login,)
            )
        ]
        for row in attempts:
            row["success"] = bool(row["success"])
        sessions = [dict(row) for row in conn.execute(
            """SELECT created_at, expires_at, last_seen_at, revoked_at, remote_address
               FROM test_dispatcher_sessions WHERE username = ? COLLATE NOCASE
               ORDER BY created_at DESC LIMIT 30""", (login,)
        )]
    return {"login_attempts": attempts, "sessions": sessions}


def set_rumex_vehicle_shipment_block(*, plate_tail: Any, blocked: bool, reason: Any, actor_name: str) -> None:
    tail = _normalize_plate_tail(plate_tail)
    actor = _required_carrier_text(actor_name, "администратора", max_length=200)
    note = _optional_carrier_text(reason, "причину", max_length=500)
    if blocked and not note:
        raise ValueError("Укажите причину блокировки машины")
    now = _now()
    init_rumex_registry_db()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if blocked:
                conn.execute(
                    """INSERT INTO rumex_vehicle_shipment_blocks (plate_tail, reason, blocked_by, blocked_at)
                       VALUES (?, ?, ?, ?) ON CONFLICT(plate_tail) DO UPDATE SET
                       reason = excluded.reason, blocked_by = excluded.blocked_by, blocked_at = excluded.blocked_at,
                       unblocked_by = '', unblocked_at = NULL""",
                    (tail, note, actor, now),
                )
            else:
                conn.execute(
                    "UPDATE rumex_vehicle_shipment_blocks SET unblocked_by = ?, unblocked_at = ? WHERE plate_tail = ?",
                    (actor, now, tail),
                )
            _management_audit(
                conn, event_type="vehicle_shipment_blocked" if blocked else "vehicle_shipment_unblocked",
                actor_name=actor, subject_type="document_vehicle", subject_id=tail,
                payload={"reason": note}, occurred_at=now,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def rumex_vehicle_shipment_block(plate_tail: Any) -> dict[str, Any] | None:
    tail = _normalize_plate_tail(plate_tail)
    init_rumex_registry_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM rumex_vehicle_shipment_blocks WHERE plate_tail = ? AND unblocked_at IS NULL", (tail,)
        ).fetchone()
    return dict(row) if row is not None else None


def list_rumex_management_audit_events(*, limit: int = 100) -> list[dict[str, Any]]:
    parsed_limit = max(1, min(int(limit), 200))
    init_rumex_registry_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM rumex_management_audit_events ORDER BY occurred_at DESC, id DESC LIMIT ?", (parsed_limit,)
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        result.append(item)
    return result


def create_rumex_admin_session(
    *, token_hash: str, username: str, max_user_id: int, expires_at: float,
    remote_address: str = "", user_agent: str = ""
) -> None:
    init_rumex_registry_db()
    now = _now()
    with _connect() as conn:
        conn.execute(
            """INSERT INTO rumex_admin_sessions (
                   token_hash, username, max_user_id, created_at, expires_at, last_seen_at,
                   remote_address, user_agent
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (token_hash, username, int(max_user_id), now, float(expires_at), now,
             (remote_address or "")[:120], (user_agent or "")[:400]),
        )


def rumex_admin_session_identity(*, token_hash: str, renewal_seconds: float | None = None) -> dict[str, Any] | None:
    if not token_hash:
        return None
    now = _now()
    init_rumex_registry_db()
    with _connect() as conn:
        row = conn.execute(
            """SELECT id, username, max_user_id FROM rumex_admin_sessions
               WHERE token_hash = ? AND revoked_at IS NULL AND expires_at >= ?""", (token_hash, now)
        ).fetchone()
        if row is None:
            return None
        if renewal_seconds is None:
            conn.execute("UPDATE rumex_admin_sessions SET last_seen_at = ? WHERE id = ?", (now, row["id"]))
        else:
            conn.execute(
                "UPDATE rumex_admin_sessions SET last_seen_at = ?, expires_at = ? WHERE id = ?",
                (now, now + float(renewal_seconds), row["id"]),
            )
    return {"username": str(row["username"]), "max_user_id": int(row["max_user_id"])}


def revoke_rumex_admin_session(*, token_hash: str) -> None:
    if not token_hash:
        return
    init_rumex_registry_db()
    with _connect() as conn:
        conn.execute(
            "UPDATE rumex_admin_sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
            (_now(), token_hash),
        )
