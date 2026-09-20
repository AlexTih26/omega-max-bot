"""HTTP tests for the separate PIN-protected RUMEX accountant registry."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

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
    import rumex_registry_backup as backup  # noqa: E402
    import rumex_registry_documents as documents  # noqa: E402
    import rumex_test_dispatcher_auth as test_dispatcher_auth  # noqa: E402


@unittest.skipUnless(AIOHTTP_AVAILABLE, "Для HTTP-тестов требуется aiohttp")
class RumexRegistryApiTests(AioHTTPTestCase):
    async def get_application(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._old_db_path = store.DB_PATH
        self._old_backup_db_path = backup.DB_PATH
        self._old_backup_dir = backup.BACKUP_DIR
        self._old_samples_dir = api.REGISTRY_SAMPLES_DIR
        self._old_ttn_archive_dir = documents.TEST_TTN_ARCHIVE_DIR
        self._old_users = os.environ.get("RUMEX_ACCOUNTANT_USERS")
        self._old_secret = os.environ.get("RUMEX_ACCOUNTANT_AUTH_SECRET")
        self._old_test_dispatcher_users = os.environ.get("RUMEX_TEST_DISPATCHER_USERS")
        self._old_test_dispatcher_secret = os.environ.get("RUMEX_TEST_DISPATCHER_AUTH_SECRET")
        store.DB_PATH = Path(self._tmpdir.name) / "rumex_registry.db"
        backup.DB_PATH = store.DB_PATH
        backup.BACKUP_DIR = Path(self._tmpdir.name) / "backups"
        api.REGISTRY_SAMPLES_DIR = Path(self._tmpdir.name) / "registry_samples"
        api.REGISTRY_SAMPLES_DIR.mkdir()
        documents.TEST_TTN_ARCHIVE_DIR = Path(self._tmpdir.name) / "ttn_archive"
        (api.REGISTRY_SAMPLES_DIR / "8102 образец.pdf").write_bytes(b"%PDF-test-sample")
        (api.REGISTRY_SAMPLES_DIR / "8102 образец ТТН.xls").write_bytes(b"test-xls-sample")
        os.environ["RUMEX_ACCOUNTANT_USERS"] = (
            "Бухгалтер 1:12345,Бухгалтер 2:23456,Бухгалтер 3:34567"
        )
        os.environ["RUMEX_ACCOUNTANT_AUTH_SECRET"] = "test-secret"
        os.environ["RUMEX_TEST_DISPATCHER_USERS"] = "Тестовый диспетчер:password-123"
        os.environ["RUMEX_TEST_DISPATCHER_AUTH_SECRET"] = "test-dispatcher-secret"

        app = web.Application(
            middlewares=[
                auth.rumex_registry_auth_middleware,
                test_dispatcher_auth.rumex_test_dispatcher_auth_middleware,
            ]
        )
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
        backup.DB_PATH = self._old_backup_db_path
        backup.BACKUP_DIR = self._old_backup_dir
        api.REGISTRY_SAMPLES_DIR = self._old_samples_dir
        documents.TEST_TTN_ARCHIVE_DIR = self._old_ttn_archive_dir
        if self._old_users is None:
            os.environ.pop("RUMEX_ACCOUNTANT_USERS", None)
        else:
            os.environ["RUMEX_ACCOUNTANT_USERS"] = self._old_users
        if self._old_secret is None:
            os.environ.pop("RUMEX_ACCOUNTANT_AUTH_SECRET", None)
        else:
            os.environ["RUMEX_ACCOUNTANT_AUTH_SECRET"] = self._old_secret
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
            driver_license_number="38 12 123456",
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

    async def test_client_error_report_is_available_before_login_and_validated(self):
        reported = await self.client.post(
            "/api/rumex-registry/client-errors",
            json={
                "kind": "error",
                "message": "Unexpected interface error",
                "source": "/rumex-test-loading.js",
                "line": 123,
                "column": 4,
            },
        )
        self.assertEqual(reported.status, 202)
        self.assertTrue((await reported.json())["ok"])

        invalid = await self.client.post(
            "/api/rumex-registry/client-errors",
            json={"kind": "error", "message": ""},
        )
        self.assertEqual(invalid.status, 400)

    async def test_test_registry_export_requires_login_and_returns_excel(self):
        denied = await self.client.get("/api/rumex-registry/test/registry/export")
        self.assertEqual(denied.status, 401)

        token = await self._test_password_login()
        exported = await self.client.get(
            "/api/rumex-registry/test/registry/export?search=%D0%A0%D0%9C-2026",
            headers={"Cookie": f"{test_dispatcher_auth.COOKIE_NAME}={token}"},
        )
        self.assertEqual(exported.status, 200)
        self.assertEqual(
            exported.content_type,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertTrue((await exported.read()).startswith(b"PK"))

    async def test_active_accountant_session_renews_database_and_cookie(self):
        token = "accountant-renewal-token"
        started_at = time.time()
        store.create_accountant_session(
            token_hash=auth._token_hash(token),
            username="Бухгалтер 1",
            expires_at=started_at + 60,
            created_at=started_at,
        )

        response = await self.client.get(
            "/api/rumex-registry/test/registry",
            headers={"Cookie": f"{auth.COOKIE_NAME}={token}"},
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(response.cookies[auth.COOKIE_NAME].value, token)
        self.assertEqual(
            response.cookies[auth.COOKIE_NAME]["max-age"], str(auth.SESSION_DAYS * 86400)
        )
        with sqlite3.connect(store.DB_PATH) as conn:
            expires_at, last_seen_at = conn.execute(
                "SELECT expires_at, last_seen_at FROM accountant_sessions WHERE token_hash = ?",
                (auth._token_hash(token),),
            ).fetchone()
        self.assertGreater(expires_at, started_at + auth.SESSION_DAYS * 86400 - 5)
        self.assertGreaterEqual(last_seen_at, started_at)

    async def test_active_dispatcher_session_renews_database_and_cookie(self):
        token = "dispatcher-renewal-token"
        started_at = time.time()
        store.create_test_dispatcher_session(
            token_hash=test_dispatcher_auth._token_hash(token),
            username="Тестовый диспетчер",
            expires_at=started_at + 60,
            created_at=started_at,
        )

        response = await self.client.get(
            "/api/rumex-registry/test/registry",
            headers={"Cookie": f"{test_dispatcher_auth.COOKIE_NAME}={token}"},
        )
        self.assertEqual(response.status, 200)
        self.assertEqual(response.cookies[test_dispatcher_auth.COOKIE_NAME].value, token)
        self.assertEqual(
            response.cookies[test_dispatcher_auth.COOKIE_NAME]["max-age"],
            str(test_dispatcher_auth.SESSION_HOURS * 3600),
        )
        with sqlite3.connect(store.DB_PATH) as conn:
            expires_at, last_seen_at = conn.execute(
                "SELECT expires_at, last_seen_at FROM test_dispatcher_sessions WHERE token_hash = ?",
                (test_dispatcher_auth._token_hash(token),),
            ).fetchone()
        self.assertGreater(expires_at, started_at + test_dispatcher_auth.SESSION_HOURS * 3600 - 5)
        self.assertGreaterEqual(last_seen_at, started_at)

    async def test_logout_revokes_sessions_without_cookie_renewal(self):
        accountant_token = await self._login()
        accountant_headers = {"Cookie": f"{auth.COOKIE_NAME}={accountant_token}"}
        accountant_logout = await self.client.post(
            "/api/rumex-registry/auth/logout", headers=accountant_headers
        )
        self.assertEqual(accountant_logout.status, 200)
        self.assertEqual(accountant_logout.cookies[auth.COOKIE_NAME]["max-age"], "0")
        accountant_registry = await self.client.get(
            "/api/rumex-registry/registry", headers=accountant_headers
        )
        self.assertEqual(accountant_registry.status, 401)

        dispatcher_token = await self._test_password_login()
        dispatcher_headers = {"Cookie": f"{test_dispatcher_auth.COOKIE_NAME}={dispatcher_token}"}
        dispatcher_logout = await self.client.post(
            "/api/rumex-registry/test/auth/logout", headers=dispatcher_headers
        )
        self.assertEqual(dispatcher_logout.status, 200)
        self.assertEqual(
            dispatcher_logout.cookies[test_dispatcher_auth.COOKIE_NAME]["max-age"], "0"
        )
        dispatcher_registry = await self.client.get(
            "/api/rumex-registry/test/registry", headers=dispatcher_headers
        )
        self.assertEqual(dispatcher_registry.status, 401)

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
                "driver_license_number": "38 12 654321",
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

    async def test_test_dispatcher_flow_requires_password_and_accountant_opens_documents(self):
        self._prepare_test_vehicle()
        denied = await self.client.get("/api/rumex-registry/test/registry")
        self.assertEqual(denied.status, 401)
        dispatcher_token = await self._test_password_login()
        headers = {"Cookie": f"{test_dispatcher_auth.COOKIE_NAME}={dispatcher_token}"}
        created = await self.client.post(
            "/api/rumex-registry/test/shipments",
            headers={**headers, "Content-Type": "application/json"},
            json={
                "plate_tail": "553",
                "block_count": 3,
                "items": [
                    {"letter": "A", "number": "3611"},
                    {"letter": "K", "number": "7741"},
                    {"letter": "A", "number": "3612"},
                ],
                "loaded_at": 1_767_225_600.0,
            },
        )
        self.assertEqual(created.status, 201)
        shipment = (await created.json())["shipment"]
        backups = list(backup.BACKUP_DIR.glob("rumex-registry-*.db"))
        self.assertEqual(len(backups), 1)
        self.assertIn("reason=test_shipment_created", backups[0].with_suffix(".meta.txt").read_text(encoding="utf-8"))
        self.assertEqual(shipment["status"], "awaiting_accountant_review")
        self.assertTrue(shipment["is_new_for_accountant"])
        shipment_url = "/api/rumex-registry/test/shipments/" + str(shipment["id"])

        accountant_token = await self._login("23456")
        accountant_headers = {"Cookie": f"{auth.COOKIE_NAME}={accountant_token}"}
        claimed = await self.client.post(shipment_url + "/claim", headers=accountant_headers, json={})
        self.assertEqual(claimed.status, 200)
        self.assertEqual((await claimed.json())["shipment"]["task_taken_by"], "Бухгалтер 2")
        first_token = await self._login("12345")
        first_headers = {"Cookie": f"{auth.COOKIE_NAME}={first_token}"}
        unavailable = await self.client.post(shipment_url + "/claim", headers=first_headers, json={})
        self.assertEqual(unavailable.status, 409)

        before_open = await self.client.get(shipment_url + "/documents/tn?copy=1", headers=headers)
        self.assertEqual(before_open.status, 409)

        reviewed = await self.client.post(
            shipment_url + "/review", headers=accountant_headers, json={"er_required": True}
        )
        self.assertEqual(reviewed.status, 200)
        reviewed_shipment = (await reviewed.json())["shipment"]
        self.assertEqual(reviewed_shipment["status"], "awaiting_er_sent")
        self.assertFalse(reviewed_shipment["is_new_for_accountant"])
        self.assertEqual(reviewed_shipment["ttn_number"], "ТТН №РМ-2026-000001")

        sent = await self.client.post(
            shipment_url + "/er-sent",
            headers=accountant_headers,
            json={"external_reference": "Контур-123"},
        )
        self.assertEqual(sent.status, 200)
        sent_shipment = (await sent.json())["shipment"]
        self.assertEqual(sent_shipment["status"], "documents_ready")
        self.assertFalse(sent_shipment["is_new_for_accountant"])

        accountant_document = await self.client.get(
            shipment_url + "/documents/tn?copy=1", headers=accountant_headers
        )
        self.assertEqual(accountant_document.status, 200)
        accountant_shipment = await self.client.get(shipment_url, headers=accountant_headers)
        self.assertIsNone((await accountant_shipment.json())["shipment"]["ttn_printed_at"])

        missing_copy = await self.client.get(shipment_url + "/documents/tn", headers=headers)
        self.assertEqual(missing_copy.status, 400)
        for copy_number in (1, 2, 3, 4):
            document = await self.client.get(
                shipment_url + "/documents/tn?copy=" + str(copy_number), headers=headers
            )
            self.assertEqual(document.status, 200)
            self.assertEqual(
                document.content_type,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            self.assertIn("attachment", document.headers.get("Content-Disposition", ""))
            self.assertIn("%E2%84%96", document.headers.get("Content-Disposition", ""))

        dispatcher_shipment = await self.client.get(shipment_url, headers=headers)
        self.assertEqual(dispatcher_shipment.status, 200)
        dispatcher_body = await dispatcher_shipment.json()
        self.assertTrue(dispatcher_body["shipment"]["ttn_printed_at"])
        self.assertEqual(dispatcher_body["shipment"]["ttn_downloaded_copies"], [1, 2, 3, 4])

        handed = await self.client.post(shipment_url + "/handed-to-driver", headers=headers)
        self.assertEqual(handed.status, 200)
        handed_shipment = (await handed.json())["shipment"]
        self.assertEqual(handed_shipment["status"], "documents_handed_to_driver")
        self.assertEqual([item["copy_number"] for item in handed_shipment["ttn_archives"]], [1, 2, 3, 4])
        archived_document = await self.client.get(shipment_url + "/documents/tn?copy=1", headers=accountant_headers)
        self.assertEqual(archived_document.status, 200)
        archive_path = documents.TEST_TTN_ARCHIVE_DIR / handed_shipment["ttn_archives"][0]["filename"]
        self.assertTrue(archive_path.is_file())
        self.assertEqual(await archived_document.read(), archive_path.read_bytes())

    async def test_test_vehicle_requires_driver_license_before_creating_shipment(self):
        self._prepare_test_vehicle()
        with sqlite3.connect(store.DB_PATH) as conn:
            conn.execute("DROP TRIGGER document_vehicle_bindings_no_update")
            conn.execute("UPDATE document_vehicle_bindings SET driver_license_number = ''")
        dispatcher_token = await self._test_password_login()
        headers = {"Cookie": f"{test_dispatcher_auth.COOKIE_NAME}={dispatcher_token}"}

        vehicle = await self.client.get("/api/rumex-registry/test/vehicles/553", headers=headers)
        self.assertEqual(vehicle.status, 200)
        vehicle_body = await vehicle.json()
        self.assertFalse(vehicle_body["found"])
        self.assertIn("номера водительского удостоверения", vehicle_body["reason"])

        created = await self.client.post(
            "/api/rumex-registry/test/shipments",
            headers={**headers, "Content-Type": "application/json"},
            json={
                "plate_tail": "553",
                "block_count": 3,
                "items": [
                    {"letter": "A", "number": "3611"},
                    {"letter": "K", "number": "7741"},
                    {"letter": "A", "number": "3612"},
                ],
                "loaded_at": 1_767_225_600.0,
            },
        )
        self.assertEqual(created.status, 400)
        self.assertIn("нет номера водительского удостоверения", (await created.json())["error"])

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
                "block_count": 3,
                "items": [
                    {"letter": "A", "number": "4621"},
                    {"letter": "A", "number": "4622"},
                    {"letter": "K", "number": "7741"},
                ],
                "loaded_at": 1_767_225_600.0,
            },
        )
        self.assertEqual(created.status, 201)
        shipment = (await created.json())["shipment"]
        self.assertEqual(shipment["dispatcher_identity_kind"], "password")
        self.assertEqual(shipment["dispatcher_identity_id"], "Тестовый диспетчер")
        self.assertEqual(shipment["events"][-1]["actor_kind"], "dispatcher_password")
        self.assertEqual(shipment["events"][-1]["actor_name"], "Тестовый диспетчер")
