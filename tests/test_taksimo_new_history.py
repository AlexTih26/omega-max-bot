"""Проверки сквозной истории блока и машины без подключения к MySQL."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
BOT = ROOT / "fotonych-bot"
if str(BOT) not in sys.path:
    sys.path.insert(0, str(BOT))

if "dotenv" not in sys.modules:
    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda *_args, **_kwargs: False
    sys.modules["dotenv"] = dotenv

import taksimo_new_store as store  # noqa: E402


DISPATCHER = {"id": 11, "role": "dispatcher", "name": "Диспетчер"}
ACCOUNTANT = {"id": 12, "role": "accountant1", "name": "Бухгалтер 1"}
OPERATOR = {"id": 7, "role": "operator1", "name": "Оператор 1"}


class BlockKeyTests(unittest.TestCase):
    def test_key_is_letter_dash_number_uppercase(self) -> None:
        self.assertEqual(store._block_key("a", "101"), "A-101")

    def test_key_ignores_spaces_in_number(self) -> None:
        self.assertEqual(store._block_key("B", " 2 02 "), "B-202")

    def test_key_accepts_every_catalog_letter(self) -> None:
        for letter in "ABCDEFK":
            self.assertEqual(store._block_key(letter, "1"), f"{letter}-1")

    def test_key_rejects_unknown_letter(self) -> None:
        for bad in ("Z", "", None, "AB"):
            with self.assertRaises(ValueError):
                store._block_key(bad, "1")

    def test_same_physical_block_gets_one_key(self) -> None:
        self.assertEqual(store._block_key("A", "901"), store._block_key("a", " 901 "))

    def test_key_rejects_overlong_number(self) -> None:
        with self.assertRaises(ValueError):
            store._block_key("K", "9" * 200)


class _Cursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, object]] = []
        self.lastrowid = 1
        self._row: dict | None = None
        self._rows: list[dict] = []
        self.existing_passport: dict | None = None
        self._inserted: tuple[str, tuple] | None = None

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        return None

    def execute(self, statement: str, parameters: object = None) -> None:
        query = " ".join(statement.split())
        self.executed.append((query, parameters))
        self._row = None
        self._rows = []
        if "INSERT INTO tn_block_registry" in query:
            self._inserted = (query, tuple(parameters or ()))
            self.lastrowid = 1
        if query.startswith("SELECT * FROM tn_block_registry"):
            self._row = self.existing_passport
        elif query.startswith("SELECT block_key, state, weight_kg, price_rub"):
            if self.existing_passport is not None:
                self._row = self.existing_passport
            else:
                key = str((self._inserted or ("", ("",)))[1][0])
                self._row = {
                    "block_key": key,
                    "state": "declared",
                    "weight_kg": (self._inserted or ("", (None,)))[1][4],
                    "price_rub": (self._inserted or ("", (0,)))[1][5],
                }

    def fetchone(self) -> dict | None:
        return self._row

    def fetchall(self) -> list[dict]:
        return self._rows


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _Cursor:
        return self._cursor


class BlockRegistryTests(unittest.TestCase):
    def _cursor(self):
        cursor = _Cursor()
        connection = _Connection(cursor)

        @contextmanager
        def transaction():
            yield connection

        return cursor, transaction

    def test_new_passport_keeps_birth_weight(self) -> None:
        cursor, transaction = self._cursor()
        with patch.object(store, "transaction", transaction), patch.object(
            store, "utc_now", return_value=datetime(2026, 1, 1, 12, 0, 0)
        ):
            passport = store._ensure_block_registry(
                cursor, letter="A", number="901", weight_kg=7780, price_rub=1200, trip_id=None
            )
        self.assertEqual(passport["block_key"], "A-901")
        self.assertEqual(passport["weight_kg"], 7780)
        self.assertEqual(passport["state"], "declared")
        self.assertTrue(any("INSERT INTO tn_block_registry" in q for q, _ in cursor.executed))

    def test_existing_passport_weight_is_not_overwritten(self) -> None:
        cursor, transaction = self._cursor()
        cursor.existing_passport = {
            "id": 5, "block_key": "A-901", "state": "in_transit", "weight_kg": 7780, "price_rub": 1200
        }
        with patch.object(store, "transaction", transaction), patch.object(
            store, "utc_now", return_value=datetime(2026, 1, 1, 12, 0, 0)
        ):
            passport = store._ensure_block_registry(
                cursor, letter="A", number="901", weight_kg=9999, price_rub=9999
            )
        self.assertEqual(passport["id"], 5)
        self.assertEqual(passport["weight_kg"], 7780)
        self.assertFalse(any("INSERT INTO tn_block_registry" in q for q, _ in cursor.executed))

    def test_history_row_records_actor(self) -> None:
        cursor, transaction = self._cursor()
        with patch.object(store, "transaction", transaction), patch.object(
            store, "utc_now", return_value=datetime(2026, 1, 1, 12, 0, 0)
        ):
            store._append_block_history(
                cursor, block_id=5, event_type="trip_declared", location_kind="in_transit",
                trip_id=3, operator=DISPATCHER,
            )
        query, parameters = next((q, p) for q, p in cursor.executed if "INSERT INTO tn_block_history" in q)
        self.assertIn("trip_declared", parameters)
        self.assertIn("Диспетчер", parameters)
        self.assertIn("in_transit", parameters)


class HistoryRoleTests(unittest.TestCase):
    def _run(self, function, operator, **kwargs):
        cursor = _Cursor()
        connection = _Connection(cursor)

        @contextmanager
        def transaction():
            yield connection

        with patch.object(store, "transaction", transaction):
            return function(operator=operator, **kwargs)

    def test_operator_cannot_register_trip_blocks(self) -> None:
        with self.assertRaises(PermissionError):
            self._run(store.register_trip_blocks, OPERATOR, public_id="x")

    def test_accountant_cannot_register_trip_blocks(self) -> None:
        with self.assertRaises(PermissionError):
            self._run(store.register_trip_blocks, ACCOUNTANT, public_id="x")

    def test_dispatcher_may_register_trip_blocks(self) -> None:
        cursor = _Cursor()
        connection = _Connection(cursor)

        @contextmanager
        def transaction():
            yield connection

        with patch.object(store, "transaction", transaction):
            with patch.object(store, "_require_role"):
                with self.assertRaises(KeyError):
                    store.register_trip_blocks("x", operator=DISPATCHER)


class HistoryStatesTests(unittest.TestCase):
    def test_registry_states_match_migration(self) -> None:
        source = (ROOT / "migrations" / "taksimo_new" / "013_block_and_vehicle_history.sql").read_text(encoding="utf-8")
        for state in ("declared", "in_transit", "at_yard", "in_wagon", "in_kodar", "unloaded_bts_east", "returned"):
            self.assertIn(f"'{state}'", source)

    def test_history_table_keeps_events_immutable_by_design(self) -> None:
        source = (ROOT / "migrations" / "taksimo_new" / "013_block_and_vehicle_history.sql").read_text(encoding="utf-8")
        self.assertIn("ON DELETE RESTRICT", source)


if __name__ == "__main__":
    unittest.main()
