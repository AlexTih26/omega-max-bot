import sqlite3
import sys
import tempfile
import time
import unittest
import os
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fotonych-bot"))

import sklad_master_store as store


class SkladMasterStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_path = store.DB_PATH
        store.DB_PATH = Path(self.temp.name) / "warehouse.db"
        store.init_sklad_master_db()
        self.site1, self.site2 = [x["id"] for x in store.list_sites()[:2]]
        self.material = store.list_materials()[0]["id"]
        self.supplier = store.list_suppliers()[0]["id"]

    def tearDown(self):
        store.DB_PATH = self.old_path
        self.temp.cleanup()

    def receipt(self, quantity=10, key=None):
        return store.record_receipt(
            site_id=self.site1,
            material_id=self.material,
            supplier_id=self.supplier,
            quantity=quantity,
            actor_max_id=1,
            actor_name="Tester",
            idempotency_key=key,
        )

    def test_receipt_is_idempotent(self):
        first = self.receipt(10, "receipt-1")
        second = self.receipt(10, "receipt-1")
        self.assertEqual(first["id"], second["id"])
        balance = next(x for x in store.site_stock(self.site1) if x["id"] == self.material)
        self.assertEqual(balance["balance"], 10)

    def test_batch_receipt_is_atomic_and_idempotent(self):
        second_material = store.list_materials()[1]["id"]
        receipt = store.record_receipt_batch(
            site_id=self.site1,
            supplier_id=self.supplier,
            items=[
                {"material_id": self.material, "quantity": 4},
                {"material_id": second_material, "quantity": 2},
            ],
            actor_max_id=1,
            actor_name="Tester",
            idempotency_key="batch-1",
        )
        replay = store.record_receipt_batch(
            site_id=self.site1,
            supplier_id=self.supplier,
            items=[{"material_id": self.material, "quantity": 999}],
            actor_max_id=1,
            actor_name="Tester",
            idempotency_key="batch-1",
        )
        self.assertEqual(receipt["id"], replay["id"])
        balances = {item["id"]: item["balance"] for item in store.site_stock(self.site1)}
        self.assertEqual((balances[self.material], balances[second_material]), (4, 2))

        with self.assertRaisesRegex(ValueError, "Материал не найден"):
            store.record_receipt_batch(
                site_id=self.site1,
                supplier_id=self.supplier,
                items=[
                    {"material_id": self.material, "quantity": 3},
                    {"material_id": 999999, "quantity": 1},
                ],
                actor_max_id=1,
                actor_name="Tester",
            )
        balances = {item["id"]: item["balance"] for item in store.site_stock(self.site1)}
        self.assertEqual(balances[self.material], 4)

    def test_issue_cannot_make_negative_balance(self):
        self.receipt(2)
        with self.assertRaisesRegex(ValueError, "Недостаточно"):
            store.record_issue(
                site_id=self.site1,
                material_id=self.material,
                quantity=3,
                actor_max_id=1,
                actor_name="Tester",
            )
        balance = next(x for x in store.site_stock(self.site1) if x["id"] == self.material)
        self.assertEqual(balance["balance"], 2)

    def test_stock_alerts_only_when_threshold_status_changes(self):
        store.set_site_material_minimum(
            site_id=self.site1,
            material_id=self.material,
            min_level=5,
            actor_max_id=1,
            actor_name="Admin",
        )
        self.receipt(7)
        warning = store.record_issue(
            site_id=self.site1,
            material_id=self.material,
            quantity=2,
            actor_max_id=1,
            actor_name="Tester",
        )
        critical = store.record_issue(
            site_id=self.site1,
            material_id=self.material,
            quantity=1,
            actor_max_id=1,
            actor_name="Tester",
        )
        still_critical = store.record_issue(
            site_id=self.site1,
            material_id=self.material,
            quantity=1,
            actor_max_id=1,
            actor_name="Tester",
        )
        self.assertEqual(warning["stock_alerts"][0]["status"], "warning")
        self.assertEqual(critical["stock_alerts"][0]["status"], "critical")
        self.assertEqual(still_critical["stock_alerts"], [])

    def test_transfer_is_atomic(self):
        self.receipt(8)
        result = store.record_transfer(
            from_site_id=self.site1,
            to_site_id=self.site2,
            material_id=self.material,
            quantity=5,
            actor_max_id=1,
            actor_name="Admin",
            idempotency_key="transfer-1",
        )
        self.assertNotEqual(result["out_id"], result["in_id"])
        source = next(x for x in store.site_stock(self.site1) if x["id"] == self.material)
        target = next(x for x in store.site_stock(self.site2) if x["id"] == self.material)
        self.assertEqual((source["balance"], target["balance"]), (3, 5))
        with self.assertRaises(ValueError):
            store.record_transfer(
                from_site_id=self.site1,
                to_site_id=self.site2,
                material_id=self.material,
                quantity=4,
                actor_max_id=1,
                actor_name="Admin",
            )
        source = next(x for x in store.site_stock(self.site1) if x["id"] == self.material)
        target = next(x for x in store.site_stock(self.site2) if x["id"] == self.material)
        self.assertEqual((source["balance"], target["balance"]), (3, 5))

    def test_request_lifecycle_and_partial_delivery(self):
        second_material = store.list_materials()[1]["id"]
        request = store.create_request(
            site_id=self.site1,
            items=[
                {"material_id": self.material, "quantity": 10},
                {"material_id": second_material, "quantity": 2},
            ],
            actor_max_id=10,
            actor_name="Master",
        )
        self.assertEqual(request["status"], "submitted")
        request = store.transition_request(
            request_id=request["id"],
            new_status="accepted",
            actor_role="supply",
            actor_max_id=20,
            actor_name="Supply",
        )
        first_item, second_item = request["items"]
        delivery = store.create_delivery(
            request_id=request["id"],
            supplier_id=self.supplier,
            items=[
                {"request_item_id": first_item["id"], "quantity": 6},
                {"request_item_id": second_item["id"], "quantity": 2},
            ],
            expected_delivery_days=3,
            actor_max_id=20,
            actor_name="Supply",
        )
        received = store.receive_delivery(
            delivery_id=delivery["id"],
            items=[
                {"delivery_item_id": delivery["items"][0]["id"], "quantity": 3},
                {"delivery_item_id": delivery["items"][1]["id"], "quantity": 2},
            ],
            actor_max_id=10,
            actor_name="Master",
            confirm_early=True,
        )
        self.assertEqual(received["request"]["status"], "partially_received")
        self.assertEqual(
            [(item["material_id"], item["quantity"]) for item in received["received_now"]],
            [(self.material, 3), (second_material, 2)],
        )
        refreshed_first = received["request"]["items"][0]
        self.assertEqual((refreshed_first["ordered"], refreshed_first["received"],
                          refreshed_first["in_transit"], refreshed_first["remaining"]),
                         (10, 3, 3, 7))
        stock = next(x for x in store.site_stock(self.site1) if x["id"] == self.material)
        self.assertEqual(stock["balance"], 3)
        refreshed = store.get_request(request["id"])
        self.assertEqual(len(refreshed["deliveries"]), 1)
        self.assertEqual(len(refreshed["deliveries"][0]["items"]), 2)

    def test_site_specific_minimum(self):
        store.set_site_material_minimum(
            site_id=self.site1,
            material_id=self.material,
            min_level=5,
            actor_max_id=99,
            actor_name="Admin",
        )
        first = next(x for x in store.site_stock(self.site1) if x["id"] == self.material)
        second = next(x for x in store.site_stock(self.site2) if x["id"] == self.material)
        self.assertEqual(first["min_level"], 5)
        self.assertEqual(first["status"], "critical")
        self.assertEqual(second["min_level"], 0)

    def test_admin_can_rename_and_safely_disable_material(self):
        renamed = store.update_material(
            self.material,
            name="Брус тестовый",
            unit="м",
            actor_max_id=99,
            actor_name="Admin",
        )
        self.assertEqual((renamed["name"], renamed["unit"]), ("Брус тестовый", "м"))
        self.receipt(1)
        with self.assertRaisesRegex(ValueError, "ненулевым остатком"):
            store.update_material(
                self.material,
                active=False,
                actor_max_id=99,
                actor_name="Admin",
            )

        unused_material = store.list_materials()[1]["id"]
        disabled = store.update_material(
            unused_material,
            active=False,
            actor_max_id=99,
            actor_name="Admin",
        )
        self.assertFalse(disabled["active"])
        self.assertNotIn(unused_material, {item["id"] for item in store.list_materials()})
        self.assertIn(
            unused_material,
            {item["id"] for item in store.list_materials(include_inactive=True)},
        )

    def test_role_access_matches_business_roles(self):
        with unittest.mock.patch.dict(os.environ, {
            "MATERIALS_MASTER_MAX_IDS": "101",
            "MATERIALS_SUPPLY_MAX_IDS": "202",
            "DRIVERS_ADMIN_MAX_IDS": "303",
        }, clear=False):
            master = store.get_access(101)
            supply = store.get_access(202)
            admin = store.get_access(303)
        self.assertIn("request_receive", master["allowed_actions"])
        self.assertIn("request_receive", supply["allowed_actions"])
        self.assertIn("request_manage", supply["allowed_actions"])
        self.assertIn("receipt_price", supply["allowed_actions"])
        self.assertIn("roles_manage", admin["allowed_actions"])

    def test_manager_role_and_payment_flow(self):
        with unittest.mock.patch.dict(os.environ, {"MATERIALS_MANAGER_MAX_IDS": "777"}, clear=False):
            manager = store.get_access(777)
        self.assertIn("payment_view", manager["allowed_actions"])
        self.assertNotIn("receipt_price", manager["allowed_actions"])
        receipt = store.record_receipt_batch(
            site_id=self.site1,
            supplier_id=self.supplier,
            items=[{"material_id": self.material, "quantity": 10}],
            actor_max_id=10,
            actor_name="Master",
        )
        line = store.get_receipt(receipt["id"])["items"][0]
        priced = store.price_receipt(
            receipt_id=receipt["id"],
            lines=[{
                "id": line["id"],
                "unit_price": 100,
                "billing_quantity": 10,
                "billing_unit": "шт",
            }],
            actor_max_id=20,
            actor_name="Supply",
        )
        self.assertEqual(priced["payment_status"], "priced")
        sent = store.send_receipt_to_manager(
            receipt_id=receipt["id"],
            actor_max_id=20,
            actor_name="Supply",
        )
        self.assertEqual(sent["payment_status"], "sent")
        self.assertEqual(float(sent["total_amount"]), 1000.0)

    def test_receipt_price_allowed_once(self):
        receipt = store.record_receipt_batch(
            site_id=self.site1,
            supplier_id=self.supplier,
            items=[{"material_id": self.material, "quantity": 5}],
            actor_max_id=10,
            actor_name="Master",
        )
        line = store.get_receipt(receipt["id"])["items"][0]
        store.price_receipt(
            receipt_id=receipt["id"],
            lines=[{
                "id": line["id"],
                "unit_price": 50,
                "billing_quantity": 5,
                "billing_unit": "шт",
            }],
            actor_max_id=20,
            actor_name="Supply",
        )
        with self.assertRaisesRegex(ValueError, "уже сохранены"):
            store.price_receipt(
                receipt_id=receipt["id"],
                lines=[{
                    "id": line["id"],
                    "unit_price": 60,
                    "billing_quantity": 5,
                    "billing_unit": "шт",
                }],
                actor_max_id=20,
                actor_name="Supply",
            )

    def test_list_roles_returns_site_restrictions(self):
        store.set_role(max_id=501, role="master", site_ids=[self.site1], actor_max_id=99, actor_name="Admin")
        roles = store.list_roles()
        item = next(x for x in roles if x["max_id"] == 501)
        self.assertEqual(item["role"], "master")
        self.assertEqual(item["site_ids"], [self.site1])

    def test_list_movements_includes_supplier_and_batch(self):
        second_material = store.list_materials()[1]["id"]
        store.record_receipt_batch(
            site_id=self.site1,
            supplier_id=self.supplier,
            items=[
                {"material_id": self.material, "quantity": 4},
                {"material_id": second_material, "quantity": 2},
            ],
            actor_max_id=10,
            actor_name="Master",
        )
        result = store.list_movements(site_id=self.site1, material_id=self.material)
        item = result["items"][0]
        supplier = store.list_suppliers()[0]["name"]
        self.assertEqual(item["supplier_name"], supplier)
        self.assertEqual(item["received_by_name"], "Master")
        self.assertEqual(len(item["batch_items"]), 2)

    def test_default_site_is_gruzovoy(self):
        sites = store.list_sites()
        dashboard = store.dashboard()
        gruzovoy = next(site for site in sites if site["name"] == "Грузовой")
        self.assertEqual(dashboard["default_site_id"], gruzovoy["id"])
        self.assertEqual(dashboard["selected_site_id"], gruzovoy["id"])

    def test_edit_receipt_updates_items_and_logs_audit(self):
        receipt = store.record_receipt_batch(
            site_id=self.site1,
            supplier_id=self.supplier,
            items=[{"material_id": self.material, "quantity": 10}],
            actor_max_id=10,
            actor_name="Master",
        )
        edited = store.edit_receipt(
            receipt_id=receipt["id"],
            supplier_id=self.supplier,
            items=[{"material_id": self.material, "quantity": 7}],
            note="исправление",
            actor_max_id=11,
            actor_name="Admin",
        )
        self.assertEqual(edited["items"][0]["quantity"], 7)
        balance = next(x for x in store.site_stock(self.site1) if x["id"] == self.material)
        self.assertEqual(balance["balance"], 7)
        audit = store.list_audit_log(limit=5)
        self.assertTrue(any(item["action"] == "receipt_edit" for item in audit["items"]))

    def test_cancel_receipt_reverses_stock_and_hides_from_payment(self):
        receipt = store.record_receipt_batch(
            site_id=self.site1,
            supplier_id=self.supplier,
            items=[{"material_id": self.material, "quantity": 10}],
            actor_max_id=10,
            actor_name="Master",
        )
        cancelled = store.cancel_receipt(
            receipt_id=receipt["id"],
            reason="Дубль прихода №1",
            actor_max_id=99,
            actor_name="Admin",
        )
        self.assertTrue(cancelled["is_cancelled"])
        self.assertEqual(cancelled["payment_status"], "cancelled")
        balance = next(x for x in store.site_stock(self.site1) if x["id"] == self.material)
        self.assertEqual(balance["balance"], 0)
        payment_ids = {item["id"] for item in store.list_payment_receipts(site_id=self.site1)}
        self.assertNotIn(receipt["id"], payment_ids)
        audit = store.list_audit_log(limit=5)
        self.assertTrue(any(item["action"] == "receipt_cancel" for item in audit["items"]))

    def test_cancel_receipt_blocked_when_stock_already_used(self):
        receipt = store.record_receipt_batch(
            site_id=self.site1,
            supplier_id=self.supplier,
            items=[{"material_id": self.material, "quantity": 10}],
            actor_max_id=10,
            actor_name="Master",
        )
        store.record_issue(
            site_id=self.site1,
            material_id=self.material,
            quantity=4,
            actor_max_id=10,
            actor_name="Master",
        )
        with self.assertRaisesRegex(ValueError, "уже списан"):
            store.cancel_receipt(
                receipt_id=receipt["id"],
                reason="Ошибка",
                actor_max_id=99,
                actor_name="Admin",
            )

    def test_find_similar_receipt_today(self):
        receipt = store.record_receipt_batch(
            site_id=self.site1,
            supplier_id=self.supplier,
            items=[{"material_id": self.material, "quantity": 1}],
            actor_max_id=1,
            actor_name="Master",
        )
        similar = store.find_similar_receipt_today(
            site_id=self.site1,
            supplier_id=self.supplier,
        )
        self.assertEqual(similar["id"], receipt["id"])
        self.assertIsNone(
            store.find_similar_receipt_today(
                site_id=self.site2,
                supplier_id=self.supplier,
            )
        )


    def test_find_open_request_for_material(self):
        open_request = store.create_request(
            site_id=self.site1,
            material_id=self.material,
            quantity=3,
            actor_max_id=1,
            actor_name="Master",
        )
        found = store.find_open_request_for_material(
            site_id=self.site1,
            material_id=self.material,
        )
        self.assertEqual(found["id"], open_request["id"])
        store.transition_request(
            request_id=open_request["id"],
            new_status="cancelled",
            actor_role="master",
            actor_max_id=1,
            actor_name="Master",
        )
        self.assertIsNone(store.find_open_request_for_material(
            site_id=self.site1,
            material_id=self.material,
        ))

    def test_create_request_requires_confirm_for_duplicate(self):
        store.create_request(
            site_id=self.site1,
            material_id=self.material,
            quantity=2,
            actor_max_id=1,
            actor_name="Master",
        )
        with self.assertRaisesRegex(ValueError, "уже есть заявка"):
            store.create_request(
                site_id=self.site1,
                material_id=self.material,
                quantity=1,
                actor_max_id=1,
                actor_name="Master",
            )
        duplicate = store.create_request(
            site_id=self.site1,
            material_id=self.material,
            quantity=1,
            actor_max_id=1,
            actor_name="Master",
            confirm_duplicate=True,
        )
        self.assertEqual(len(duplicate["items"]), 1)

    def test_create_delivery_requires_eta(self):
        request = store.create_request(
            site_id=self.site1,
            material_id=self.material,
            quantity=5,
            actor_max_id=10,
            actor_name="Master",
        )
        store.transition_request(
            request_id=request["id"],
            new_status="accepted",
            actor_role="supply",
            actor_max_id=20,
            actor_name="Supply",
        )
        item_id = store.get_request(request["id"])["items"][0]["id"]
        with self.assertRaisesRegex(ValueError, "срок поставки"):
            store.create_delivery(
                request_id=request["id"],
                supplier_id=self.supplier,
                items=[{"request_item_id": item_id, "quantity": 5}],
                actor_max_id=20,
                actor_name="Supply",
            )

    def test_mark_in_transit_and_update_eta(self):
        request = store.create_request(
            site_id=self.site1,
            material_id=self.material,
            quantity=4,
            actor_max_id=10,
            actor_name="Master",
        )
        store.transition_request(
            request_id=request["id"],
            new_status="accepted",
            actor_role="supply",
            actor_max_id=20,
            actor_name="Supply",
        )
        moved = store.transition_request(
            request_id=request["id"],
            new_status="in_transit",
            actor_role="supply",
            actor_max_id=20,
            actor_name="Supply",
            expected_delivery_days=5,
        )
        self.assertEqual(moved["status"], "in_transit")
        self.assertEqual(moved["expected_delivery_days"], 5)
        self.assertTrue(moved.get("eta_date_label"))
        self.assertEqual(moved["ordered_by_name"], "Supply")

        updated = store.update_request_eta(
            request_id=request["id"],
            expected_delivery_days=7,
            actor_role="supply",
            actor_max_id=20,
            actor_name="Supply",
        )
        self.assertEqual(updated["expected_delivery_days"], 7)
        self.assertIsNone(updated.get("eta_reminder_sent_at"))

    def test_eta_reminders_due_within_24_hours(self):
        request = store.create_request(
            site_id=self.site1,
            material_id=self.material,
            quantity=2,
            actor_max_id=10,
            actor_name="Master",
        )
        store.transition_request(
            request_id=request["id"],
            new_status="accepted",
            actor_role="supply",
            actor_max_id=20,
            actor_name="Supply",
        )
        store.transition_request(
            request_id=request["id"],
            new_status="in_transit",
            actor_role="supply",
            actor_max_id=20,
            actor_name="Supply",
            expected_delivery_days=7,
        )
        with store._connect() as conn:
            conn.execute(
                "UPDATE sm_requests SET expected_delivery_at=? WHERE id=?",
                (time.time() + 12 * 3600, request["id"]),
            )
            conn.commit()
        due = store.list_eta_reminders_due()
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["id"], request["id"])
        store.mark_eta_reminder_sent(request["id"])
        self.assertEqual(store.list_eta_reminders_due(), [])

    def test_request_timeline_from_audit(self):
        request = store.create_request(
            site_id=self.site1,
            material_id=self.material,
            quantity=5,
            actor_max_id=10,
            actor_name="Master",
        )
        store.transition_request(
            request_id=request["id"],
            new_status="accepted",
            actor_role="supply",
            actor_max_id=20,
            actor_name="Supply",
        )
        item_id = store.get_request(request["id"])["items"][0]["id"]
        delivery = store.create_delivery(
            request_id=request["id"],
            supplier_id=self.supplier,
            items=[{"request_item_id": item_id, "quantity": 5}],
            expected_delivery_days=3,
            actor_max_id=20,
            actor_name="Supply",
        )
        store.receive_delivery(
            delivery_id=delivery["id"],
            items=[{"delivery_item_id": delivery["items"][0]["id"], "quantity": 5}],
            actor_max_id=10,
            actor_name="Master",
            confirm_early=True,
        )
        timeline = store.get_request_timeline(request["id"])
        labels = [item["label"] for item in timeline]
        self.assertIn("Заявка создана", labels)
        self.assertIn("Статус: принята", labels)
        self.assertIn("Заказ у поставщика", labels)
        self.assertIn("Поставка на базу", labels)

    def test_describe_audit_entry_formats_details(self):
        described = store.describe_audit_entry({
            "action": "request_transition",
            "entity_type": "request",
            "entity_id": "5",
            "details": {"from": "submitted", "to": "accepted"},
        })
        self.assertEqual(described["action_label"], "Статус заявки")
        self.assertEqual(described["entity_label"], "Заявка №5")
        self.assertIn("принята", described["lines"][0])

        receipt_desc = store.describe_audit_entry({
            "action": "receipt_price",
            "entity_type": "receipt",
            "entity_id": "5",
            "details": {"total_amount": 4500, "lines": 2},
        })
        self.assertIn("4500.00", receipt_desc["lines"][0])

    def test_eta_hidden_when_request_received(self):
        request = store.create_request(
            site_id=self.site1,
            material_id=self.material,
            quantity=5,
            actor_max_id=10,
            actor_name="Master",
        )
        store.transition_request(
            request_id=request["id"],
            new_status="accepted",
            actor_role="supply",
            actor_max_id=20,
            actor_name="Supply",
        )
        store.transition_request(
            request_id=request["id"],
            new_status="in_transit",
            actor_role="supply",
            actor_max_id=20,
            actor_name="Supply",
            expected_delivery_days=5,
        )
        item_id = store.get_request(request["id"])["items"][0]["id"]
        delivery = store.create_delivery(
            request_id=request["id"],
            supplier_id=self.supplier,
            items=[{"request_item_id": item_id, "quantity": 5}],
            expected_delivery_days=5,
            actor_max_id=20,
            actor_name="Supply",
        )
        received = store.receive_delivery(
            delivery_id=delivery["id"],
            items=[{"delivery_item_id": delivery["items"][0]["id"], "quantity": 5}],
            actor_max_id=10,
            actor_name="Master",
            confirm_early=True,
        )
        req = received["request"]
        self.assertEqual(req["status"], "received")
        self.assertIn("Принято на базе", req.get("eta_display_line") or "")
        self.assertNotIn("через 5 дн.", req.get("eta_display_line") or "")

    def test_receive_early_requires_confirmation(self):
        request = store.create_request(
            site_id=self.site1,
            material_id=self.material,
            quantity=3,
            actor_max_id=10,
            actor_name="Master",
        )
        store.transition_request(
            request_id=request["id"],
            new_status="accepted",
            actor_role="supply",
            actor_max_id=20,
            actor_name="Supply",
        )
        store.transition_request(
            request_id=request["id"],
            new_status="in_transit",
            actor_role="supply",
            actor_max_id=20,
            actor_name="Supply",
            expected_delivery_days=5,
        )
        item_id = store.get_request(request["id"])["items"][0]["id"]
        delivery = store.create_delivery(
            request_id=request["id"],
            supplier_id=self.supplier,
            items=[{"request_item_id": item_id, "quantity": 3}],
            expected_delivery_days=5,
            actor_max_id=20,
            actor_name="Supply",
        )
        with self.assertRaisesRegex(ValueError, "раньше срока"):
            store.receive_delivery(
                delivery_id=delivery["id"],
                items=[{"delivery_item_id": delivery["items"][0]["id"], "quantity": 3}],
                actor_max_id=10,
                actor_name="Master",
            )


class LegacyMigrationTests(unittest.TestCase):
    def test_legacy_request_is_preserved_and_backfilled(self):
        with tempfile.TemporaryDirectory() as directory:
            old_path = store.DB_PATH
            store.DB_PATH = Path(directory) / "legacy.db"
            try:
                conn = sqlite3.connect(store.DB_PATH)
                conn.executescript(
                    """
                    CREATE TABLE sm_sites (
                      id INTEGER PRIMARY KEY, name TEXT UNIQUE, sort_order INTEGER DEFAULT 0,
                      active INTEGER DEFAULT 1, created_at REAL, updated_at REAL);
                    CREATE TABLE sm_materials (
                      id INTEGER PRIMARY KEY, name TEXT UNIQUE, unit TEXT DEFAULT 'шт',
                      min_level REAL DEFAULT 0, sort_order INTEGER DEFAULT 0,
                      active INTEGER DEFAULT 1, created_at REAL, updated_at REAL);
                    CREATE TABLE sm_suppliers (
                      id INTEGER PRIMARY KEY, name TEXT UNIQUE, sort_order INTEGER DEFAULT 0,
                      active INTEGER DEFAULT 1, created_at REAL, updated_at REAL);
                    CREATE TABLE sm_stock_ops (
                      id INTEGER PRIMARY KEY, site_id INTEGER, material_id INTEGER,
                      supplier_id INTEGER, op_type TEXT, quantity REAL, quantity_delta REAL,
                      actor_max_id INTEGER, actor_name TEXT, note TEXT, request_id INTEGER,
                      created_at REAL);
                    CREATE TABLE sm_requests (
                      id INTEGER PRIMARY KEY, site_id INTEGER, material_id INTEGER,
                      quantity REAL, urgency TEXT, status TEXT, requested_by_max_id INTEGER,
                      requested_by_name TEXT, comment TEXT, supply_comment TEXT,
                      created_at REAL, updated_at REAL);
                    INSERT INTO sm_sites VALUES(99,'Legacy site',0,1,1,1);
                    INSERT INTO sm_materials VALUES(88,'Legacy material','кг',0,0,1,1,1);
                    INSERT INTO sm_requests VALUES(77,99,88,12,'urgent','new',5,'Old','keep me','',1,1);
                    """
                )
                conn.commit()
                conn.close()
                store.init_sklad_master_db()
                request = store.get_request(77)
                self.assertEqual(request["comment"], "keep me")
                self.assertEqual(request["status"], "submitted")
                self.assertEqual(request["items"][0]["quantity"], 12)
                with sqlite3.connect(store.DB_PATH) as check:
                    version = check.execute("PRAGMA user_version").fetchone()[0]
                self.assertEqual(version, store.SCHEMA_VERSION)
                backups = list((store.DB_PATH.parent / "backups").glob("legacy-pre-v*.db"))
                self.assertEqual(len(backups), 1)
            finally:
                store.DB_PATH = old_path


if __name__ == "__main__":
    unittest.main()
