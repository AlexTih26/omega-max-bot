"""ASGI-проверки временной capability `trip.create` без MySQL."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from fastapi import APIRouter, Depends
    from fastapi.testclient import TestClient
except ModuleNotFoundError:
    FASTAPI_AVAILABLE = False
else:
    FASTAPI_AVAILABLE = True
    from typing import Annotated

    from app.auth.dependencies import require_permission
    from app.auth.models import AuthenticatedUser
    from app.core.settings import Settings
    from app.main import create_app


@unittest.skipUnless(FASTAPI_AVAILABLE, "Для FastAPI-тестов установите requirements-fastapi.txt")
class TripCreatePermissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sessions = _FakeSessionRepository()
        self.settings = Settings(auth_secret="test-secret")
        app = create_app(settings=self.settings, session_repository=self.sessions)
        router = APIRouter()

        @router.get("/__test__/trip-create-permission")
        async def permission_probe(
            actor: Annotated[AuthenticatedUser, Depends(require_permission("trip.create", "edit"))],
        ) -> dict[str, int]:
            return {"account_id": actor.account_id}

        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()

    def test_missing_session_is_rejected_before_permission_check(self) -> None:
        response = self.client.get("/__test__/trip-create-permission")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Требуется вход"})
        self.assertEqual(self.sessions.token_hashes, [])

    def test_dispatcher_and_admin_have_temporary_trip_create_permission(self) -> None:
        for account_id, role in ((7, "dispatcher"), (8, "admin")):
            with self.subTest(role=role):
                self.sessions.user = AuthenticatedUser(
                    account_id=account_id,
                    login=role,
                    name="Тестовый пользователь",
                    role=role,
                )
                self.client.cookies.set(self.settings.auth_cookie_name, role)

                response = self.client.get("/__test__/trip-create-permission")

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"account_id": account_id})

    def test_accountant_is_forbidden(self) -> None:
        self.sessions.user = AuthenticatedUser(
            account_id=9,
            login="accountant1",
            name="Бухгалтер",
            role="accountant1",
        )
        self.client.cookies.set(self.settings.auth_cookie_name, "accountant-token")

        response = self.client.get("/__test__/trip-create-permission")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"detail": "Недостаточно прав"})

    def test_unsupported_capability_fails_during_route_setup(self) -> None:
        with self.assertRaises(ValueError):
            require_permission("trip.delete", "edit")


class _FakeSessionRepository:
    def __init__(self) -> None:
        self.user: AuthenticatedUser | None = None
        self.token_hashes: list[str] = []

    async def find_active_user(self, token_hash: str) -> AuthenticatedUser | None:
        self.token_hashes.append(token_hash)
        return self.user


if __name__ == "__main__":
    unittest.main()
