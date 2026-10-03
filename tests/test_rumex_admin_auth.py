"""HTTP-тесты двух независимых способов входа в управление РУМЕКС."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
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
    import rumex_admin_auth as auth  # noqa: E402
    import rumex_management_api as management_api  # noqa: E402


@unittest.skipUnless(AIOHTTP_AVAILABLE, "Для HTTP-тестов требуется aiohttp")
class RumexAdminAuthTests(AioHTTPTestCase):
    async def get_application(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._old_db_path = store.DB_PATH
        self._old_environment = {
            name: os.environ.get(name)
            for name in ("RUMEX_ADMIN_AUTH_SECRET", "MAX_BOT_TOKEN")
        }
        store.DB_PATH = Path(self._tmpdir.name) / "rumex_registry.db"
        os.environ["RUMEX_ADMIN_AUTH_SECRET"] = "test-management-session-secret"
        os.environ["MAX_BOT_TOKEN"] = "test-max-bot-token"

        app = web.Application(middlewares=[auth.rumex_admin_auth_middleware])
        auth.register_rumex_admin_auth_routes(app)
        management_api.register_rumex_management_routes(app)
        return app

    async def asyncTearDown(self) -> None:
        await super().asyncTearDown()
        store.DB_PATH = self._old_db_path
        for name, value in self._old_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self._tmpdir.cleanup()

    def _provision_account(self, *, username: str = "owner", active: bool = True) -> dict:
        account = store.provision_rumex_admin_account(
            username=username,
            full_name="Тестовый владелец",
            max_user_id=987654,
            password_hash=auth.hash_browser_password("correct-password-123"),
            actor_name="Тест",
        )
        if not active:
            with store._connect() as conn:
                conn.execute(
                    "UPDATE rumex_admin_accounts SET active = 0 WHERE username = ?", (username,)
                )
        return account

    async def _password_login(self) -> str:
        response = await self.client.post(
            "/api/rumex-management/auth/password",
            json={"username": "owner", "password": "correct-password-123"},
        )
        self.assertEqual(response.status, 200)
        body = await response.json()
        self.assertEqual(body, {"ok": True, "user": "Тестовый владелец"})
        self.assertNotIn("password_hash", body)
        return response.cookies[auth.COOKIE_NAME].value

    def _max_init_data(self, user_id: int) -> str:
        user = json.dumps({"id": user_id, "first_name": "MAX", "last_name": "Владелец"}, separators=(",", ":"))
        pairs = [("auth_date", str(int(time.time()))), ("user", user)]
        launch_params = "\n".join(f"{key}={value}" for key, value in sorted(pairs))
        secret_key = hmac.new(
            b"WebAppData", os.environ["MAX_BOT_TOKEN"].encode("utf-8"), hashlib.sha256
        ).digest()
        signature = hmac.new(secret_key, launch_params.encode("utf-8"), hashlib.sha256).hexdigest()
        return "auth_date={}&user={}&hash={}".format(pairs[0][1], quote(user, safe=""), signature)

    async def test_browser_password_login_protects_management_and_uses_own_cookie(self):
        self._provision_account()

        denied = await self.client.get(
            "/api/rumex-management/profile",
            headers={
                "Cookie": "rumex_accountant_auth=accountant; rumex_test_dispatcher_auth=dispatcher; "
                "taksimo_auth=legacy; taksimo_new_auth=new"
            },
        )
        self.assertEqual(denied.status, 401)

        token = await self._password_login()
        profile = await self.client.get(
            "/api/rumex-management/profile",
            headers={"Cookie": f"{auth.COOKIE_NAME}={token}"},
        )
        self.assertEqual(profile.status, 200)
        self.assertEqual((await profile.json())["user"]["username"], "Тестовый владелец")
        self.assertEqual(profile.cookies[auth.COOKIE_NAME].value, token)

        with store._connect() as conn:
            conn.execute("UPDATE rumex_admin_accounts SET active = 0 WHERE username = 'owner'")
        deactivated = await self.client.get(
            "/api/rumex-management/profile",
            headers={"Cookie": f"{auth.COOKIE_NAME}={token}"},
        )
        self.assertEqual(deactivated.status, 401)

    async def test_browser_login_rejects_invalid_and_inactive_accounts_without_details(self):
        self._provision_account(active=False)

        inactive = await self.client.post(
            "/api/rumex-management/auth/password",
            json={"username": "owner", "password": "correct-password-123"},
        )
        self.assertEqual(inactive.status, 401)
        self.assertEqual((await inactive.json())["error"], "Неверный логин или пароль")

        missing = await self.client.post(
            "/api/rumex-management/auth/password",
            json={"username": "not-found", "password": "correct-password-123"},
        )
        self.assertEqual(missing.status, 401)
        self.assertEqual((await missing.json())["error"], "Неверный логин или пароль")

    async def test_browser_login_rate_limit_and_logout_revoke_management_session(self):
        self._provision_account()
        for _ in range(auth.MAX_FAILED_ATTEMPTS):
            failed = await self.client.post(
                "/api/rumex-management/auth/password",
                json={"username": "owner", "password": "wrong-password-123"},
            )
            self.assertEqual(failed.status, 401)

        limited = await self.client.post(
            "/api/rumex-management/auth/password",
            json={"username": "owner", "password": "correct-password-123"},
        )
        self.assertEqual(limited.status, 429)

        store.record_rumex_admin_login_attempt(
            success=True, remote_address="127.0.0.1", attempted_at=time.time() - auth.ATTEMPT_WINDOW_SECONDS
        )
        with store._connect() as conn:
            conn.execute(
                "UPDATE rumex_admin_login_attempts SET attempted_at = ? WHERE success = 0",
                (time.time() - auth.ATTEMPT_WINDOW_SECONDS - 1,),
            )
        token = await self._password_login()
        headers = {"Cookie": f"{auth.COOKIE_NAME}={token}"}
        logout = await self.client.post("/api/rumex-management/auth/logout", headers=headers)
        self.assertEqual(logout.status, 200)
        self.assertEqual(logout.cookies[auth.COOKIE_NAME]["max-age"], "0")
        revoked = await self.client.get("/api/rumex-management/profile", headers=headers)
        self.assertEqual(revoked.status, 401)

    async def test_existing_max_super_admin_login_remains_available(self):
        init_data = self._max_init_data(123456)
        with patch.object(auth, "is_super_admin", return_value=True):
            response = await self.client.post(
                "/api/rumex-management/auth", headers={"X-Max-Init-Data": init_data}
            )
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json()), {"ok": True, "user": "MAX Владелец"})
        self.assertIn(auth.COOKIE_NAME, response.cookies)

        direct_browser = await self.client.post("/api/rumex-management/auth")
        self.assertEqual(direct_browser.status, 401)
        self.assertEqual((await direct_browser.json())["error"], "Откройте кабинет из MAX")

    async def test_password_reset_keeps_existing_max_session(self):
        self._provision_account()
        max_token = "existing-max-session"
        store.create_rumex_admin_session(
            token_hash=auth._token_hash(max_token),
            username="MAX Владелец",
            max_user_id=987654,
            expires_at=time.time() + 3600,
        )

        store.provision_rumex_admin_account(
            username="owner",
            full_name="Тестовый владелец",
            max_user_id=987654,
            password_hash=auth.hash_browser_password("replacement-password-123"),
            actor_name="Тест",
        )
        self.assertIsNotNone(
            store.rumex_admin_session_identity(token_hash=auth._token_hash(max_token))
        )
