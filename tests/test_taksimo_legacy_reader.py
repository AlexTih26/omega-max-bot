"""Проверки read-only чтения старой SQLite-Таксимо новым контуром."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
BOT = ROOT / "fotonych-bot"
if str(BOT) not in sys.path:
    sys.path.insert(0, str(BOT))

import taksimo_legacy_reader as legacy  # noqa: E402


def _create_legacy_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE vehicles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plate TEXT NOT NULL UNIQUE, brand TEXT NOT NULL DEFAULT '',
            driver TEXT NOT NULL DEFAULT '', sort_order INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE wagon_pool (
            id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT NOT NULL UNIQUE,
            active INTEGER NOT NULL DEFAULT 1, sort_order INTEGER NOT NULL DEFAULT 0,
            stage TEXT NOT NULL DEFAULT '', planned_zone TEXT NOT NULL DEFAULT '',
            slot_id INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE wagon_slots (
            id INTEGER PRIMARY KEY AUTOINCREMENT, zone TEXT NOT NULL,
            slot_index INTEGER NOT NULL, wagon_number TEXT NOT NULL DEFAULT '',
            expected_blocks TEXT NOT NULL DEFAULT '[]', updated_at REAL,
            scheme_code TEXT NOT NULL DEFAULT 'scheme1', has_box INTEGER NOT NULL DEFAULT 0,
            UNIQUE(zone, slot_index)
        );
        CREATE TABLE slabs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL,
            letter TEXT NOT NULL, number TEXT NOT NULL, pos_x INTEGER NOT NULL,
            pos_y INTEGER NOT NULL, suffix TEXT NOT NULL DEFAULT '',
            weight TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
            on_yard INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL,
            platform_zone TEXT NOT NULL DEFAULT '', wagon_number TEXT NOT NULL DEFAULT '',
            loading_date TEXT NOT NULL DEFAULT '', customer TEXT NOT NULL DEFAULT '',
            wagon_dispatch_id INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE unload_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, unload_date TEXT NOT NULL,
            trn TEXT NOT NULL DEFAULT '', vehicle_id INTEGER, driver TEXT NOT NULL DEFAULT '',
            crane_start TEXT NOT NULL DEFAULT '', crane_end TEXT NOT NULL DEFAULT '',
            crane_minutes INTEGER, riggers_count INTEGER NOT NULL DEFAULT 2,
            riggers_pay INTEGER NOT NULL DEFAULT 6000, taxi_pay INTEGER NOT NULL DEFAULT 0,
            notes TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL,
            operator TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 1,
            updated_at REAL NOT NULL DEFAULT 0, unload_datetime TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'completed'
        );
        CREATE TABLE wagon_dispatches (
            id INTEGER PRIMARY KEY AUTOINCREMENT, wagon_number TEXT NOT NULL,
            slot_zone TEXT NOT NULL DEFAULT '', slot_index INTEGER NOT NULL DEFAULT 0,
            slab_count INTEGER NOT NULL DEFAULT 0, blocks_json TEXT NOT NULL DEFAULT '[]',
            dispatched_at REAL NOT NULL, dispatched_by TEXT NOT NULL DEFAULT '',
            received_at REAL, received_by TEXT NOT NULL DEFAULT '',
            customer TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'in_transit',
            return_status TEXT NOT NULL DEFAULT '', return_target_zone TEXT NOT NULL DEFAULT '',
            return_actual_zone TEXT NOT NULL DEFAULT ''
        );
        INSERT INTO vehicles (plate, brand, driver, sort_order, active) VALUES
            ('А111АА', 'Камаз', 'Иванов', 0, 1),
            ('Б222ББ', 'Урал', 'Петров', 1, 1),
            ('В333ВВ', 'Маз', 'Архив', 2, 0);
        INSERT INTO wagon_pool (number, active, sort_order, stage, planned_zone, slot_id) VALUES
            ('12345678', 1, 0, 'ready', 'ГРУЗОВОЙ', 3),
            ('87654321', 0, 1, 'archive', '', 0);
        INSERT INTO wagon_slots (zone, slot_index, wagon_number) VALUES
            ('ГРУЗОВОЙ', 1, '12345678'),
            ('ТУРАН', 2, '');
        INSERT INTO slabs (session_id, letter, number, pos_x, pos_y, platform_zone, created_at) VALUES
            (1, 'A', '001', 2, 3, 'ХРАНЕНИЯ', 1700000000);
        INSERT INTO unload_sessions (unload_date, driver, status, operator, created_at) VALUES
            ('20260101', 'Иванов', 'completed', 'оператор1', 1700000000);
        INSERT INTO wagon_dispatches (wagon_number, dispatched_at, status) VALUES
            ('12345678', 1700000000, 'in_transit');
        """
    )
    connection.commit()
    connection.close()


class TaksimoLegacyReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._path = Path(self._tmp.name) / "taksimo.db"
        _create_legacy_db(self._path)
        self._patcher = patch.object(legacy, "DB_PATH", self._path)
        self._patcher.start()

    def tearDown(self) -> None:
        self._patcher.stop()
        self._tmp.cleanup()

    def test_vehicles_returns_active_only_and_ordered(self) -> None:
        vehicles = legacy.list_legacy_vehicles()
        self.assertEqual([v["plate"] for v in vehicles], ["А111АА", "Б222ББ"])
        self.assertEqual(vehicles[0]["driver"], "Иванов")

    def test_vehicles_include_inactive(self) -> None:
        plates = [v["plate"] for v in legacy.list_legacy_vehicles(include_inactive=True)]
        self.assertIn("В333ВВ", plates)

    def test_wagons_returns_active_and_fields(self) -> None:
        wagons = legacy.list_legacy_wagons()
        self.assertEqual([w["number"] for w in wagons], ["12345678"])
        self.assertEqual(wagons[0]["stage"], "ready")
        self.assertEqual(wagons[0]["slot_id"], 3)

    def test_snapshot_returns_the_same_active_catalogs_as_live_views(self) -> None:
        snapshot = legacy.read_legacy_snapshot()
        self.assertEqual(snapshot["vehicles"], legacy.list_legacy_vehicles())
        self.assertEqual(snapshot["wagons"], legacy.list_legacy_wagons())
        self.assertEqual(snapshot["dispatch_slabs"], [])

    def test_slots(self) -> None:
        slots = legacy.list_legacy_wagon_slots()
        self.assertEqual(len(slots), 2)
        self.assertEqual(slots[0]["zone"], "ГРУЗОВОЙ")
        self.assertEqual(slots[0]["wagon_number"], "12345678")

    def test_slabs_sessions_history_read(self) -> None:
        self.assertEqual(len(legacy.list_legacy_slabs()), 1)
        self.assertEqual(len(legacy.list_legacy_sessions()), 1)
        self.assertEqual(len(legacy.list_legacy_wagon_history()), 1)

    def test_yard_slabs_filters_to_on_yard(self) -> None:
        connection = sqlite3.connect(self._path)
        connection.execute(
            "INSERT INTO slabs (session_id, letter, number, pos_x, pos_y, platform_zone, on_yard, created_at) "
            "VALUES (1, 'B', '002', 4, 5, 'В КОДАР', 0, 1700000000)"
        )
        connection.commit()
        connection.close()
        slabs = legacy.list_legacy_yard_slabs()
        self.assertEqual(len(slabs), 1)
        self.assertEqual(slabs[0]["letter"], "A")

    def test_connection_is_read_only(self) -> None:
        connection = legacy._connect_ro()
        self.assertIsNotNone(connection)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("INSERT INTO vehicles (plate) VALUES ('Х')")
        finally:
            connection.close()

    def test_missing_db_returns_empty(self) -> None:
        with patch.object(legacy, "DB_PATH", Path(self._tmp.name) / "nope.db"):
            self.assertEqual(legacy.list_legacy_vehicles(), [])
            self.assertEqual(legacy.list_legacy_wagons(), [])
            self.assertEqual(legacy.list_legacy_wagon_slots(), [])


if __name__ == "__main__":
    unittest.main()
