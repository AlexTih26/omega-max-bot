"""HTTP tests for the separate PIN-protected RUMEX accountant registry."""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from hashlib import sha256
from hmac import new as hmac_new
from pathlib import Path
from urllib.parse import quote

try:
    from aiohttp import web
    from aiohttp.test_utils import AioHTTPTestCase
except ModuleNotFoundError:
    web = None
    AioHTTPTestCase = unittest.TestCase
    AIOHTTP_AVAILABLE = False
else:
    AIOHTTP_AVAILABLE = True

ROOT = Path(__file__).resolve().parent.parent
BOT = ROOT / "fotonych-bot"
if str(BOT) not in sys.path:
    sys.path.insert(0, str(BOT))

import rumex_registry_store as store  # noqa: E402
if AIOHTTP_AVAILABLE:
    import rumex_registry_api as api  # noqa: E402
    import rumex_registry_auth as auth  # noqa: E402
    import rumex_test_dispatcher_auth as test_dispatcher_auth  # noqa: E402


@unittest.skipUnless(AIOHTTP_AVAILABLE, "Для HTTP-тестов требуется aiohttp")
class RumexRegistryApiTests(AioHTTPTestCase):
    async def get_application(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._old_db_path = store.DB_PATH
        self._old_samples_dir = api.REGISTRY_SAMPLES_DIR
        self._old_users = os.environ.get("RUMEX_ACCOUNTANT_USERS")
        self._old_secret = os.environ.get("RUMEX_ACCOUNTANT_AUTH_SECRET")
        self._old_max_token = os.environ.get("MAX_BOT_TOKEN")
        self._old_test_dispatchers = os.environ.get("RUMEX_TEST_DISPATCHER_MAX_IDS")
        self._old_test_dispatcher_users = os.environ.get("RUMEX_TEST_DISPATCHER_USERS")
        self._old_test_dispatcher_secret = os.environ.get("RUMEX_TEST_DISPATCHER_AUTH_SECRET")
        store.DB_PATH = Path(self._tmpdir.name) / "rumex_registry.db"
        api.REGISTRY_SAMPLES_DIR = Path(self._tmpdir.name) / "registry_samples"
        api.REGISTRY_SAMPLES_DIR.mkdir()
        (api.REGISTRY_SAMPLES_DIR / "8102 образец.pdf").write_bytes(b"%PDF-test-sample")
        (api.REGISTRY_SAMPLES_DIR / "8102 образец ТТН.xls").write_bytes(b"test-xls-sample")
        os.environ["RUMEX_ACCOUNTANT_USERS"] = (
            "Бухгалтер 1:12345,Бухгалтер 2:23456,Бухгалтер 3:34567"
        )
        os.environ["RUMEX_ACCOUNTANT_AUTH_SECRET"] = "test-secret"
        os.environ["MAX_BOT_TOKEN"] = "test-max-token"
        os.environ["RUMEX_TEST_DISPATCHER_MAX_IDS"] = "9001"
        os.environ["RUMEX_TEST_DISPATCHER_USERS"] = "Тестовый диспетчер:password-123"
        os.environ["RUMEX_TEST_DISPATCHER_AUTH_SECRET"] = "test-dispatcher-secret"

        app = web.Application(middlewares=[auth.rumex_registry_auth_middleware])
        auth.register_rumex_registry_auth_routes(app)
        test_dispatcher_auth.register_rumex_test_dispatcher_auth_routes(app)
        api.register_rumex_registry_routes(app)
        self.shipment = store.create_shipment(
            registry_year=2026,
            items=[{"letter": "A", "number": "103"}],
            dispatcher_name="Диспетчер",
        )
        return app

    async def asyncTearDown(self) -> None:
        await super().asyncTearDown()
        store.DB_PATH = self._old_db_path
        api.REGISTRY_SAMPLES_DIR = self._old_samples_dir
        if self._old_users is None:
            os.environ.pop("RUMEX_ACCOUNTANT_USERS", None)
        else:
            os.environ["RUMEX_ACCOUNTANT_USERS"] = self._old_users
        if self._old_secret is None:
            os.environ.pop("RUMEX_ACCOUNTANT_AUTH_SECRET", None)
        else:
            os.environ["RUMEX_ACCOUNTANT_AUTH_SECRET"] = self._old_secret
        if self._old_max_token is None:
            os.environ.pop("MAX_BOT_TOKEN", None)
        else:
            os.environ["MAX_BOT_TOKEN"] = self._old_max_token
        if self._old_test_dispatchers is None:
            os.environ.pop("RUMEX_TEST_DISPATCHER_MAX_IDS", None)
        else:
            os.environ["RUMEX_TEST_DISPATCHER_MAX_IDS"] = self._old_test_dispatchers
        if self._old_test_dispatcher_users is None:
            os.environ.pop("RUMEX_TEST_DISPATCHER_USERS", None)
        else:
            os.environ["RUMEX_TEST_DISPATCHER_USERS"] = self._old_test_dispatcher_users
        if self._old_test_dispatcher_secret is None:
            os.environ.pop("RUMEX_TEST_DISPATCHER_AUTH_SECRET", None)
        else:
            os.environ["RUMEX_TEST_DISPATCHER_AUTH_SECRET"] = self._old_test_dispatcher_secret
        self._tmpdir.cleanup()

    async def _login(self, pin: str = "12345") -> str:
        response = await self.client.post("/api/rumex-registry/auth", json={"pin": pin})
        self.assertEqual(response.status, 200)
        body = await response.json()
        self.assertIn(body["user"], {"Бухгалтер 1", "Бухгалтер 2", "Бухгалтер 3"})
        return response.cookies[auth.COOKIE_NAME].value

    async def _test_password_login(self) -> str:
        response = await self.client.post(
            "/api/rumex-registry/test/auth",
            json={"username": "Тестовый диспетчер", "password": "password-123"},
        )
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["user"], "Тестовый диспетчер")
        return response.cookies[test_dispatcher_auth.COOKIE_NAME].value

    def _test_dispatcher_headers(self, user_id: int = 9001) -> dict[str, str]:
        user = '{"id":' + str(user_id) + ',"first_name":"Тестовый","last_name":"Диспетчер"}'
        auth_date = str(int(time.time()))
        check_string = "auth_date=" + auth_date + "\nuser=" + user
        secret = hmac_new(b"WebAppData", b"test-max-token", sha256).digest()
        signature = hmac_new(secret, check_string.encode("utf-8"), sha256).hexdigest()
        init_data = "auth_date=" + auth_date + "&user=" + quote(user, safe="") + "&hash=" + signature
        return {"X-Max-Init-Data": init_data}

    def _prepare_test_vehicle(self) -> None:
        source_path = Path(self._tmpdir.name) / "drivers_registry.json"
        source_path.write_text(
            '{"drivers":[{"max_user_id":42,"plate_tail":"553",'
            '"name":"Иванов Иван Иванович","vehicle":"FAW J6",'
            '"taksimo_plate":"К553НХ 138","active":true}]}',
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
            accountant_name="Бухгалтер 1",
        )

    async def test_registry_requires_pin_and_returns_shipments_after_login(self):
        denied = await self.client.get("/api/rumex-registry/registry")
        self.assertEqual(denied.status, 401)

        token = await self._login()
        response = await self.client.get(
            "/api/rumex-registry/registry",
            headers={"Cookie": f"{auth.COOKIE_NAME}={token}"},
        )
        self.assertEqual(response.status, 200)
        body = await response.json()
        self.assertEqual(body["shipments"][0]["registry_number"], "РМ-2026-000001")
        self.assertEqual(len(body["block_types"]), 7)

    async def test_er_workflow_requires_order_and_opens_ttn(self):
        token = await self._login()
        headers = {"Cookie": f"{auth.COOKIE_NAME}={token}"}
        url = f"/api/rumex-registry/shipments/{self.shipment['id']}"

        premature = await self.client.post(f"{url}/er-confirmed", headers=headers, json={})
        self.assertEqual(premature.status, 409)

        sent = await self.client.post(
            f"{url}/er-sent",
            headers=headers,
            json={"external_reference": "Контур-555"},
        )
        self.assertEqual(sent.status, 200)
        self.assertEqual((await sent.json())["shipment"]["status"], "er_sent")

        confirmed = await self.client.post(
            f"{url}/er-confirmed",
            headers=headers,
            json={"external_reference": "Контур-555"},
        )
        self.assertEqual(confirmed.status, 200)
        shipment = (await confirmed.json())["shipment"]
        self.assertEqual(shipment["status"], "tn_ready")
        self.assertEqual(
            {doc["document_kind"]: doc["status"] for doc in shipment["documents"]},
            {"ER": "confirmed", "TN": "ready"},
        )

    async def test_only_registry_admin_can_save_carriers(self):
        user_token = await self._login("23456")
        user_headers = {"Cookie": f"{auth.COOKIE_NAME}={user_token}"}
        payload = {
            "name": "ООО «Перевозчик из карточки»",
            "inn": "1234567890",
            "kpp": "123456789",
            "legal_address": "г. Иркутск, ул. Пример, д. 1",
            "confirmation_source": "counterparty_card",
            "confirmation_reference": "Карточка от 01.01.2026",
            "active": True,
        }
        denied = await self.client.post("/api/rumex-registry/carriers", headers=user_headers, json=payload)
        self.assertEqual(denied.status, 403)

        admin_token = await self._login()
        admin_headers = {"Cookie": f"{auth.COOKIE_NAME}={admin_token}"}
        created = await self.client.post("/api/rumex-registry/carriers", headers=admin_headers, json=payload)
        self.assertEqual(created.status, 201)
        carrier = (await created.json())["carrier"]
        self.assertEqual(carrier["inn"], "1234567890")

        listed = await self.client.get("/api/rumex-registry/carriers", headers=user_headers)
        self.assertEqual(listed.status, 200)
        self.assertEqual((await listed.json())["carriers"][0]["name"], payload["name"])

    async def test_document_fleet_is_readable_to_accountants_but_only_admin_can_confirm_binding(self):
        self._prepare_test_vehicle()
        user_token = await self._login("23456")
        user_headers = {"Cookie": f"{auth.COOKIE_NAME}={user_token}"}

        listed = await self.client.get("/api/rumex-registry/document-fleet", headers=user_headers)
        self.assertEqual(listed.status, 200)
        vehicle = (await listed.json())["vehicles"][0]
        self.assertEqual(vehicle["plate_tail"], "553")
        self.assertEqual(vehicle["document_binding"]["driver_full_name"], "Иванов Иван Иванович")

        denied = await self.client.post(
            "/api/rumex-registry/document-fleet/553/binding",
            headers=user_headers,
            json={"carrier_id": vehicle["document_binding"]["carrier_id"], "driver_full_name": "Петров Пётр"},
        )
        self.assertEqual(denied.status, 403)

        admin_token = await self._login()
        admin_headers = {"Cookie": f"{auth.COOKIE_NAME}={admin_token}"}
        detail = await self.client.get("/api/rumex-registry/document-fleet/553", headers=admin_headers)
        self.assertEqual(detail.status, 200)
        detail_body = await detail.json()
        self.assertEqual(len(detail_body["binding_history"]), 1)

        confirmed = await self.client.post(
            "/api/rumex-registry/document-fleet/553/binding",
            headers=admin_headers,
            json={
                "carrier_id": detail_body["vehicle"]["document_binding"]["carrier_id"],
                "driver_full_name": "Петров Пётр Петрович",
                "note": "Перепроверено по карточке перевозчика",
            },
        )
        self.assertEqual(confirmed.status, 201)
        self.assertEqual((await confirmed.json())["binding"]["driver_full_name"], "Петров Пётр Петрович")

    async def test_samples_are_authenticated_and_only_existing_files_are_linked(self):
        denied = await self.client.get("/api/rumex-registry/samples")
        self.assertEqual(denied.status, 401)

        token = await self._login()
        headers = {"Cookie": f"{auth.COOKIE_NAME}={token}"}
        response = await self.client.get("/api/rumex-registry/samples", headers=headers)
        self.assertEqual(response.status, 200)
        samples = {sample["id"]: sample for sample in (await response.json())["samples"]}
        self.assertTrue(samples["tn-pdf"]["available"])
        self.assertTrue(samples["tn-xls"]["available"])
        self.assertFalse(samples["er"]["available"])
        self.assertEqual(samples["er"]["view_url"], "")

        pdf = await self.client.get(samples["tn-pdf"]["view_url"], headers=headers)
        self.assertEqual(pdf.status, 200)
        self.assertEqual(pdf.content_type, "application/pdf")
        self.assertGreater(len(await pdf.read()), 0)

        xls = await self.client.get(samples["tn-xls"]["download_url"], headers=headers)
        self.assertEqual(xls.status, 200)
        self.assertEqual(xls.content_type, "application/vnd.ms-excel")
        self.assertIn("attachment", xls.headers.get("Content-Disposition", ""))

    async def test_test_dispatcher_flow_is_max_protected_and_accountant_opens_documents(self):
        self._prepare_test_vehicle()
        denied = await self.client.get("/api/rumex-registry/test/registry")
        self.assertEqual(denied.status, 401)
        forbidden = await self.client.get(
            "/api/rumex-registry/test/registry", headers=self._test_dispatcher_headers(9999)
        )
        self.assertEqual(forbidden.status, 403)

        headers = self._test_dispatcher_headers()
        created = await self.client.post(
            "/api/rumex-registry/test/shipments",
            headers={**headers, "Content-Type": "application/json"},
            json={
                "plate_tail": "553",
                "block_count": 1,
                "items": [{"letter": "A", "number": "3611"}],
                "loaded_at": 1_767_225_600.0,
            },
        )
        self.assertEqual(created.status, 201)
        shipment = (await created.json())["shipment"]
        self.assertEqual(shipment["status"], "awaiting_accountant_review")
        shipment_url = "/api/rumex-registry/test/shipments/" + str(shipment["id"])

        before_open = await self.client.get(shipment_url + "/documents/tn", headers=headers)
        self.assertEqual(before_open.status, 409)

        accountant_token = await self._login("23456")
        accountant_headers = {"Cookie": f"{auth.COOKIE_NAME}={accountant_token}"}
        reviewed = await self.client.post(
            shipment_url + "/review", headers=accountant_headers, json={"er_required": True}
        )
        self.assertEqual(reviewed.status, 200)
        self.assertEqual((await reviewed.json())["shipment"]["status"], "awaiting_er_sent")

        sent = await self.client.post(
            shipment_url + "/er-sent",
            headers=accountant_headers,
            json={"external_reference": "Контур-123"},
        )
        self.assertEqual(sent.status, 200)
        self.assertEqual((await sent.json())["shipment"]["status"], "documents_ready")

        document = await self.client.get(shipment_url + "/documents/tn", headers=headers)
        self.assertEqual(document.status, 200)
        self.assertEqual(
            document.content_type,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertIn("attachment", document.headers.get("Content-Disposition", ""))

    async def test_test_dispatcher_password_session_is_isolated_from_accountants(self):
        self._prepare_test_vehicle()
        invalid = await self.client.post(
            "/api/rumex-registry/test/auth",
            json={"username": "Тестовый диспетчер", "password": "wrong-password"},
        )
        self.assertEqual(invalid.status, 401)

        token = await self._test_password_login()
        headers = {test_dispatcher_auth.COOKIE_NAME: token}
        listed = await self.client.get("/api/rumex-registry/test/registry", cookies=headers)
        self.assertEqual(listed.status, 200)
        accountant_registry = await self.client.get("/api/rumex-registry/registry", cookies=headers)
        self.assertEqual(accountant_registry.status, 401)

        created = await self.client.post(
            "/api/rumex-registry/test/shipments",
            cookies=headers,
            json={
                "plate_tail": "553",
                "block_count": 1,
                "items": [{"letter": "A", "number": "4621"}],
                "loaded_at": 1_767_225_600.0,
            },
        )
        self.assertEqual(created.status, 201)
        shipment = (await created.json())["shipment"]
        self.assertEqual(shipment["dispatcher_identity_kind"], "password")
        self.assertEqual(shipment["dispatcher_identity_id"], "Тестовый диспетчер")
        self.assertEqual(shipment["events"][-1]["actor_kind"], "dispatcher_password")
        self.assertEqual(shipment["events"][-1]["actor_name"], "Тестовый диспетчер")
