"""Статический контракт миграции 016 без подключения к MySQL.

Проверяется только текст исходных SQL и документации. Тест не применяет
миграции, не открывает соединение с MySQL и не доказывает состояние целевой БД.
"""

from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "migrations" / "taksimo_new" / "016_audit_and_idempotency.sql"
TRIGGERS = ROOT / "migrations" / "taksimo_new" / "016_audit_and_idempotency_triggers.sql"
DOCUMENTATION = ROOT / "docs" / "schema-audit-idempotency.md"


class AuditAndIdempotencyMigrationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = MIGRATION.read_text(encoding="utf-8")
        cls.normalized_sql = " ".join(cls.sql.split())
        cls.triggers = TRIGGERS.read_text(encoding="utf-8")
        cls.normalized_triggers = " ".join(cls.triggers.split())
        cls.documentation = DOCUMENTATION.read_text(encoding="utf-8")

    def test_evolves_the_existing_action_log_with_actor_snapshots(self) -> None:
        self.assertNotIn("CREATE TABLE tn_action_log", self.normalized_sql)
        self.assertRegex(
            self.normalized_sql,
            r"ALTER TABLE tn_action_log ADD COLUMN actor_display_name VARCHAR\(120\) NULL AFTER role_code, "
            r"ADD COLUMN capability_code VARCHAR\(80\) NULL AFTER action, "
            r"ADD COLUMN idempotency_id BIGINT UNSIGNED NULL AFTER subject_id",
        )
        self.assertIn("KEY idx_tn_action_log_idempotency (idempotency_id)", self.normalized_sql)
        self.assertRegex(
            self.normalized_sql,
            r"fk_tn_action_log_idempotency FOREIGN KEY \(idempotency_id\) "
            r"REFERENCES tn_command_idempotency\(id\) ON DELETE RESTRICT",
        )
        self.assertIn("Исторические записи tn_action_log не переписываются", self.sql)

    def test_creates_durable_idempotency_storage_with_the_approved_scope(self) -> None:
        self.assertIn("CREATE TABLE tn_command_idempotency", self.normalized_sql)
        for column in (
            "actor_account_id BIGINT UNSIGNED NOT NULL",
            "operation_code VARCHAR(80) NOT NULL",
            "idempotency_key CHAR(36) NOT NULL",
            "fingerprint_algorithm VARCHAR(40) NOT NULL",
            "fingerprint_version VARCHAR(40) NOT NULL",
            "request_fingerprint VARCHAR(128) NOT NULL",
            "status VARCHAR(16) NOT NULL",
            "initial_response_status TINYINT UNSIGNED NULL",
            "response_body_json JSON NULL",
            "completed_at DATETIME(6) NULL",
        ):
            with self.subTest(column=column):
                self.assertIn(column, self.normalized_sql)

        self.assertRegex(
            self.normalized_sql,
            r"UNIQUE KEY uq_tn_command_idempotency_scope \( actor_account_id, operation_code, idempotency_key \)",
        )
        self.assertRegex(
            self.normalized_sql,
            r"fk_tn_command_idempotency_actor FOREIGN KEY \(actor_account_id\) "
            r"REFERENCES tn_operator_accounts\(id\) ON DELETE RESTRICT",
        )
        self.assertRegex(
            self.normalized_sql,
            r"REGEXP_LIKE\( idempotency_key, '\^\[0-9a-f\]\{8\}-.+\$', 'c' \)",
        )

    def test_schema_allows_only_pending_without_a_result_or_completed_201(self) -> None:
        self.assertIn(
            "chk_tn_command_idempotency_status CHECK (status IN ('pending', 'completed'))",
            self.normalized_sql,
        )
        self.assertRegex(
            self.normalized_sql,
            r"chk_tn_command_idempotency_result CHECK \( \(status = 'pending' "
            r"AND initial_response_status IS NULL AND response_body_json IS NULL "
            r"AND completed_at IS NULL\) OR \(status = 'completed' "
            r"AND initial_response_status = 201 AND response_body_json IS NOT NULL "
            r"AND completed_at IS NOT NULL\) \)",
        )

    def test_triggers_make_audit_and_queue_history_append_only(self) -> None:
        expected_triggers = {
            "tn_action_log_no_update": ("UPDATE", "tn_action_log"),
            "tn_action_log_no_delete": ("DELETE", "tn_action_log"),
            "tn_vehicle_queue_transitions_no_update": ("UPDATE", "tn_vehicle_queue_transitions"),
            "tn_vehicle_queue_transitions_no_delete": ("DELETE", "tn_vehicle_queue_transitions"),
            "tn_vehicle_queue_entries_no_delete": ("DELETE", "tn_vehicle_queue_entries"),
        }
        for trigger_name, (event, table_name) in expected_triggers.items():
            with self.subTest(trigger=trigger_name):
                self.assertRegex(
                    self.normalized_triggers,
                    rf"CREATE TRIGGER {trigger_name} BEFORE {event} ON {table_name}",
                )

        self.assertNotIn("tn_vehicle_queue_entries_no_update", self.triggers)

    def test_idempotency_triggers_allow_exactly_one_safe_transition(self) -> None:
        self.assertRegex(
            self.normalized_triggers,
            r"CREATE TRIGGER tn_command_idempotency_pending_insert_only BEFORE INSERT "
            r"ON tn_command_idempotency",
        )
        self.assertIn("NEW.status = 'pending'", self.normalized_triggers)
        self.assertIn("NEW.initial_response_status IS NULL", self.normalized_triggers)
        self.assertIn("NEW.response_body_json IS NULL", self.normalized_triggers)
        self.assertIn("NEW.completed_at IS NULL", self.normalized_triggers)

        transition = self._trigger_sql("tn_command_idempotency_pending_to_completed_only")
        for condition in (
            "OLD.status = 'pending'",
            "NEW.status = 'completed'",
            "NEW.initial_response_status = 201",
            "NEW.response_body_json IS NOT NULL",
            "NEW.completed_at IS NOT NULL",
            "NEW.completed_at >= OLD.created_at",
            "NEW.actor_account_id <=> OLD.actor_account_id",
            "NEW.operation_code <=> OLD.operation_code",
            "NEW.idempotency_key <=> OLD.idempotency_key",
            "NEW.fingerprint_algorithm <=> OLD.fingerprint_algorithm",
            "NEW.fingerprint_version <=> OLD.fingerprint_version",
            "NEW.request_fingerprint <=> OLD.request_fingerprint",
            "NEW.created_at <=> OLD.created_at",
        ):
            with self.subTest(condition=condition):
                self.assertIn(condition, transition)

        self.assertRegex(
            self.normalized_triggers,
            r"CREATE TRIGGER tn_command_idempotency_no_delete BEFORE DELETE "
            r"ON tn_command_idempotency",
        )

    def test_does_not_repurpose_inbox_or_outbox_or_register_runtime(self) -> None:
        combined_sql = f"{self.sql}\n{self.triggers}".lower()
        self.assertNotIn("integration_inbox", combined_sql)
        self.assertNotIn("integration_outbox", combined_sql)
        self.assertNotIn("notification_outbox", combined_sql)
        self.assertNotIn("create_app", combined_sql)
        self.assertNotIn("fotonych-bot", combined_sql)

    def test_documentation_records_all_approved_decisions_and_boundaries(self) -> None:
        self.assertIn("**Статус: APPROVED", self.documentation)
        for decision in (
            "A16-01A",
            "A16-02A",
            "A16-03A",
            "A16-04A",
            "A16-05A",
            "A16-06A",
            "A16-07A",
        ):
            with self.subTest(decision=decision):
                self.assertIn(decision, self.documentation)
        self.assertIn("source-only", self.documentation)
        self.assertIn("NotImplementedTripRepository", self.documentation)
        self.assertIn("регистрация `POST /api/v1/dispatcher/trips`", self.documentation)
        self.assertIn("FastAPI-outbox и доставка остаются отдельной\nзадачей `017`", self.documentation)

    def _trigger_sql(self, name: str) -> str:
        pattern = rf"CREATE TRIGGER {name}\s+.*?END//"
        match = re.search(pattern, self.triggers, flags=re.DOTALL)
        self.assertIsNotNone(match, f"Не найден триггер {name}")
        return " ".join(match.group(0).split())


if __name__ == "__main__":
    unittest.main()
