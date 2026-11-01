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
            {"user": {"user_id": 101, "name": "Мастер", "first_name": "Мастер"}}
            if raw == "valid"
            else {"user": {"user_id": 888, "name": "Гость", "first_name": "Гость"}}
            if raw == "guest"
            else None
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

    async def test_transfer_notification_reaches_both_chats_with_details(self):
        """Перемещение должно уходить в чат приходов и чат сводок с деталями."""
        store.record_receipt(
            site_id=self.site_id,
            material_id=self.material_id,
            supplier_id=self.supplier_id,
            quantity=10,
            actor_max_id=101,
            actor_name="Мастер",
        )
        to_site_id = store.list_sites()[1]["id"]
        receipt_chat = []
        reports_chat = []
        personal = []
        original_notify = api._notify
        original_receipt_notify = api.notify_master_receipt
        original_event_notify = api.notify_event

        async def capture(lines, **kwargs):
            personal.append((lines, kwargs))

        async def capture_receipt(text):
            receipt_chat.append(text)
            return True

        async def capture_event(text):
            reports_chat.append(text)
            return True

        api._notify = capture
        api.notify_master_receipt = capture_receipt
        api.notify_event = capture_event
        try:
            response = await self.client.post(
                "/api/sklad-master/transfers",
                headers={"X-Max-Init-Data": "valid"},
                json={
                    "from_site_id": self.site_id,
                    "to_site_id": to_site_id,
                    "material_id": self.material_id,
                    "quantity": 4,
                    "note": "на Грузовой",
                    "idempotency_key": "api-transfer-1",
                },
            )
        finally:
            api._notify = original_notify
            api.notify_master_receipt = original_receipt_notify
            api.notify_event = original_event_notify
        self.assertEqual(response.status, 201)
        self.assertEqual(len(receipt_chat), 1)
        self.assertEqual(len(reports_chat), 1)
        text = receipt_chat[0]
        self.assertIn("перемещение", text)
        self.assertIn("Время:", text)
        self.assertIn("Откуда:", text)
        self.assertIn("Куда:", text)
        self.assertIn("Наименование:", text)
        self.assertIn("Количество: 4", text)
        self.assertIn("(id 101)", text)
        self.assertIn("Примечание: на Грузовой", text)
        self.assertEqual(reports_chat[0], text)
        self.assertIn("перемещение", "\n".join(personal[0][0]))

    async def test_transfer_replay_does_not_notify_twice(self):
        """Повтор с тем же Idempotency-Key не должен дублировать сообщение в чат."""
        store.record_receipt(
            site_id=self.site_id,
            material_id=self.material_id,
            supplier_id=self.supplier_id,
            quantity=10,
            actor_max_id=101,
            actor_name="Мастер",
        )
        to_site_id = store.list_sites()[1]["id"]
        receipt_chat = []
        original_notify = api._notify
        original_receipt_notify = api.notify_master_receipt
        original_event_notify = api.notify_event

        async def capture(lines, **kwargs):
            return None

        async def capture_receipt(text):
            receipt_chat.append(text)
            return True

        async def capture_event(text):
            return True

        api._notify = capture
        api.notify_master_receipt = capture_receipt
        api.notify_event = capture_event
        payload = {
            "from_site_id": self.site_id,
            "to_site_id": to_site_id,
            "material_id": self.material_id,
            "quantity": 2,
            "idempotency_key": "api-transfer-replay-1",
        }
        try:
            first = await self.client.post(
                "/api/sklad-master/transfers",
                headers={"X-Max-Init-Data": "valid"},
                json=payload,
            )
            second = await self.client.post(
                "/api/sklad-master/transfers",
                headers={"X-Max-Init-Data": "valid"},
                json=payload,
            )
        finally:
            api._notify = original_notify
            api.notify_master_receipt = original_receipt_notify
            api.notify_event = original_event_notify
        self.assertEqual(first.status, 201)
        self.assertEqual(second.status, 201)
        self.assertEqual(len(receipt_chat), 1)

    async def test_issue_without_stock_does_not_notify_chats(self):
        """Ошибка «недостаточно остатка» не должна порождать сообщение в чат."""
        receipt_chat = []
        reports_chat = []
        original_receipt_notify = api.notify_master_receipt
        original_event_notify = api.notify_event

        async def capture_receipt(text):
            receipt_chat.append(text)
            return True

        async def capture_event(text):
            reports_chat.append(text)
            return True

        api.notify_master_receipt = capture_receipt
        api.notify_event = capture_event
        try:
            response = await self.client.post(
                "/api/sklad-master/issues",
                headers={"X-Max-Init-Data": "valid"},
                json={
                    "site_id": self.site_id,
                    "material_id": self.second_material_id,
                    "quantity": 999,
                    "idempotency_key": "api-issue-empty-1",
                },
            )
        finally:
            api.notify_master_receipt = original_receipt_notify
            api.notify_event = original_event_notify
        self.assertEqual(response.status, 400)
        self.assertEqual(receipt_chat, [])
        self.assertEqual(reports_chat, [])

    async def test_movement_message_has_time_and_names(self):
        """Сообщение о движении несёт дату, время, площадку и наименование."""
        store.record_receipt(
            site_id=self.site_id,
            material_id=self.material_id,
            supplier_id=self.supplier_id,
            quantity=20,
            actor_max_id=101,
            actor_name="Мастер",
        )
        lines = api._movement_lines(
            "📤 Склад Мастер · выдача",
            result={
                "site": {"id": self.site_id, "name": "Туран"},
                "site_id": self.site_id,
                "material": {"id": self.material_id, "name": "Брус 150х150", "unit": "шт"},
                "quantity": 5,
                "balance": 15,
                "created_at": 1762000000.0,
            },
            actor_name="Мастер",
            actor_max_id=101,
            body={"note": "на ремонт"},
        )
        text = "\n".join(lines)
        self.assertIn("Время:", text)
        self.assertIn("Площадка: Туран", text)
        self.assertIn("Наименование: Брус 150х150", text)
        self.assertIn("Количество: 5 шт", text)
        self.assertIn("Остаток: 15 шт", text)
        self.assertIn("Примечание: на ремонт", text)

    async def test_adjustment_message_shows_signed_delta(self):
        """Корректировка показывает знак изменения, то есть расход или приход."""
        lines = api._movement_lines(
            "🧮 Склад Мастер · корректировка",
            result={
                "site_id": self.site_id,
                "material": {"name": "Проволока 6 мм", "unit": "кг"},
                "quantity_delta": -3.5,
                "balance": 8.0,
                "created_at": 1762000000.0,
            },
            actor_name="Мастер",
            actor_max_id=101,
        )
        text = "\n".join(lines)
        self.assertIn("Наименование: Проволока 6 мм", text)
        self.assertIn("Количество: −3.5 кг", text)

    async def test_receipt_message_contains_date_and_names(self):
        """Приход в чат несёт дату и список наименований."""
        sent = []
        original_receipt_notify = api.notify_master_receipt

        async def capture_receipt(text):
            sent.append(text)
            return True

        api.notify_master_receipt = capture_receipt
        try:
            response = await self.client.post(
                "/api/sklad-master/receipts",
                headers={"X-Max-Init-Data": "valid"},
                json={
                    "site_id": self.site_id,
                    "supplier_id": self.supplier_id,
                    "items": [{"material_id": self.material_id, "quantity": 7}],
                    "note": "накладная 77",
                    "idempotency_key": "api-receipt-time-1",
                },
            )
        finally:
            api.notify_master_receipt = original_receipt_notify
        self.assertEqual(response.status, 201)
        self.assertEqual(len(sent), 1)
        text = sent[0]
        self.assertIn("приход", text)
        self.assertIn("Дата:", text)
        self.assertIn(":", text.split("Время:")[-1] if "Время:" in text else "Дата:")
        self.assertIn("Наименования:", text)
        self.assertIn("Брус 150х150", text)

    async def test_sites_without_movements_are_marked_idle(self):
        """Площадка с движениями — рабочая, без движений — «не в работе»."""
        site_id = store.list_sites()[0]["id"]
        store.record_receipt(
            site_id=site_id,
            material_id=store.list_materials()[0]["id"],
            supplier_id=store.list_suppliers()[0]["id"],
            quantity=3,
            actor_max_id=101,
            actor_name="Мастер",
            idempotency_key="idle-check-receipt-1",
        )
        response = await self.client.get(
            "/api/sklad-master/bootstrap",
            headers={"X-Max-Init-Data": "valid"},
        )
        body = await response.json()
        idle = {site["id"]: site["idle"] for site in body["sites"]}
        self.assertFalse(idle[site_id])
        others = [value for key, value in idle.items() if key != site_id]
        self.assertTrue(all(others))

    async def test_export_summary_returns_xlsx(self):
        """Сводка выгружается в Excel и содержит итоги прихода и расхода."""
        import io

        from openpyxl import load_workbook

        response = await self.client.get(
            "/api/sklad-master/export.xlsx?kind=summary",
            headers={"X-Max-Init-Data": "valid"},
        )
        self.assertEqual(response.status, 200)
        self.assertIn("spreadsheetml", response.headers.get("Content-Type", ""))
        workbook = load_workbook(io.BytesIO(await response.read()))
        sheet = workbook.active
        headers = [cell.value for cell in sheet[4]]
        self.assertIn("Материал", headers)
        self.assertIn("Приход", headers)
        self.assertIn("Расход", headers)
        self.assertIn("Остаток", headers)
        texts = [str(cell.value or "") for row in sheet.iter_rows() for cell in row]
        self.assertTrue(any(value.startswith("ИТОГО") for value in texts))

    async def test_export_full_has_history_and_receipts(self):
        """Полная выгрузка содержит листы истории, приходов и перемещений."""
        import io

        from openpyxl import load_workbook

        response = await self.client.get(
            "/api/sklad-master/export.xlsx?kind=full",
            headers={"X-Max-Init-Data": "valid"},
        )
        self.assertEqual(response.status, 200)
        workbook = load_workbook(io.BytesIO(await response.read()))
        for title in ("История", "Приходы", "Перемещения"):
            self.assertIn(title, workbook.sheetnames)

    async def test_export_requires_access(self):
        """Без входа в mini-app выгрузка недоступна."""
        response = await self.client.get("/api/sklad-master/export.xlsx")
        self.assertEqual(response.status, 401)

    async def test_export_link_downloads_file_without_mini_app(self):
        """Ссылка отдаёт файл без заголовков мини-аппа и работает один раз."""
        created = await self.client.post(
            "/api/sklad-master/export-link?kind=summary",
            headers={"X-Max-Init-Data": "valid"},
            json={},
        )
        self.assertEqual(created.status, 200)
        body = await created.json()
        token = body["url"].split("t=")[-1]
        self.assertTrue(token)
        self.assertEqual(body["expires_in"], 300)

        first = await self.client.get(f"/api/sklad-master/export?t={token}")
        self.assertEqual(first.status, 200)
        self.assertIn("spreadsheetml", first.headers.get("Content-Type", ""))
        self.assertGreater(len(await first.read()), 1000)

        second = await self.client.get(f"/api/sklad-master/export?t={token}")
        self.assertEqual(second.status, 403)

    async def test_export_link_requires_access(self):
        """Без входа ссылка не выдаётся."""
        response = await self.client.post("/api/sklad-master/export-link", json={})
        self.assertEqual(response.status, 401)

    async def test_export_link_unknown_token_rejected(self):
        """Неизвестный токен не даёт файл."""
        response = await self.client.get("/api/sklad-master/export?t=unknown-token")
        self.assertEqual(response.status, 403)

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

    async def test_bootstrap_guest_without_roles(self):
        response = await self.client.get(
            "/api/sklad-master/bootstrap",
            headers={"X-Max-Init-Data": "guest"},
        )
        self.assertEqual(response.status, 200)
        body = await response.json()
        self.assertTrue(body.get("guest"))
        self.assertEqual(body["user"]["id"], 888)
        self.assertEqual(body["access_request"]["status"], "none")

    async def test_access_request_creates_pending(self):
        original = api.submit_and_notify_access_request

        async def fake_submit(*, max_id, display_name):
            ok, message, row = store.submit_access_request(
                max_id=max_id,
                display_name=display_name,
            )
            return ok, message

        api.submit_and_notify_access_request = fake_submit
        try:
            response = await self.client.post(
                "/api/sklad-master/access-request",
                headers={"X-Max-Init-Data": "guest"},
                json={},
            )
            self.assertEqual(response.status, 200)
            body = await response.json()
            self.assertTrue(body["ok"])
            self.assertEqual(body["access_request"]["status"], "pending")

            again = await self.client.post(
                "/api/sklad-master/access-request",
                headers={"X-Max-Init-Data": "guest"},
                json={},
            )
            self.assertEqual(again.status, 200)
            again_body = await again.json()
            self.assertFalse(again_body["ok"])
            self.assertEqual(again_body["access_request"]["status"], "pending")
        finally:
            api.submit_and_notify_access_request = original

    async def test_resolve_access_request_assigns_role(self):
        store.submit_access_request(max_id=888, display_name="Гость")
        ok, _msg, row = store.resolve_access_request(
            max_id=888,
            action="master",
            actor_max_id=101,
            actor_name="Админ",
        )
        self.assertTrue(ok)
        self.assertEqual(row["status"], "approved")
        access = store.get_access(888)
        self.assertIn("master", access["roles"])

        ok2, reason, _row2 = store.resolve_access_request(
            max_id=888,
            action="supply",
            actor_max_id=101,
            actor_name="Админ",
        )
        self.assertFalse(ok2)
        self.assertEqual(reason, "already_processed")


if __name__ == "__main__":
    unittest.main()
