"""Статический контракт миграции 014 без подключения к MySQL.

Этот тест проверяет исходный текст миграции, а не доказывает её применение или
совместимость с конкретным сервером MySQL.
"""

from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "migrations" / "taksimo_new" / "014_accounts_and_permissions.sql"
TRIPS_SCHEMA = ROOT / "docs" / "schema-trips.md"


class AccountsAndPermissionsMigrationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = MIGRATION.read_text(encoding="utf-8")
        cls.normalized_sql = " ".join(cls.sql.split())
        cls.documentation = TRIPS_SCHEMA.read_text(encoding="utf-8")

    def test_creates_canonical_roles_and_permission_tables(self) -> None:
        for table_name in ("tn_roles", "tn_role_permissions", "tn_account_permissions"):
            with self.subTest(table=table_name):
                self.assertRegex(
                    self.normalized_sql,
                    rf"CREATE TABLE IF NOT EXISTS {table_name} ",
                )

        self.assertIn("id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT", self.normalized_sql)
        self.assertIn("UNIQUE KEY uq_tn_roles_code (code)", self.normalized_sql)
        self.assertRegex(
            self.normalized_sql,
            r"CHECK \(code IN \('admin', 'dispatcher', 'accountant', 'operator'\)\)",
        )

    def test_preserves_legacy_role_code_as_a_unique_compatibility_alias(self) -> None:
        self.assertIn("role_code сохраняется NOT NULL уникальным compatibility alias", self.sql)
        self.assertIn("DROP CHECK chk_tn_operator_accounts_role", self.normalized_sql)
        self.assertIn("DROP INDEX uq_tn_operator_accounts_role", self.normalized_sql)
        self.assertIn(
            "ADD UNIQUE KEY uq_tn_operator_accounts_legacy_code (role_code)",
            self.normalized_sql,
        )
        self.assertNotRegex(self.normalized_sql, r"DROP COLUMN\s+role_code")

    def test_backfills_each_legacy_account_code_to_its_canonical_role(self) -> None:
        expected_mapping = {
            "admin": "admin",
            "dispatcher": "dispatcher",
            "accountant1": "accountant",
            "accountant2": "accountant",
            "accountant3": "accountant",
            "operator1": "operator",
            "operator2": "operator",
            "operator3": "operator",
        }

        for legacy_code, canonical_code in expected_mapping.items():
            with self.subTest(legacy_code=legacy_code):
                self.assertRegex(
                    self.normalized_sql,
                    rf"WHEN '{legacy_code}' THEN '{canonical_code}'",
                )

        self.assertIn("MODIFY COLUMN role_id BIGINT UNSIGNED NOT NULL", self.normalized_sql)
        self.assertRegex(
            self.normalized_sql,
            r"fk_tn_operator_accounts_role FOREIGN KEY \(role_id\) "
            r"REFERENCES tn_roles\(id\) ON DELETE RESTRICT",
        )

    def test_seeds_only_observed_trip_create_for_admin_and_dispatcher(self) -> None:
        self.assertEqual(self.sql.count("'trip.create'"), 1)
        self.assertRegex(
            self.normalized_sql,
            r"SELECT role\.id, 'trip\.create', 1, 1, 0, UTC_TIMESTAMP\(6\), UTC_TIMESTAMP\(6\) "
            r"FROM tn_roles AS role WHERE role\.code IN \('admin', 'dispatcher'\) "
            r"ON DUPLICATE KEY UPDATE role_id = role_id",
        )

    def test_permission_foreign_keys_restrict_deletion(self) -> None:
        for constraint_name in (
            "fk_tn_role_permissions_role",
            "fk_tn_account_permissions_account",
        ):
            with self.subTest(constraint=constraint_name):
                self.assertRegex(
                    self.normalized_sql,
                    rf"{constraint_name} FOREIGN KEY .*? ON DELETE RESTRICT",
                )

    def test_does_not_add_replay_storage_or_mutate_trips(self) -> None:
        lowered_sql = self.sql.lower()
        self.assertNotIn("idempotency", lowered_sql)
        self.assertNotIn("tn_trips", lowered_sql)
        self.assertNotIn("drop table", lowered_sql)
        self.assertNotIn("create trigger", lowered_sql)

    def test_documentation_keeps_the_endpoint_gated_by_016(self) -> None:
        self.assertIn("до миграции `016`", self.documentation)
        self.assertIn("только в миграции `016`", self.documentation)
        self.assertIn("Миграция `014` только готовит канонические роли и права", self.documentation)


if __name__ == "__main__":
    unittest.main()
