"""Проверки единого входа по ролям и журнала действий без MySQL."""

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

try:
    import taksimo_new_auth as auth  # noqa: E402
except ModuleNotFoundError:
    auth = None

AIOHTTP_AVAILABLE = auth is not None


ADMIN = {"id": 1, "role": "admin", "name": "Администратор"}
DISPATCHER = {"id": 2, "role": "dispatcher", "name": "Диспетчер"}
ACCOUNTANT = {"id": 3, "role": "accountant1", "name": "Бухгалтер 1"}
OPERATOR = {"id": 4, "role": "operator1", "name": "Оператор 1"}


class _Cursor:
    def __init__(self, rows=None) -> None:
        self.executed: list[tuple[str, object]] = []
        self.rows = rows or []
        self._row = None

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        return None

    def execute(self, statement: str, parameters: object = None) -> None:
        query = " ".join(statement.split())
        self.executed.append((query, parameters))
        self._row = self.rows[0] if self.rows else None

    def fetchone(self) -> dict | None:
        return self._row

    def fetchall(self) -> list[dict]:
        return list(self.rows)


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _Cursor:
        return self._cursor


class LoginTests(unittest.TestCase):
    def _lookup(self, rows):
        cursor = _Cursor(rows)
        connection = _Connection(cursor)

        @contextmanager
        def transaction():
            yield connection

        def verifier(pin, encoded):
            return pin == "correct" and encoded == "stored"

        with patch.object(store, "transaction", transaction):
            return store.find_operator_for_login("ivanov", "correct", verifier), cursor

    def test_login_finds_account_by_login_and_pin(self) -> None:
        operator, _ = self._lookup([
            {"id": 4, "role_code": "operator1", "login": "ivanov", "display_name": "Иванов", "pin_hash": "stored"}
        ])
        self.assertEqual(operator["role"], "operator1")
        self.assertEqual(operator["login"], "ivanov")
        self.assertEqual(operator["name"], "Иванов")

    def test_wrong_pin_does_not_return_account(self) -> None:
        cursor = _Cursor([
            {"id": 4, "role_code": "operator1", "login": "ivanov", "display_name": "Иванов", "pin_hash": "stored"}
        ])
        connection = _Connection(cursor)

        @contextmanager
        def transaction():
            yield connection

        with patch.object(store, "transaction", transaction):
            result = store.find_operator_for_login("ivanov", "wrong", lambda pin, encoded: False)
        self.assertIsNone(result)

    def test_malformed_login_is_rejected_without_query(self) -> None:
        cursor = _Cursor([])
        connection = _Connection(cursor)

        @contextmanager
        def transaction():
            yield connection

        with patch.object(store, "transaction", transaction):
            result = store.find_operator_for_login("и в а н", "correct", lambda pin, encoded: True)
        self.assertIsNone(result)
        self.assertEqual(cursor.executed, [])

    def test_login_lookup_query_filters_by_login(self) -> None:
        _, cursor = self._lookup([
            {"id": 4, "role_code": "operator1", "login": "ivanov", "display_name": "Иванов", "pin_hash": "stored"}
        ])
        query, parameters = cursor.executed[0]
        self.assertIn("WHERE login = %s AND active = 1", query)
        self.assertEqual(parameters, ("ivanov",))

    def test_legacy_account_without_login_uses_pin_only(self) -> None:
        cursor = _Cursor([
            {"id": 7, "role_code": "operator1", "login": None, "display_name": "Оператор 1", "pin_hash": "stored"}
        ])
        connection = _Connection(cursor)

        @contextmanager
        def transaction():
            yield connection

        with patch.object(store, "transaction", transaction):
            result = store.find_operator_for_pin("correct", lambda pin, encoded: True)
        self.assertEqual(result["name"], "Оператор 1")
        self.assertIn("login IS NULL", cursor.executed[0][0])


@unittest.skipUnless(auth, "Для проверок прав нужен aiohttp")
class PermissionTests(unittest.TestCase):
    def test_admin_sees_admin_section_but_not_operations(self) -> None:
        permissions = auth._permissions(ADMIN)
        self.assertTrue(permissions["admin"])
        self.assertTrue(permissions["dashboard"])
        self.assertEqual(permissions["reception"], "admin" in auth.SECTION_ROLES["reception"])

    def test_operator_sees_reception_and_corrections(self) -> None:
        permissions = auth._permissions(OPERATOR)
        self.assertTrue(permissions["reception"])
        self.assertTrue(permissions["corrections"])
        self.assertFalse(permissions["admin"])

    def test_dispatcher_sees_yard_and_wagons_but_not_reception(self) -> None:
        permissions = auth._permissions(DISPATCHER)
        self.assertTrue(permissions["yard"])
        self.assertTrue(permissions["wagons"])
        self.assertFalse(permissions["reception"])
        self.assertFalse(permissions["admin"])

    def test_accountant_sees_documents_but_not_yard(self) -> None:
        permissions = auth._permissions(ACCOUNTANT)
        self.assertTrue(permissions["reports"])
        self.assertTrue(permissions["journal"])
        self.assertFalse(permissions["yard"])
        self.assertFalse(permissions["corrections"])

    def test_every_section_names_only_known_roles(self) -> None:
        for section, allowed in auth.SECTION_ROLES.items():
            for role in allowed:
                self.assertIn(role, store.ROLE_CODES, f"{section}: {role}")

    def test_unknown_role_gets_no_permissions(self) -> None:
        permissions = auth._permissions({"id": 9, "role": "wat", "name": "?"})
        self.assertFalse(any(permissions.values()))


class JournalFilterTests(unittest.TestCase):
    def _list(self, **filters):
        cursor = _Cursor([])
        connection = _Connection(cursor)

        @contextmanager
        def transaction():
            yield connection

        with patch.object(store, "transaction", transaction):
            store.list_actions(**filters)
        return cursor.executed[0]

    def test_journal_without_filters_reads_all_actions(self) -> None:
        query, parameters = self._list(limit=50)
        self.assertNotIn("WHERE", query)
        self.assertEqual(parameters, (50,))

    def test_journal_filters_by_role_and_action(self) -> None:
        query, parameters = self._list(limit=10, role="admin", action="auth_login")
        self.assertIn("l.role_code = %s", query)
        self.assertIn("l.action = %s", query)
        self.assertEqual(parameters, ("admin", "auth_login", 10))

    def test_journal_filters_by_subject_type(self) -> None:
        query, parameters = self._list(subject_type="vehicle")
        self.assertIn("l.subject_type = %s", query)
        self.assertEqual(parameters, ("vehicle", 100))

    def test_journal_returns_actor_login_for_audit(self) -> None:
        query, _ = self._list()
        self.assertIn("a.login AS actor_login", query)


class LoginJournalTests(unittest.TestCase):
    def test_successful_login_records_action_with_role(self) -> None:
        cursor = _Cursor([])
        connection = _Connection(cursor)

        @contextmanager
        def transaction():
            yield connection

        with patch.object(store, "transaction", transaction), patch.object(
            store, "utc_now", return_value=datetime(2026, 1, 1, 12, 0, 0)
        ):
            store.record_action(
                operator=OPERATOR, action="auth_login", subject_type="account",
                subject_id="4", details={"login": "ivanov", "role": "operator1"},
                remote_address="10.0.0.1",
            )
        query, parameters = cursor.executed[0]
        self.assertIn("INSERT INTO tn_action_log", query)
        self.assertEqual(parameters[1], "operator1")
        self.assertEqual(parameters[2], "auth_login")
        self.assertIn("ivanov", parameters[5])
        self.assertEqual(parameters[6], "10.0.0.1")


if __name__ == "__main__":
    unittest.main()
