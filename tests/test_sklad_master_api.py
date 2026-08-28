import os
import sys
import tempfile
import unittest
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fotonych-bot"))

import sklad_master_api as api
import sklad_master_store as store


class SkladMasterApiTests(AioHTTPTestCase):
    async def get_application(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_path = store.DB_PATH
        self.old_masters = os.environ.get("MATERIALS_MASTER_MAX_IDS")
        self.old_supply = os.environ.get("MATERIALS_SUPPLY_MAX_IDS")
        self.old_admins = os.environ.get("DRIVERS_ADMIN_MAX_IDS")
        store.DB_PATH = Path(self.temp.name) / "api.db"
        os.environ["MATERIALS_MASTER_MAX_IDS"] = "101"
        self.old_validate = api.validate_init_data
        self.old_user_id = api.user_id_from_user
        api.validate_init_data = lambda raw, token: (
            {"user": {"user_id": 101, "name": "Мастер"}} if raw == "valid" else None
        )
        api.user_id_from_user = lambda user: int(user["user_id"])
        app = web.Application()
        api.register_sklad_master_routes(app)
        self.site_id = store.list_sites()[0]["id"]
        self.material_id = store.list_materials()[0]["id"]
        self.second_material_id = store.list_materials()[1]["id"]
        self.supplier_id = store.list_suppliers()[0]["id"]
        store.create_request(
            site_id=self.site_id,
            material_id=self.material_id,
            quantity=2,
            actor_max_id=101,
            actor_name="Мастер",
        )
        return app

    async def asyncTearDown(self):
        await super().asyncTearDown()
        api.validate_init_data = self.old_validate
        api.user_id_from_user = self.old_user_id
        store.DB_PATH = self.old_path
        if self.old_masters is None:
            os.environ.pop("MATERIALS_MASTER_MAX_IDS", None)
        else:
            os.environ["MATERIALS_MASTER_MAX_IDS"] = self.old_masters
        if self.old_supply is None:
            os.environ.pop("MATERIALS_SUPPLY_MAX_IDS", None)
        else:
            os.environ["MATERIALS_SUPPLY_MAX_IDS"] = self.old_supply
        if self.old_admins is None:
            os.environ.pop("DRIVERS_ADMIN_MAX_IDS", None)
        else:
            os.environ["DRIVERS_ADMIN_MAX_IDS"] = self.old_admins
        self.temp.cleanup()

    async def test_bootstrap_requires_max_auth(self):
        response = await self.client.get("/api/sklad-master/bootstrap")
        self.assertEqual(response.status, 401)

    async def test_bootstrap_returns_role_and_capabilities(self):
        response = await self.client.get(
            "/api/sklad-master/bootstrap",
            headers={"X-Max-Init-Data": "valid"},
        )
        self.assertEqual(response.status, 200)
        body = await response.json()
        self.assertEqual(body["roles"], ["master"])
        self.assertTrue(body["capabilities"]["receipt"])
        self.assertTrue(body["capabilities"]["transfer"])
        self.assertEqual(body["requests"][0]["status_label"], "Новая")
        self.assertIn("cancel", body["requests"][0]["allowed_actions"])

    async def test_receipt_notification_contains_full_details(self):
        sent = []
        original_notify = api._notify

        async def capture(lines, **kwargs):
            sent.append((lines, kwargs))

        api._notify = capture
        try:
            response = await self.client.post(
                "/api/sklad-master/receipts",
                headers={"X-Max-Init-Data": "valid"},
                json={
                    "site_id": self.site_id,
                    "supplier_id": self.supplier_id,
                    "items": [
                        {"material_id": self.material_id, "quantity": 3},
                        {"material_id": self.second_material_id, "quantity": 2},
                    ],
                    "note": "накладная 15",
                    "idempotency_key": "api-receipt-1",
                },
            )
        finally:
            api._notify = original_notify
        self.assertEqual(response.status, 201)
        text = "\n".join(sent[0][0])
        self.assertIn("Площадка:", text)
        self.assertIn("Позиции:", text)
        self.assertIn(": 3.0 шт", text)
        self.assertIn(": 2.0 шт", text)
        self.assertIn("Поставщик:", text)
        self.assertIn("Принял:", text)
        self.assertIn("(id 101)", text)
        self.assertIn("Примечание: накладная 15", text)
        self.assertEqual(sent[0][1]["role"], "supply")

    async def test_issue_sends_supply_alert_once_on_threshold_transition(self):
        store.set_site_material_minimum(
            site_id=self.site_id,
            material_id=self.material_id,
            min_level=5,
            actor_max_id=101,
            actor_name="Мастер",
        )
        store.record_receipt(
            site_id=self.site_id,
            material_id=self.material_id,
            supplier_id=self.supplier_id,
            quantity=6,
            actor_max_id=101,
            actor_name="Мастер",
        )
        sent = []
        original_notify = api._notify

        async def capture(lines, **kwargs):
            sent.append((lines, kwargs))

        api._notify = capture
        try:
            response = await self.client.post(
                "/api/sklad-master/issues",
                headers={"X-Max-Init-Data": "valid"},
                json={
                    "site_id": self.site_id,
                    "material_id": self.material_id,
                    "quantity": 1,
                    "note": "выдано",
                    "idempotency_key": "issue-alert-1",
                },
            )
        finally:
            api._notify = original_notify
        self.assertEqual(response.status, 201)
        self.assertEqual(len(sent), 2)
        alert_text = "\n".join(sent[1][0])
        self.assertIn("Остаток достиг минимума", alert_text)
        self.assertIn("Остаток: 5.0", alert_text)
        self.assertEqual(sent[1][1]["role"], "supply")
        self.assertFalse(sent[1][1]["admin"])

    async def test_request_notifications_explain_full_partial_workflow(self):
        sent = []
        original_notify = api._notify

        async def capture(lines, **kwargs):
            sent.append("\n".join(line for line in lines if line))

        api._notify = capture
        try:
            created_response = await self.client.post(
                "/api/sklad-master/requests",
                headers={"X-Max-Init-Data": "valid"},
                json={
                    "site_id": self.site_id,
                    "items": [{"material_id": self.material_id, "quantity": 5}],
                    "urgency": "urgent",
                    "idempotency_key": "request-message-1",
                },
            )
            created = (await created_response.json())["request"]
            os.environ["MATERIALS_SUPPLY_MAX_IDS"] = "101"
            accepted_response = await self.client.patch(
                f"/api/sklad-master/requests/{created['id']}",
                headers={"X-Max-Init-Data": "valid"},
                json={"status": "accepted", "idempotency_key": "request-accepted-1"},
            )
            accepted = (await accepted_response.json())["request"]
            delivery_response = await self.client.post(
                f"/api/sklad-master/requests/{created['id']}/deliveries",
                headers={"X-Max-Init-Data": "valid"},
                json={
                    "supplier_id": self.supplier_id,
                    "items": [{
                        "request_item_id": accepted["items"][0]["id"],
                        "quantity": 3,
                    }],
                    "idempotency_key": "request-delivery-1",
                },
            )
            delivery = (await delivery_response.json())["delivery"]
            receive_response = await self.client.post(
                f"/api/sklad-master/deliveries/{delivery['id']}/receive",
                headers={"X-Max-Init-Data": "valid"},
                json={
                    "items": [{
                        "delivery_item_id": delivery["items"][0]["id"],
                        "quantity": 2,
                    }],
                    "idempotency_key": "request-receive-1",
                },
            )
            self.assertEqual(receive_response.status, 200)
        finally:
            api._notify = original_notify

        self.assertEqual(len(sent), 4)
        self.assertIn("Запрошено:", sent[0])
        self.assertIn(": 5 шт", sent[0])
        self.assertIn("Заявка #", sent[1])
        self.assertIn("Принял заявку:", sent[1])
        self.assertIn("Поставщик:", sent[2])
        self.assertIn("Отправлено:", sent[2])
        self.assertIn("Принято сейчас:", sent[3])
        self.assertIn("Исполнение заявки: частично", sent[3])
        self.assertIn("Осталось получить:", sent[3])
        self.assertIn(": 3 шт", sent[3])

    async def test_admin_can_edit_material_and_see_inactive_catalog(self):
        os.environ["DRIVERS_ADMIN_MAX_IDS"] = "101"
        response = await self.client.patch(
            f"/api/sklad-master/admin/materials/{self.second_material_id}",
            headers={"X-Max-Init-Data": "valid"},
            json={"name": "Материал переименован", "unit": "кг", "active": False},
        )
        self.assertEqual(response.status, 200)
        material = (await response.json())["material"]
        self.assertEqual(material["name"], "Материал переименован")
        self.assertFalse(material["active"])

        bootstrap = await self.client.get(
            "/api/sklad-master/bootstrap",
            headers={"X-Max-Init-Data": "valid"},
        )
        body = await bootstrap.json()
        inactive = [item for item in body["admin_materials"] if not item["active"]]
        self.assertEqual(inactive[0]["name"], "Материал переименован")


if __name__ == "__main__":
    unittest.main()
