"""Контракт liveness- и readiness-маршрутов FastAPI без MySQL."""

from __future__ import annotations

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
    from app.core.settings import Settings
    from app.main import create_app
    from app.repositories.sessions import SessionRepositoryUnavailable


@unittest.skipUnless(FASTAPI_AVAILABLE, "Для FastAPI-тестов установите requirements-fastapi.txt")
class HealthRoutesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sessions = _FakeSessionRepository()
        self.client = TestClient(
            create_app(
                settings=Settings(
                    auth_secret="test-secret",
                    mysql_user="test-user",
                    mysql_password="test-password",
                ),
                session_repository=self.sessions,
                permission_repository=_FakePermissionRepository(),
            )
        )

    def tearDown(self) -> None:
        self.client.close()

    def test_healthz_confirms_asgi_process_without_touching_mysql(self) -> None:
        response = self.client.get("/api/v1/healthz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertEqual(self.sessions.ready_checks, 0)

    def test_readyz_confirms_configured_read_only_mysql_access(self) -> None:
        response = self.client.get("/api/v1/readyz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ready"})
        self.assertEqual(self.sessions.ready_checks, 1)

    def test_readyz_rejects_incomplete_configuration_without_database_call(self) -> None:
        client = TestClient(
            create_app(
                settings=Settings(),
                session_repository=self.sessions,
                permission_repository=_FakePermissionRepository(),
            )
        )
        try:
            response = client.get("/api/v1/readyz")
        finally:
            client.close()

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})
        self.assertEqual(self.sessions.ready_checks, 0)

    def test_readyz_hides_mysql_failure(self) -> None:
        self.sessions.error = SessionRepositoryUnavailable("private MySQL error")

        response = self.client.get("/api/v1/readyz")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})
        self.assertEqual(self.sessions.ready_checks, 1)


class _FakeSessionRepository:
    def __init__(self) -> None:
        self.ready_checks = 0
        self.error: Exception | None = None

    async def find_active_user(self, token_hash: str) -> None:
        return None

    async def check_ready(self) -> None:
        self.ready_checks += 1
        if self.error is not None:
            raise self.error


class _FakePermissionRepository:
    async def get_capability(self, account_id: int, capability: str) -> None:
        return None


if __name__ == "__main__":
    unittest.main()
