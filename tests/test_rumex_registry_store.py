"""Tests for the isolated, permanent RUMEX shipment registry."""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
BOT = ROOT / "fotonych-bot"
if str(BOT) not in sys.path:
    sys.path.insert(0, str(BOT))

import rumex_registry_store as store  # noqa: E402
import rumex_registry_backup as backup  # noqa: E402


class RumexRegistryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._old_db_path = store.DB_PATH
        store.DB_PATH = Path(self._tmpdir.name) / "rumex_registry.db"

    def tearDown(self) -> None:
        store.DB_PATH = self._old_db_path
        self._tmpdir.cleanup()

    def _create_shipment(self, *, year: int = 2026, number: str = "101") -> dict:
        return store.create_shipment(
            registry_year=year,
            items=[{"letter": "A", "number": number}],
            dispatcher_max_user_id=42,
            dispatcher_name="Диспетчер РУМЕКС",
            created_at=1_767_225_600.0,
        )

    def test_init_creates_wal_database_and_required_tables(self) -> None:
        store.init_rumex_registry_db()

        self.assertTrue(store.DB_PATH.is_file())
        with sqlite3.connect(store.DB_PATH) as conn:
            journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            tables = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }

        self.assertEqual(journal_mode.lower(), "wal")
        self.assertTrue(
            {
                "organizations",
                "document_profiles",
                "carriers",
                "drivers",
                "vehicles",
                "document_fleet_vehicles",
                "document_vehicle_bindings",
                "test_shipments",
                "test_shipment_revisions",
                "test_shipment_items",
                "test_shipment_documents",
                "test_ttn_number_sequences",
                "rumex_test_shipment_events",
                "block_types",
                "shipments",
                "shipment_items",
                "shipment_documents",
                "rumex_audit_events",
                "accountant_login_attempts",
                "accountant_sessions",
            }.issubset(tables)
        )

    def test_active_sessions_extend_expiration(self) -> None:
        store.create_accountant_session(
            token_hash="accountant-token",
            username="Бухгалтер 1",
            expires_at=200.0,
            created_at=100.0,
        )
        store.create_test_dispatcher_session(
            token_hash="dispatcher-token",
            username="Тестовый диспетчер",
            expires_at=200.0,
            created_at=100.0,
        )

        self.assertEqual(
            store.accountant_session_username(
                token_hash="accountant-token", now=150.0, renewal_seconds=604800
            ),
            "Бухгалтер 1",
        )
        self.assertEqual(
            store.test_dispatcher_session_username(
                token_hash="dispatcher-token", now=150.0, renewal_seconds=43200
            ),
            "Тестовый диспетчер",
        )
        with sqlite3.connect(store.DB_PATH) as conn:
            accountant = conn.execute(
                "SELECT expires_at, last_seen_at FROM accountant_sessions WHERE token_hash = ?",
                ("accountant-token",),
            ).fetchone()
            dispatcher = conn.execute(
                "SELECT expires_at, last_seen_at FROM test_dispatcher_sessions WHERE token_hash = ?",
                ("dispatcher-token",),
            ).fetchone()

        self.assertEqual(accountant, (604950.0, 150.0))
        self.assertEqual(dispatcher, (43350.0, 150.0))

    def test_block_catalog_is_created_and_can_be_confirmed(self) -> None:
        blocks = {item["code"]: item for item in store.list_block_types(active_only=True)}

        self.assertEqual(set(blocks), set(store.BLOCK_CODES))
        self.assertEqual(blocks["A"]["nominal_weight_kg"], 7780)
        self.assertFalse(blocks["A"]["weight_confirmed"])
        self.assertEqual(blocks["K"]["nominal_weight_tonnes"], 3.85)

        updated = store.save_block_type(
            "A",
            nominal_weight_kg=7800,
            weight_confirmed=True,
            source_note="Подтверждено бухгалтерией",
        )
        self.assertEqual(updated["nominal_weight_kg"], 7800)
        self.assertTrue(updated["weight_confirmed"])

    def test_carrier_requires_confirmed_details_and_keeps_shipment_snapshot(self) -> None:
        with self.assertRaisesRegex(ValueError, "подтверждённый источник"):
            store.save_carrier(
                name="ООО «Подтверждённый перевозчик»",
                inn="1234567890",
                kpp="123456789",
                legal_address="г. Иркутск, ул. Пример, д. 1",
                confirmation_source="",
                confirmation_reference="Карточка от 01.01.2026",
                actor_name="Бухгалтер 1",
            )

        carrier = store.save_carrier(
            name="ООО «Подтверждённый перевозчик»",
            inn="1234567890",
            kpp="123456789",
            legal_address="г. Иркутск, ул. Пример, д. 1",
            confirmation_source="counterparty_card",
            confirmation_reference="Карточка контрагента от 01.01.2026",
            actor_name="Бухгалтер 1",
            occurred_at=1_767_225_600.0,
        )
        self.assertEqual(carrier["confirmation_source"], "counterparty_card")
        self.assertTrue(carrier["active"])

        shipment = store.create_shipment(
            registry_year=2026,
            items=[{"letter": "A", "number": "104"}],
            carrier_id=carrier["id"],
            dispatcher_name="Диспетчер",
        )
        updated = store.save_carrier(
            carrier_id=carrier["id"],
            name="ООО «Подтверждённый перевозчик»",
            inn="1234567890",
            kpp="123456789",
            legal_address="г. Иркутск, ул. Новая, д. 2",
            confirmation_source="kontur",
            confirmation_reference="Контур: карточка от 02.01.2026",
            actor_name="Бухгалтер 1",
            active=False,
        )
        self.assertFalse(updated["active"])
        self.assertEqual(store.list_carriers(active_only=True), [])
        self.assertEqual(
            json.loads(store.get_shipment(shipment["id"])["carrier_snapshot_json"])["legal_address"],
            "г. Иркутск, ул. Пример, д. 1",
        )
        with sqlite3.connect(store.DB_PATH) as conn:
            events = conn.execute(
                "SELECT event_type FROM rumex_audit_events WHERE shipment_id IS NULL ORDER BY id"
            ).fetchall()
        self.assertEqual([event[0] for event in events], ["carrier_created", "carrier_updated"])

    def test_document_vehicle_binding_is_read_only_snapshot_with_permanent_history(self) -> None:
        source_path = Path(self._tmpdir.name) / "drivers_registry.json"
        source = {
            "drivers": [
                {
                    "max_user_id": 42,
                    "plate_tail": "553",
                    "name": "Иванов Иван Иванович",
                    "vehicle": "FAW J6",
                    "taksimo_plate": "К553НХ 138",
                    "active": True,
                }
            ]
        }
        source_path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
        source_before = source_path.read_text(encoding="utf-8")
        carrier = store.save_carrier(
            name="ООО «Перевозчик для машины»",
            inn="1234567890",
            kpp="123456789",
            legal_address="г. Иркутск, ул. Пример, д. 1",
            confirmation_source="counterparty_card",
            confirmation_reference="Карточка от 01.09.2026",
            actor_name="Бухгалтер 1",
        )

        imported = store.import_document_fleet_snapshot(
            accountant_name="Бухгалтер 1",
            source_path=source_path,
            occurred_at=1_767_225_600.0,
        )
        self.assertEqual(source_path.read_text(encoding="utf-8"), source_before)
        self.assertEqual(len(imported), 1)
        self.assertEqual(imported[0]["plate_tail"], "553")
        self.assertEqual(imported[0]["full_plate"], "К553НХ 138")
        self.assertIsNone(imported[0]["document_binding"])

        first = store.confirm_document_vehicle_binding(
            plate_tail="553",
            carrier_id=carrier["id"],
            driver_full_name="Иванов Иван Иванович",
            driver_license_number="38 12 123456",
            accountant_name="Бухгалтер 1",
            checked_at=1_767_225_700.0,
        )
        self.assertEqual(first["status"], "confirmed")
        self.assertEqual(first["full_plate_snapshot"], "К553НХ 138")
        self.assertEqual(first["carrier_snapshot"]["inn"], "1234567890")
        self.assertEqual(first["driver_license_number"], "38 12 123456")

        source["drivers"][0]["vehicle"] = "FAW J7"
        source["drivers"][0]["taksimo_plate"] = "К553НХ 138 (новый снимок)"
        source_path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
        store.import_document_fleet_snapshot(
            accountant_name="Бухгалтер 1",
            source_path=source_path,
            occurred_at=1_767_225_800.0,
        )
        second = store.confirm_document_vehicle_binding(
            plate_tail="553",
            carrier_id=carrier["id"],
            driver_full_name="Иванов Иван Иванович",
            driver_license_number="38 12 123456",
            accountant_name="Бухгалтер 1",
            checked_at=1_767_225_900.0,
        )

        vehicle = store.get_document_fleet_vehicle("553")
        assert vehicle is not None
        self.assertEqual(vehicle["model"], "FAW J7")
        self.assertEqual(vehicle["document_binding"]["id"], second["id"])
        history = store.list_document_vehicle_binding_history("553")
        self.assertEqual([row["id"] for row in history], [second["id"], first["id"]])
        self.assertEqual(history[1]["model_snapshot"], "FAW J6")

        with sqlite3.connect(store.DB_PATH) as conn:
            with self.assertRaisesRegex(sqlite3.DatabaseError, "нельзя изменять"):
                conn.execute(
                    "UPDATE document_vehicle_bindings SET driver_full_name = 'Изменён' WHERE id = ?",
                    (first["id"],),
                )
            with self.assertRaisesRegex(sqlite3.DatabaseError, "нельзя удалять"):
                conn.execute(
                    "DELETE FROM document_vehicle_bindings WHERE id = ?", (first["id"],)
                )

    def test_test_shipment_flow_assigns_immutable_ttn_number_and_keeps_revisions_forever(self) -> None:
        source_path = Path(self._tmpdir.name) / "drivers_registry.json"
        source_path.write_text(
            json.dumps(
                {
                    "drivers": [
                        {
                            "max_user_id": 42,
                            "plate_tail": "553",
                            "name": "Иванов Иван Иванович",
                            "vehicle": "FAW J6",
                            "taksimo_plate": "К553НХ 138",
                            "active": True,
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        carrier = store.save_carrier(
            name="ООО «Тестовый перевозчик»",
            inn="1234567890",
            kpp="123456789",
            legal_address="г. Иркутск, ул. Тестовая, д. 1",
            confirmation_source="counterparty_card",
            confirmation_reference="Карточка от 01.09.2026",
            actor_name="Бухгалтер 1",
        )
        store.import_document_fleet_snapshot(
            accountant_name="Бухгалтер 1", source_path=source_path
        )
        store.confirm_document_vehicle_binding(
            plate_tail="553",
            carrier_id=carrier["id"],
            driver_full_name="Иванов Иван Иванович",
            driver_license_number="38 12 123456",
            accountant_name="Бухгалтер 1",
        )
        existing = self._create_shipment(number="100")

        shipment = store.create_test_shipment(
            plate_tail="553",
            block_count=3,
            items=[
                {"letter": "A", "number": "3611"},
                {"letter": "K", "number": "7741"},
                {"letter": "A", "number": "3612"},
            ],
            dispatcher_max_user_id=9001,
            dispatcher_name="Диспетчер теста",
            loaded_at=1_767_225_600.0,
            registry_year=2026,
            created_at=1_767_166_900.0,
        )
        self.assertEqual(existing["registry_number"], "РМ-2026-000001")
        self.assertEqual(shipment["registry_number"], "РМ-2026-000002")
        self.assertEqual(shipment["status"], "awaiting_accountant_review")
        self.assertEqual(shipment["total_weight_kg"], 19410)
        self.assertEqual(shipment["document_snapshot"]["vehicle"]["full_plate"], "К553НХ 138")
        self.assertEqual(shipment["document_snapshot"]["driver"]["full_name"], "Иванов Иван Иванович")
        self.assertEqual(shipment["document_snapshot"]["driver"]["license_number"], "38 12 123456")
        self.assertEqual(shipment["documents"], [])
        self.assertTrue(shipment["is_new_for_accountant"])
        history = store.list_test_block_history("A", "3611")
        self.assertEqual(history[0]["registry_number"], shipment["registry_number"])

        returned = store.return_test_shipment_for_correction(
            shipment["id"],
            accountant_name="Бухгалтер 2",
            reason="Исправьте номер блока и фактическое время погрузки",
            occurred_at=1_767_225_800.0,
        )
        self.assertEqual(returned["status"], "requires_correction")
        self.assertEqual(returned["correction_reason"], "Исправьте номер блока и фактическое время погрузки")
        self.assertFalse(returned["is_new_for_accountant"])

        corrected = store.resubmit_test_shipment(
            shipment["id"],
            block_count=3,
            items=[
                {"letter": "A", "number": "51151"},
                {"letter": "K", "number": "7741"},
                {"letter": "A", "number": "3612"},
            ],
            loaded_at=1_767_228_000.0,
            dispatcher_max_user_id=9001,
            dispatcher_name="Диспетчер теста",
            occurred_at=1_767_228_100.0,
        )
        self.assertEqual(corrected["revision_number"], 2)
        self.assertEqual(corrected["status"], "awaiting_accountant_review")
        self.assertFalse(corrected["is_new_for_accountant"])
        self.assertEqual([item["block_number"] for item in corrected["items"]], ["51151", "7741", "3612"])
        self.assertEqual(store.list_test_block_history("A", "3611")[0]["revision_number"], 1)

        reviewed = store.review_test_shipment(
            shipment["id"], accountant_name="Бухгалтер 2", er_required=True,
            occurred_at=1_767_228_200.0,
        )
        self.assertEqual(reviewed["status"], "awaiting_er_sent")
        self.assertFalse(reviewed["is_new_for_accountant"])
        self.assertEqual(reviewed["ttn_number"], "ТТН №РМ-2026-000001")
        self.assertEqual(reviewed["ttn_year"], 2026)
        self.assertEqual(reviewed["ttn_sequence"], 1)
        self.assertEqual(
            {document["document_kind"]: document["status"] for document in reviewed["documents"]},
            {"ER": "draft", "TN": "draft"},
        )
        self.assertEqual(
            {document["document_kind"]: document["registry_number"] for document in reviewed["documents"]}["TN"],
            "ТТН №РМ-2026-000001",
        )

        ready = store.mark_test_er_sent_to_kontur(
            shipment["id"],
            accountant_name="Бухгалтер 2",
            external_reference="Контур-777",
            occurred_at=1_767_228_300.0,
        )
        self.assertEqual(ready["status"], "documents_ready")
        self.assertFalse(ready["is_new_for_accountant"])
        self.assertEqual(
            {document["document_kind"]: document["status"] for document in ready["documents"]},
            {"ER": "sent_to_kontur", "TN": "ready"},
        )

        downloaded = store.mark_test_ttn_downloaded(
            shipment["id"],
            dispatcher_max_user_id=9001,
            dispatcher_name="Диспетчер теста",
            occurred_at=1_767_228_350.0,
        )
        self.assertEqual(downloaded["status"], "documents_ready")
        self.assertEqual(downloaded["ttn_printed_by_name"], "Диспетчер теста")
        self.assertEqual(downloaded["ttn_printed_at"], 1_767_228_350.0)

        handed = store.confirm_test_documents_handed_to_driver(
            shipment["id"],
            dispatcher_max_user_id=9001,
            dispatcher_name="Диспетчер теста",
            occurred_at=1_767_228_400.0,
        )
        self.assertEqual(handed["status"], "documents_handed_to_driver")
        self.assertEqual(handed["printed_by_name"], "Диспетчер теста")
        self.assertEqual(
            {document["document_kind"]: document["status"] for document in handed["documents"]},
            {"ER": "issued", "TN": "issued"},
        )
        self.assertEqual(
            [(revision["revision_number"], revision["revision_kind"]) for revision in handed["revisions"]],
            [(1, "submitted"), (1, "returned_for_correction"), (2, "resubmitted")],
        )
        self.assertEqual(
            [event["event_type"] for event in handed["events"]],
            [
                "test_shipment_loaded",
                "test_shipment_returned_for_correction",
                "test_shipment_resubmitted",
                "test_shipment_reviewed",
                "test_er_sent_to_kontur_documents_opened",
                "test_ttn_downloaded",
                "test_documents_handed_to_driver",
            ],
        )

        overnight = store.create_test_shipment(
            plate_tail="553",
            block_count=3,
            items=[
                {"letter": "A", "number": "8111"},
                {"letter": "K", "number": "8741"},
                {"letter": "A", "number": "8112"},
            ],
            dispatcher_max_user_id=9001,
            dispatcher_name="Диспетчер теста",
            loaded_at=1_767_225_600.0,
            created_at=1_767_225_700.0,
        )
        self.assertEqual(overnight["status"], "documents_ready")
        self.assertTrue(overnight["ttn_number"])
        self.assertEqual(
            {document["document_kind"]: document["status"] for document in overnight["documents"]},
            {"ER": "draft", "TN": "ready"},
        )
        self.assertEqual(overnight["events"][-1]["event_type"], "test_documents_opened_automatically")

        released_overnight = store.confirm_test_documents_handed_to_driver(
            overnight["id"],
            dispatcher_max_user_id=9001,
            dispatcher_name="Диспетчер теста",
            occurred_at=1_767_225_800.0,
        )
        self.assertEqual(released_overnight["status"], "documents_handed_to_driver")
        checked_overnight = store.review_test_shipment(
            overnight["id"], accountant_name="Бухгалтер 2", er_required=True,
            occurred_at=1_767_243_800.0,
        )
        self.assertEqual(checked_overnight["status"], "awaiting_er_sent")
        self.assertEqual(checked_overnight["ttn_number"], overnight["ttn_number"])
        finalized_overnight = store.mark_test_er_sent_to_kontur(
            overnight["id"], accountant_name="Бухгалтер 2", external_reference="Контур-ночь",
            occurred_at=1_767_243_900.0,
        )
        self.assertEqual(finalized_overnight["status"], "documents_ready")
        self.assertTrue(finalized_overnight["kontur_sent_at"])

        with sqlite3.connect(store.DB_PATH) as conn:
            revision_id = handed["revisions"][0]["id"]
            item_id = store.list_test_block_history("A", "3611")[0]["id"]
            with self.assertRaisesRegex(sqlite3.DatabaseError, "Выданный номер ТТН нельзя изменять"):
                conn.execute(
                    "UPDATE test_shipments SET ttn_number = 'ТТН №РМ-2026-000002' WHERE id = ?",
                    (shipment["id"],),
                )
            with self.assertRaisesRegex(sqlite3.DatabaseError, "Выданную ТТН нельзя удалить"):
                conn.execute("DELETE FROM test_shipments WHERE id = ?", (shipment["id"],))
            with self.assertRaisesRegex(sqlite3.DatabaseError, "нельзя изменять"):
                conn.execute("UPDATE test_shipment_revisions SET actor_name = 'x' WHERE id = ?", (revision_id,))
            with self.assertRaisesRegex(sqlite3.DatabaseError, "нельзя удалять"):
                conn.execute("DELETE FROM test_shipment_items WHERE id = ?", (item_id,))

    def test_test_ttn_number_restarts_each_loaded_at_calendar_year(self) -> None:
        source_path = Path(self._tmpdir.name) / "drivers_registry.json"
        source_path.write_text(
            json.dumps(
                {
                    "drivers": [
                        {
                            "max_user_id": 42,
                            "plate_tail": "553",
                            "name": "Иванов Иван Иванович",
                            "vehicle": "FAW J6",
                            "taksimo_plate": "К553НХ 138",
                            "active": True,
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        carrier = store.save_carrier(
            name="ООО «Тестовый перевозчик»",
            inn="1234567890",
            kpp="123456789",
            legal_address="г. Иркутск, ул. Тестовая, д. 1",
            confirmation_source="counterparty_card",
            confirmation_reference="Карточка от 01.09.2026",
            actor_name="Бухгалтер 1",
        )
        store.import_document_fleet_snapshot(
            accountant_name="Бухгалтер 1", source_path=source_path
        )
        store.confirm_document_vehicle_binding(
            plate_tail="553",
            carrier_id=carrier["id"],
            driver_full_name="Иванов Иван Иванович",
            driver_license_number="38 12 123456",
            accountant_name="Бухгалтер 1",
        )

        def create_and_review(*, loaded_at: float, item_number: str) -> dict:
            shipment = store.create_test_shipment(
                plate_tail="553",
                block_count=3,
                items=[
                    {"letter": "A", "number": item_number},
                    {"letter": "K", "number": f"K{item_number}"},
                    {"letter": "A", "number": f"A{item_number}"},
                ],
                dispatcher_max_user_id=9001,
                dispatcher_name="Диспетчер теста",
                loaded_at=loaded_at,
                registry_year=2026,
                created_at=1_767_166_900.0,
            )
            return store.review_test_shipment(
                shipment["id"],
                accountant_name="Бухгалтер 1",
                er_required=False,
                occurred_at=loaded_at + 200.0,
            )

        loaded_in_2026 = create_and_review(loaded_at=1_767_225_600.0, item_number="2026")
        loaded_in_2027 = create_and_review(loaded_at=1_798_761_600.0, item_number="2027")

        self.assertEqual(loaded_in_2026["ttn_number"], "ТТН №РМ-2026-000001")
        self.assertEqual(loaded_in_2026["ttn_sequence"], 1)
        self.assertEqual(loaded_in_2027["ttn_number"], "ТТН №РМ-2027-000001")
        self.assertEqual(loaded_in_2027["ttn_sequence"], 1)
        self.assertEqual(loaded_in_2027["registry_year"], 2026)

    def test_test_shipment_rejects_outside_approved_block_count(self) -> None:
        with self.assertRaisesRegex(ValueError, "от 3 до 6 блоков"):
            store.create_test_shipment(
                plate_tail="553",
                block_count=2,
                items=[{"letter": "A", "number": "1"}, {"letter": "A", "number": "2"}],
                dispatcher_max_user_id=9001,
                dispatcher_name="Диспетчер теста",
                loaded_at=1_767_225_600.0,
            )

    def test_test_shipment_requires_driver_license_in_confirmed_vehicle_binding(self) -> None:
        source_path = Path(self._tmpdir.name) / "drivers_registry.json"
        source_path.write_text(
            json.dumps(
                {
                    "drivers": [
                        {
                            "max_user_id": 42,
                            "plate_tail": "553",
                            "name": "Иванов Иван Иванович",
                            "vehicle": "FAW J6",
                            "taksimo_plate": "К553НХ 138",
                            "active": True,
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        carrier = store.save_carrier(
            name="ООО «Тестовый перевозчик»",
            inn="1234567890",
            kpp="123456789",
            legal_address="г. Иркутск, ул. Тестовая, д. 1",
            confirmation_source="counterparty_card",
            confirmation_reference="Карточка от 01.09.2026",
            actor_name="Бухгалтер 1",
        )
        store.import_document_fleet_snapshot(
            accountant_name="Бухгалтер 1", source_path=source_path
        )
        store.confirm_document_vehicle_binding(
            plate_tail="553",
            carrier_id=carrier["id"],
            driver_full_name="Иванов Иван Иванович",
            driver_license_number="38 12 123456",
            accountant_name="Бухгалтер 1",
        )
        with sqlite3.connect(store.DB_PATH) as conn:
            conn.execute("DROP TRIGGER document_vehicle_bindings_no_update")
            conn.execute("UPDATE document_vehicle_bindings SET driver_license_number = ''")

        with self.assertRaisesRegex(ValueError, "нет номера водительского удостоверения"):
            store.create_test_shipment(
                plate_tail="553",
                block_count=3,
                items=[
                    {"letter": "A", "number": "1"},
                    {"letter": "K", "number": "2"},
                    {"letter": "A", "number": "3"},
                ],
                dispatcher_max_user_id=9001,
                dispatcher_name="Диспетчер теста",
                loaded_at=1_767_225_600.0,
            )

    def test_registry_number_is_unique(self) -> None:
        shipment = self._create_shipment()

        with sqlite3.connect(store.DB_PATH) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    """INSERT INTO shipments (
                           registry_number, registry_year, registry_sequence, status,
                           document_profile_id, profile_snapshot_json, created_at, updated_at
                       ) VALUES (?, ?, ?, 'awaiting_er', 1, '{}', 0, 0)""",
                    (shipment["registry_number"], 2026, 999),
                )

    def test_number_generation_is_atomic_and_creates_linked_documents(self) -> None:
        first = self._create_shipment(number="101")
        second = self._create_shipment(number="102")

        self.assertEqual(first["registry_number"], "РМ-2026-000001")
        self.assertEqual(second["registry_number"], "РМ-2026-000002")
        self.assertEqual(first["status"], "awaiting_er")
        self.assertEqual(first["items"][0]["weight_kg"], 7780)
        self.assertEqual(
            {(doc["document_kind"], doc["display_number"], doc["status"]) for doc in first["documents"]},
            {
                ("ER", "РМ-2026-000001 · ЭР", "draft"),
                ("TN", "РМ-2026-000001 · ТТН", "waiting_er_confirmation"),
            },
        )
        self.assertEqual(first["audit_events"][0]["event_type"], "shipment_created")

    def test_backup_uses_consistent_sqlite_copy(self) -> None:
        self._create_shipment()
        backup_dir = Path(self._tmpdir.name) / "backups"
        with patch.object(backup, "DB_PATH", store.DB_PATH), patch.object(backup, "BACKUP_DIR", backup_dir):
            path = backup.backup_rumex_registry_db(reason="test")

        self.assertIsNotNone(path)
        assert path is not None
        self.assertTrue(path.is_file())
        self.assertEqual(path.with_suffix(".meta.txt").read_text(encoding="utf-8").splitlines()[0], "reason=test")
        with sqlite3.connect(path) as conn:
            self.assertEqual(
                conn.execute("SELECT registry_number FROM shipments").fetchone()[0],
                "РМ-2026-000001",
            )

    def test_er_confirmation_opens_ttn_only_after_sending(self) -> None:
        shipment = self._create_shipment()

        with self.assertRaisesRegex(ValueError, "только после отправки"):
            store.mark_er_confirmed(shipment["id"], accountant_name="Бухгалтер 1")

        sent = store.mark_er_sent(
            shipment["id"],
            accountant_name="Бухгалтер 1",
            external_reference="Контур-123",
        )
        self.assertEqual(sent["status"], "er_sent")
        self.assertEqual(
            next(doc for doc in sent["documents"] if doc["document_kind"] == "ER")["status"],
            "sent_to_kontur",
        )

        confirmed = store.mark_er_confirmed(
            shipment["id"],
            accountant_name="Бухгалтер 1",
            external_reference="Контур-123",
        )
        self.assertEqual(confirmed["status"], "tn_ready")
        self.assertEqual(
            {doc["document_kind"]: doc["status"] for doc in confirmed["documents"]},
            {"ER": "confirmed", "TN": "ready"},
        )
        self.assertEqual(
            [event["event_type"] for event in confirmed["audit_events"]],
            ["shipment_created", "er_sent_to_kontur", "er_confirmed_tn_opened"],
        )

    def test_audit_events_cannot_be_changed_or_deleted(self) -> None:
        shipment = self._create_shipment()
        event_id = shipment["audit_events"][0]["id"]

        with sqlite3.connect(store.DB_PATH) as conn:
            with self.assertRaisesRegex(sqlite3.DatabaseError, "нельзя изменять"):
                conn.execute("UPDATE rumex_audit_events SET event_type = 'changed' WHERE id = ?", (event_id,))
            with self.assertRaisesRegex(sqlite3.DatabaseError, "нельзя удалять"):
                conn.execute("DELETE FROM rumex_audit_events WHERE id = ?", (event_id,))

    def test_managed_dispatcher_and_rumex_only_vehicle_block(self) -> None:
        account = store.create_rumex_dispatcher_account(
            username="dispatcher-1",
            full_name="Диспетчер Первый",
            max_user_id=123,
            password_hash="salt$hash",
            actor_name="Администратор",
        )
        self.assertNotIn("password_hash", account)
        self.assertTrue(account["active"])
        store.create_test_dispatcher_session(
            token_hash="managed-dispatcher", username="dispatcher-1", expires_at=300, created_at=100
        )
        store.update_rumex_dispatcher_account(
            username="dispatcher-1", full_name="Диспетчер Первый", max_user_id=123,
            active=False, actor_name="Администратор",
        )
        self.assertIsNone(store.test_dispatcher_session_username(token_hash="managed-dispatcher", now=150))

        store.set_rumex_vehicle_shipment_block(
            plate_tail="553", blocked=True, reason="Документы на проверке", actor_name="Администратор"
        )
        block = store.rumex_vehicle_shipment_block("553")
        self.assertEqual(block["reason"], "Документы на проверке")
        store.set_rumex_vehicle_shipment_block(
            plate_tail="553", blocked=False, reason="", actor_name="Администратор"
        )
        self.assertIsNone(store.rumex_vehicle_shipment_block("553"))
        self.assertGreaterEqual(len(store.list_rumex_management_audit_events()), 3)


if __name__ == "__main__":
    unittest.main()
