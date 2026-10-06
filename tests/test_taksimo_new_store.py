"""Проверки доменной модели новой Таксимо без подключения к MySQL."""

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


class _Cursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, object]] = []
        self.lastrowid = 0
        self._row: dict | None = None
        self.wagon_status: str | None = None
        self.active_position: dict | None = None

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        return None

    def execute(self, statement: str, parameters: object = None) -> None:
        query = " ".join(statement.split())
        self.executed.append((query, parameters))
        self._row = None
        if "FROM tn_blocks AS block" in query:
            self._row = {
                "id": 7,
                "block_type": "А",
                "block_number": "001",
                "current_location_kind": "yard",
                "rumex_shipment_id": 55,
                "rumex_document_version": 1,
            }
            if "i.physical_owner" in query:
                self._row["physical_owner"] = "taksimo_new"
        elif query.startswith("SELECT * FROM tn_wagons"):
            self._row = {"id": 41, "status": self.wagon_status} if self.wagon_status else None
        elif "FROM tn_wagon_position_history" in query and "WHERE wagon_id = %s AND active_marker = 1" in query:
            self._row = self.active_position
        elif "COUNT(*) AS count" in query:
            self._row = {"count": 5}
        elif query.startswith("INSERT INTO tn_wagons"):
            self.lastrowid = 41
        elif query.startswith("INSERT INTO tn_wagon_loads"):
            self.lastrowid = 42

    def fetchone(self) -> dict | None:
        return self._row

    def fetchall(self) -> list[dict]:
        return []


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _Cursor:
        return self._cursor


class TaksimoNewStoreTests(unittest.TestCase):
    def test_rumex_linked_block_can_be_loaded_to_wagon_with_owner_in_outbox(self) -> None:
        cursor = _Cursor()
        connection = _Connection(cursor)
        operator = {"id": 3, "role": "operator1", "name": "Оператор 1"}

        @contextmanager
        def transaction():
            yield connection

        with patch.object(store, "transaction", transaction), patch.object(
            store, "utc_now", return_value=datetime(2026, 1, 1, 12, 0, 0)
        ), patch.object(store, "_ensure_wagon_position") as ensure_position, patch.object(
            store, "_append_event"
        ), patch.object(store, "_append_outbox") as append_outbox:
            result = store.load_block_to_wagon(
                block_id=7, wagon_number="123", dead_end_code="gruzovoy_1", slot_index=2, operator=operator
            )

        self.assertEqual(result["id"], 42)
        self.assertEqual(result["wagon_number"], "123")
        self.assertEqual(result["block_id"], 7)
        self.assertTrue(any("i.physical_owner" in query for query, _ in cursor.executed))
        append_outbox.assert_called_once()
        payload = append_outbox.call_args.kwargs["payload"]
        self.assertEqual(payload["rumex_shipment_id"], 55)
        self.assertEqual(payload["rumex_document_version"], 1)
        self.assertEqual(payload["physical_owner"], "taksimo_new")
        self.assertEqual(payload["dead_end_code"], "gruzovoy_1")
        self.assertEqual(payload["slot_index"], 2)
        ensure_position.assert_called_once()

    def test_mark_wagon_returned_empty(self) -> None:
        cursor = _Cursor()
        cursor.wagon_status = "unloaded_bts_east"
        connection = _Connection(cursor)
        operator = {"id": 3, "role": "operator1", "name": "Оператор 1"}

        @contextmanager
        def transaction():
            yield connection

        with patch.object(store, "transaction", transaction), patch.object(
            store, "utc_now", return_value=datetime(2026, 1, 1, 12, 0, 0)
        ), patch.object(store, "_ensure_wagon_position") as ensure_position, patch.object(store, "_append_event"), patch.object(
            store, "_append_wagon_physical_facts"
        ), patch.object(store, "_append_notification"):
            result = store.mark_wagon_returned_empty(
                wagon_number="123", dead_end_code="gruzovoy_2", slot_index=4, operator=operator
            )

        self.assertEqual(result["wagon_number"], "123")
        self.assertEqual(result["status"], "returned_empty")
        self.assertEqual(result["blocks_count"], 5)
        self.assertEqual(result["dead_end_code"], "gruzovoy_2")
        self.assertEqual(result["slot_index"], 4)
        ensure_position.assert_called_once()
        self.assertTrue(
            any(
                query.startswith("UPDATE tn_wagons SET status")
                and "returned_empty_at" in query
                and "returned_empty_by_operator_id" in query
                for query, _ in cursor.executed
            )
        )

    def test_returned_empty_wagon_reopens_for_loading(self) -> None:
        cursor = _Cursor()
        cursor.wagon_status = "returned_empty"
        connection = _Connection(cursor)
        operator = {"id": 3, "role": "operator1", "name": "Оператор 1"}

        @contextmanager
        def transaction():
            yield connection

        with patch.object(store, "transaction", transaction), patch.object(
            store, "utc_now", return_value=datetime(2026, 1, 1, 12, 0, 0)
        ), patch.object(store, "_ensure_wagon_position"), patch.object(
            store, "_append_event"
        ), patch.object(store, "_append_outbox"):
            result = store.load_block_to_wagon(
                block_id=7, wagon_number="123", dead_end_code="gruzovoy_3", slot_index=8, operator=operator
            )

        self.assertEqual(result["wagon_number"], "123")
        self.assertTrue(
            any(
                query.startswith("UPDATE tn_wagons SET status = 'forming'")
                for query, _ in cursor.executed
            )
        )

    def test_intake_line_in_wagon_requires_own_position(self) -> None:
        line = {
            "block_type": "A", "block_number": "001", "receipt_state": "received",
            "condition_code": "ok", "wagon_number": "123",
        }

        with self.assertRaisesRegex(ValueError, "Укажите тупик"):
            store._intake_line_payload(line, require_location=True)

    def test_unpositioned_forming_wagon_can_receive_position_when_marked_loaded(self) -> None:
        cursor = _Cursor()
        cursor.wagon_status = "forming"
        connection = _Connection(cursor)
        operator = {"id": 3, "role": "operator1", "name": "Оператор 1"}

        @contextmanager
        def transaction():
            yield connection

        with patch.object(store, "transaction", transaction), patch.object(
            store, "utc_now", return_value=datetime(2026, 1, 1, 12, 0, 0)
        ), patch.object(store, "_ensure_wagon_position") as ensure_position, patch.object(
            store, "_append_event"
        ), patch.object(store, "_append_wagon_physical_facts"), patch.object(store, "_append_notification"):
            result = store.mark_wagon_loaded(
                "123", dead_end_code="gruzovoy_1", slot_index=6, operator=operator
            )

        self.assertEqual(result["status"], "loaded")
        self.assertEqual(result["dead_end_code"], "gruzovoy_1")
        self.assertEqual(result["slot_index"], 6)
        ensure_position.assert_called_once()
