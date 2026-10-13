"""Контракт `GET /api/v1/auth/me` без MySQL и без legacy-контура."""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from fastapi.testclient import TestClient
except ModuleNotFoundError:
    FASTAPI_AVAILABLE = False
else:
    FASTAPI_AVAILABLE = True
    from app.auth.models import AuthenticatedUser
    from app.core.settings import Settings
    from app.main import create_app
    from app.repositories.sessions import SessionRepositoryUnavailable


@unittest.skipUnless(FASTAPI_AVAILABLE, "Для FastAPI-тестов установите requirements-fastapi.txt")
class AuthMeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sessions = _FakeSessionRepository()
        self.settings = Settings(auth_secret="test-secret")
        self.client = TestClient(create_app(settings=self.settings, session_repository=self.sessions))

    def tearDown(self) -> None:
        self.client.close()

    def test_missing_cookie_is_unauthorized_without_database_call(self) -> None:
        response = self.client.get("/api/v1/auth/me")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Требуется вход"})
        self.assertEqual(self.sessions.token_hashes, [])

    def test_active_session_returns_user_and_effective_permissions(self) -> None:
        self.sessions.user = AuthenticatedUser(
            account_id=7,
            login="dispatcher",
            name="Диспетчер",
            role="dispatcher",
        )
        self.client.cookies.set(self.settings.auth_cookie_name, "session-token")

        response = self.client.get("/api/v1/auth/me")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"], {
            "id": 7,
            "login": "dispatcher",
            "name": "Диспетчер",
            "role": "dispatcher",
        })
        self.assertTrue(response.json()["permissions"]["trips"])
        self.assertFalse(response.json()["permissions"]["admin"])
        expected_hash = hmac.new(
            b"test-secret", b"session-token", hashlib.sha256,
        ).hexdigest()
        self.assertEqual(self.sessions.token_hashes, [expected_hash])

    def test_unknown_or_expired_session_is_unauthorized(self) -> None:
        self.client.cookies.set(self.settings.auth_cookie_name, "expired-token")

        response = self.client.get("/api/v1/auth/me")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Требуется вход"})

    def test_unavailable_session_store_does_not_reveal_connection_details(self) -> None:
        self.sessions.error = SessionRepositoryUnavailable("private MySQL error")
        self.client.cookies.set(self.settings.auth_cookie_name, "session-token")

        response = self.client.get("/api/v1/auth/me")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"detail": "Сервис авторизации недоступен"})


@unittest.skipUnless(FASTAPI_AVAILABLE, "Для FastAPI-тестов установите requirements-fastapi.txt")
class PermissionMappingTests(unittest.TestCase):
    def test_unknown_role_has_no_section_permissions(self) -> None:
        from app.auth.permissions import effective_permissions

        self.assertFalse(any(effective_permissions("unknown").values()))


class _FakeSessionRepository:
    def __init__(self) -> None:
        self.user = None
        self.error: Exception | None = None
        self.token_hashes: list[str] = []

    async def find_active_user(self, token_hash: str):
        self.token_hashes.append(token_hash)
        if self.error is not None:
            raise self.error
        return self.user


if __name__ == "__main__":
    unittest.main()
