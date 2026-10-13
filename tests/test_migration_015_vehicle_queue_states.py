"""Статический контракт миграции 015 без подключения к MySQL.

Тест проверяет исходники, а не применение миграции или совместимость с конкретным
экземпляром MySQL.
"""

from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "migrations" / "taksimo_new" / "015_vehicle_queue_and_trip_states.sql"
DOCUMENTATION = ROOT / "docs" / "schema-queue-states.md"

QUEUE_STATES = {
    "queued",
    "assigned_to_trip",
    "departed",
    "arrived_taksimo",
    "received",
    "returned_to_queue",
    "cancelled",
}
DOCUMENT_STATES = {
    "draft",
    "ttn_issued",
    "ttn_printed",
    "accountant_review",
    "er_sent",
    "approved",
    "documents_printed",
    "handed_to_driver",
    "cancelled",
}
CAPABILITIES = {
    "queue.entry.create",
    "queue.entry.view",
    "queue.entry.cancel",
    "queue.transition",
    "queue.history.view",
}


class VehicleQueueAndTripStatesMigrationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = MIGRATION.read_text(encoding="utf-8")
        cls.normalized_sql = " ".join(cls.sql.split())
        cls.documentation = DOCUMENTATION.read_text(encoding="utf-8")

    def _check_states(self, constraint_name: str) -> set[str]:
        start = self.normalized_sql.index(f"CONSTRAINT {constraint_name} CHECK (")
        constraint_sql = self.normalized_sql[start : start + 1000]
        match = re.search(r"IN \((.*?)\)", constraint_sql)
        self.assertIsNotNone(match, f"Не найдено ограничение {constraint_name}")
        return set(re.findall(r"'([^']+)'", match.group(1)))

    def test_document_status_is_nullable_and_preserves_legacy_status(self) -> None:
        self.assertRegex(
            self.normalized_sql,
            r"ALTER TABLE tn_trips ADD COLUMN document_status VARCHAR\(24\) NULL AFTER status",
        )
        self.assertEqual(
            self._check_states("chk_tn_trips_document_status"),
            DOCUMENT_STATES,
        )
        lowered_sql = self.sql.lower()
        self.assertNotIn("drop check chk_tn_trips_status", lowered_sql)
        self.assertNotIn("modify column status", lowered_sql)
        self.assertNotIn("update tn_trips", lowered_sql)
        self.assertNotIn("insert into tn_trips", lowered_sql)

    def test_creates_queue_projection_with_one_active_cycle_per_vehicle(self) -> None:
        self.assertIn("CREATE TABLE tn_vehicle_queue_entries", self.normalized_sql)
        self.assertEqual(
            self._check_states("chk_tn_vehicle_queue_entries_state"),
            QUEUE_STATES,
        )
        self.assertRegex(
            self.normalized_sql,
            r"active_vehicle_id BIGINT UNSIGNED GENERATED ALWAYS AS "
            r"\( CASE WHEN state IN \('queued', 'assigned_to_trip', 'departed', "
            r"'arrived_taksimo', 'received'\) THEN vehicle_id ELSE NULL END \) STORED",
        )
        self.assertIn(
            "UNIQUE KEY uq_tn_vehicle_queue_entries_active_vehicle (active_vehicle_id)",
            self.normalized_sql,
        )
        self.assertIn(
            "UNIQUE KEY uq_tn_vehicle_queue_entries_public_id (public_id)",
            self.normalized_sql,
        )
        self.assertIn("cancellation_reason VARCHAR(500) NOT NULL DEFAULT ''", self.normalized_sql)

    def test_queue_projection_foreign_keys_have_the_approved_delete_actions(self) -> None:
        expected_foreign_keys = {
            "fk_tn_vehicle_queue_entries_vehicle": "tn_vehicles(id) ON DELETE RESTRICT",
            "fk_tn_vehicle_queue_entries_assigned_trip": "tn_trips(id) ON DELETE SET NULL",
            "fk_tn_vehicle_queue_entries_intake": "tn_intakes(id) ON DELETE SET NULL",
            "fk_tn_vehicle_queue_entries_created_by": "tn_operator_accounts(id) ON DELETE RESTRICT",
            "fk_tn_vehicle_queue_entries_transition_by": "tn_operator_accounts(id) ON DELETE RESTRICT",
            "fk_tn_vehicle_queue_entries_cancelled_by": "tn_operator_accounts(id) ON DELETE RESTRICT",
        }
        for constraint_name, target in expected_foreign_keys.items():
            with self.subTest(constraint=constraint_name):
                self.assertRegex(
                    self.normalized_sql,
                    rf"{constraint_name} FOREIGN KEY \([^)]*\) REFERENCES {re.escape(target)}",
                )

    def test_creates_transition_history_for_initial_and_subsequent_events(self) -> None:
        self.assertIn("CREATE TABLE tn_vehicle_queue_transitions", self.normalized_sql)
        self.assertIn("from_state VARCHAR(24) NULL", self.normalized_sql)
        self.assertIn("to_state VARCHAR(24) NOT NULL", self.normalized_sql)
        self.assertIn("actor_account_id BIGINT UNSIGNED NOT NULL", self.normalized_sql)
        self.assertIn("details_json JSON NULL", self.normalized_sql)
        self.assertEqual(
            self._check_states("chk_tn_vehicle_queue_transitions_from_state"),
            QUEUE_STATES,
        )
        self.assertEqual(
            self._check_states("chk_tn_vehicle_queue_transitions_to_state"),
            QUEUE_STATES,
        )
        self.assertRegex(
            self.normalized_sql,
            r"fk_tn_vehicle_queue_transitions_entry FOREIGN KEY \(queue_entry_id\) "
            r"REFERENCES tn_vehicle_queue_entries\(id\) ON DELETE RESTRICT",
        )
        self.assertRegex(
            self.normalized_sql,
            r"fk_tn_vehicle_queue_transitions_actor FOREIGN KEY \(actor_account_id\) "
            r"REFERENCES tn_operator_accounts\(id\) ON DELETE RESTRICT",
        )

    def test_seeds_exactly_the_approved_queue_capabilities(self) -> None:
        seeded_capabilities = set(
            re.findall(r"SELECT role\.id, '([^']+)'", self.normalized_sql)
        )
        self.assertEqual(seeded_capabilities, CAPABILITIES)
        self.assertNotIn("trip.create", self.sql)

    def test_does_not_add_outbox_idempotency_or_database_mutation_guards(self) -> None:
        lowered_sql = self.sql.lower()
        self.assertNotIn("outbox", lowered_sql)
        self.assertNotIn("idempotency", lowered_sql)
        self.assertNotIn("create trigger", lowered_sql)
        self.assertNotIn("drop table", lowered_sql)
        self.assertNotIn("delete from", lowered_sql)

    def test_documentation_records_all_approved_decisions_and_boundaries(self) -> None:
        self.assertIn("**Статус: APPROVED", self.documentation)
        for decision in ("Q1A", "Q2A", "Q3A", "Q4A", "Q5A", "Q6A", "Q7A", "Q8B"):
            with self.subTest(decision=decision):
                self.assertIn(decision, self.documentation)
        self.assertIn("`tn_notification_outbox` уже создаётся миграцией `005`", self.documentation)
        self.assertIn("FastAPI-outbox остаются отдельной задачей `017`", self.documentation)
        self.assertIn("`document_status` остаётся `NULL`", self.documentation)
        self.assertIn("writer пока отсутствует", self.documentation)
        self.assertIn("неявного соответствия", self.documentation)


if __name__ == "__main__":
    unittest.main()
