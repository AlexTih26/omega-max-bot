"""Проверки справочников администратора и журнала действий без MySQL."""

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


ADMIN = {"id": 1, "role": "admin", "name": "Администратор"}
OPERATOR = {"id": 3, "role": "operator1", "name": "Оператор 1"}
DISPATCHER = {"id": 2, "role": "dispatcher", "name": "Диспетчер"}


class _Cursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, object]] = []
        self.lastrowid = 0
        self._row: dict | None = None

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        return None

    def execute(self, statement: str, parameters: object = None) -> None:
        query = " ".join(statement.split())
        self.executed.append((query, parameters))
        self._row = None
        if query.startswith("SELECT COALESCE(MAX(sort_order)"):
            self._row = {"next_order": 8}
        elif query.startswith("INSERT INTO tn_block_types"):
            self.lastrowid = 1

    def fetchone(self) -> dict | None:
        return self._row

    def fetchall(self) -> list[dict]:
        return []


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _Cursor:
        return self._cursor


class CatalogRoleTests(unittest.TestCase):
    def _run(self, function, operator, **kwargs):
        cursor = _Cursor()
        connection = _Connection(cursor)

        @contextmanager
        def transaction():
            yield connection

        with patch.object(store, "transaction", transaction), patch.object(
            store, "utc_now", return_value=datetime(2026, 1, 1, 12, 0, 0)
        ):
            return function(operator=operator, **kwargs), cursor

    def test_admin_can_save_block_type(self) -> None:
        result, cursor = self._run(
            store.save_block_type, ADMIN, letter="a", weight_kg=7780, price_rub=1200.5
        )
        self.assertEqual(result["letter"], "A")
        self.assertEqual(result["weight_kg"], 7780)
        self.assertEqual(result["price_rub"], 1200.5)
        self.assertTrue(any("INSERT INTO tn_action_log" in query for query, _ in cursor.executed))

    def test_operator_cannot_change_catalog(self) -> None:
        with self.assertRaises(PermissionError):
            self._run(store.save_block_type, OPERATOR, letter="A", weight_kg=7780)

    def test_dispatcher_cannot_change_catalog(self) -> None:
        with self.assertRaises(PermissionError):
            self._run(store.save_vehicle, DISPATCHER, plate="А123ВС75")

    def test_block_type_rejects_weight_from_non_catalog_letter(self) -> None:
        with self.assertRaises(ValueError):
            self._run(store.save_block_type, ADMIN, letter="Z", weight_kg=100)

    def test_carrier_requires_valid_inn(self) -> None:
        with self.assertRaises(ValueError):
            self._run(store.save_carrier, ADMIN, name="ООО Тест", inn="123", legal_form="ООО")

    def test_carrier_accepts_ten_digit_inn(self) -> None:
        result, _ = self._run(
            store.save_carrier, ADMIN, name="ООО Тест", inn="2460123456", legal_form="ООО"
        )
        self.assertEqual(result["inn"], "2460123456")

    def test_vehicle_action_is_written_to_journal(self) -> None:
        _, cursor = self._run(store.save_vehicle, ADMIN, plate="А123ВС75")
        journal = [query for query, _ in cursor.executed if "INSERT INTO tn_action_log" in query]
        self.assertEqual(len(journal), 1)
        parameters = next(parameters for query, parameters in cursor.executed if "INSERT INTO tn_action_log" in query)
        self.assertEqual(parameters[1], "admin")
        self.assertEqual(parameters[3], "vehicle")

    def test_block_types_are_read_from_own_table(self) -> None:
        cursor = _Cursor()
        connection = _Connection(cursor)

        @contextmanager
        def transaction():
            yield connection

        with patch.object(store, "transaction", transaction):
            store.block_types()
        self.assertTrue(any(query.startswith("SELECT letter, weight_kg") for query, _ in cursor.executed))

    def test_role_codes_cover_four_roles_and_eight_accounts(self) -> None:
        self.assertEqual(
            set(store.ROLE_CODES),
            {
                "admin", "dispatcher",
                "accountant1", "accountant2", "accountant3",
                "operator1", "operator2", "operator3",
            },
        )
        self.assertEqual(store.OPERATOR_ROLES, {"operator1", "operator2", "operator3"})
        self.assertEqual(store.ADMIN_ROLES, {"admin"})

    def test_operator_entry_still_works_when_admin_account_exists(self) -> None:
        class RolesCursor(_Cursor):
            def fetchall(self) -> list[dict]:
                return [{"role_code": role} for role in ("admin", "operator1", "operator2", "operator3")]

        cursor = RolesCursor()
        connection = _Connection(cursor)

        @contextmanager
        def transaction():
            yield connection

        with patch.object(store, "transaction", transaction):
            self.assertTrue(store.operator_accounts_ready())


if __name__ == "__main__":
    unittest.main()
