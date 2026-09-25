"""Проверки изолированного HTTP-контура новой Таксимо без рабочей БД."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

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

if AIOHTTP_AVAILABLE:
    import comments_api  # noqa: E402
    import taksimo_new_api as api  # noqa: E402
    import taksimo_new_auth as auth  # noqa: E402


@unittest.skipUnless(AIOHTTP_AVAILABLE, "Для HTTP-тестов требуется aiohttp")
class TaksimoNewApiTests(AioHTTPTestCase):
    async def get_application(self):
        app = web.Application(middlewares=[auth.taksimo_new_auth_middleware])
        auth.register_taksimo_new_auth_routes(app)
        api.register_taksimo_new_routes(app)
        return app

    async def test_integration_is_closed_when_token_is_not_configured(self):
        with patch.dict(os.environ, {"TAKSIMO_NEW_INTEGRATION_TOKEN": ""}, clear=False):
            response = await self.client.post(
                "/api/taksimo-new/integration/rumex/expected-intakes",
                json={"external_reference": "test", "expected_blocks": [{"block_type": "A", "block_number": "1"}]},
            )
        self.assertEqual(response.status, 503)
        self.assertIn("ещё не настроен", (await response.json())["error"])

    async def test_integration_rejects_wrong_bearer_token(self):
        with patch.dict(os.environ, {"TAKSIMO_NEW_INTEGRATION_TOKEN": "test-token"}, clear=False):
            response = await self.client.get(
                "/api/taksimo-new/integration/outbox",
                headers={"Authorization": "Bearer wrong-token"},
            )
        self.assertEqual(response.status, 401)

    async def test_integration_passes_idempotency_key_only_after_authorization(self):
        intake = {"public_id": "4b4b59af-8774-4df8-b981-263915ce6ea4", "status": "expected"}
        with patch.dict(os.environ, {"TAKSIMO_NEW_INTEGRATION_TOKEN": "test-token"}, clear=False):
            with patch.object(api.store, "import_expected_intake", return_value=intake) as imported:
                response = await self.client.post(
                    "/api/taksimo-new/integration/rumex/expected-intakes",
                    headers={"Authorization": "Bearer test-token", "Idempotency-Key": "rumex-test-1"},
                    json={"external_reference": "test", "expected_blocks": [{"block_type": "A", "block_number": "1"}]},
                )
        self.assertEqual(response.status, 201)
        self.assertEqual((await response.json())["intake"], intake)
        imported.assert_called_once()
        self.assertEqual(imported.call_args.kwargs["idempotency_key"], "rumex-test-1")

    async def test_operation_cancellation_passes_the_confirmed_operation_to_store(self):
        operator = {"id": 1, "role": "operator1", "name": "Оператор 1"}
        cancellation = {"id": "d92c95fa-bbaa-46bd-ae96-fbca16d8e746", "subject_type": "wagon_load"}
        with patch.object(auth, "auth_status", return_value={"configured": True, "database": True}):
            with patch.object(auth, "operator_from_request", return_value=operator):
                with patch.object(api, "operator_from_request", return_value=operator):
                    with patch.object(api.store, "cancel_operation", return_value=cancellation) as cancelled:
                        response = await self.client.post(
                            "/api/taksimo-new/operations/wagon_load/42/cancel",
                            json={"reason": "Исправление первичного документа"},
                        )
        self.assertEqual(response.status, 201)
        self.assertEqual((await response.json())["cancellation"], cancellation)
        self.assertEqual(cancelled.call_args.kwargs["subject_type"], "wagon_load")
        self.assertEqual(cancelled.call_args.kwargs["subject_public_id"], "42")
        self.assertEqual(cancelled.call_args.kwargs["reason"], "Исправление первичного документа")
        self.assertEqual(cancelled.call_args.kwargs["operator"], operator)


@unittest.skipUnless(AIOHTTP_AVAILABLE, "Для проверок авторизации требуется aiohttp")
class TaksimoNewAuthStatusTests(unittest.TestCase):
    def test_database_status_does_not_depend_on_cookie_secret(self):
        with patch.dict(os.environ, {"TAKSIMO_NEW_AUTH_SECRET": ""}, clear=False):
            with patch.object(auth.store, "operator_accounts_ready", return_value=True):
                self.assertEqual(auth.auth_status(), {"configured": False, "database": True})


@unittest.skipUnless(AIOHTTP_AVAILABLE, "Для проверок middleware требуется aiohttp")
class TaksimoNewLegacyBoundaryTests(unittest.TestCase):
    def test_new_prefix_bypasses_legacy_taksimo_auth(self):
        from types import SimpleNamespace
        import asyncio

        request = SimpleNamespace(path="/api/taksimo-new/auth/check")

        async def handler(_request):
            return "new-api"

        async def legacy_auth(_request, _handler):
            raise AssertionError("Старый middleware не должен вызываться для нового префикса")

        with patch.object(comments_api, "taksimo_auth_middleware", legacy_auth):
            result = asyncio.run(comments_api.isolated_taksimo_auth_middleware(request, handler))
        self.assertEqual(result, "new-api")
