"""ASGI-контракт незарегистрированного создания рейса без MySQL и legacy-кода."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from uuid import UUID


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from fastapi.testclient import TestClient
except ModuleNotFoundError:
    FASTAPI_AVAILABLE = False
else:
    FASTAPI_AVAILABLE = True
    from app.api.dispatcher_trips import create_dispatcher_trips_router
    from app.auth.models import AuthenticatedUser
    from app.core.settings import Settings
    from app.main import create_app
    from app.repositories.trips import (
        CreateTripCommand,
        NotImplementedTripRepository,
        TripCreationResult,
    )
    from app.trips.contracts import (
        TripBlockResponse,
        TripCarrierResponse,
        TripDriverResponse,
        TripResponse,
        TripVehicleResponse,
    )
    from app.trips.service import (
        IdempotencyKeyReused,
        RepositoryTripService,
        TripReferenceNotFound,
    )


@unittest.skipUnless(FASTAPI_AVAILABLE, "Для FastAPI-тестов установите requirements-fastapi.txt")
class DispatcherTripsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sessions = _FakeSessionRepository()
        self.settings = Settings(auth_secret="test-secret")
        self.repository = _FakeTripRepository()
        app = create_app(settings=self.settings, session_repository=self.sessions)
        app.include_router(create_dispatcher_trips_router(RepositoryTripService(self.repository)))
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()

    def test_plain_create_app_does_not_register_endpoint_or_use_sessions(self) -> None:
        sessions = _FakeSessionRepository()
        client = TestClient(create_app(settings=self.settings, session_repository=sessions))
        client.cookies.set(self.settings.auth_cookie_name, "session-token")
        try:
            response = client.post(
                "/api/v1/dispatcher/trips",
                headers={
                    "Content-Type": "application/json",
                    "Idempotency-Key": "11111111-1111-4111-8111-111111111111",
                },
                json=_create_payload(),
            )
        finally:
            client.close()

        self.assertEqual(response.status_code, 404)
        self.assertEqual(sessions.token_hashes, [])

    def test_missing_session_is_unauthorized(self) -> None:
        response = self.client.post("/api/v1/dispatcher/trips", headers=_headers(), json=_create_payload())

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Требуется вход"})
        self.assertEqual(self.sessions.token_hashes, [])

    def test_dispatcher_create_normalizes_blocks_and_returns_server_snapshots(self) -> None:
        self._authenticate_dispatcher()

        response = self.client.post("/api/v1/dispatcher/trips", headers=_headers(), json=_create_payload())

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json(), _expected_response())
        self.assertEqual(len(self.repository.created_commands), 1)
        command = self.repository.created_commands[0]
        self.assertEqual(command.actor.account_id, 7)
        self.assertEqual(command.request.blocks[0].letter, "A")
        self.assertEqual(command.request.blocks[0].number, "901")

    def test_same_key_and_normalized_request_replays_without_second_creation(self) -> None:
        self._authenticate_dispatcher()
        headers = _headers()

        first = self.client.post("/api/v1/dispatcher/trips", headers=headers, json=_create_payload())
        normalized_payload = _create_payload()
        normalized_payload["blocks"][0].update({"letter": "A", "number": "901"})
        replay = self.client.post("/api/v1/dispatcher/trips", headers=headers, json=normalized_payload)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json(), first.json())
        self.assertEqual(len(self.repository.created_commands), 1)

    def test_same_key_with_different_request_is_conflict(self) -> None:
        self._authenticate_dispatcher()
        headers = _headers()
        self.client.post("/api/v1/dispatcher/trips", headers=headers, json=_create_payload())
        changed = _create_payload()
        changed["blocks"][0]["number"] = "902"

        response = self.client.post("/api/v1/dispatcher/trips", headers=headers, json=changed)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "idempotency_key_reused")
        self.assertEqual(len(self.repository.created_commands), 1)

    def test_http_boundary_rejects_media_type_json_shape_and_noncanonical_key(self) -> None:
        self._authenticate_dispatcher()
        unsupported_media_type = self.client.post(
            "/api/v1/dispatcher/trips",
            headers={"Idempotency-Key": _headers()["Idempotency-Key"]},
            content="{}",
        )
        non_object_json = self.client.post(
            "/api/v1/dispatcher/trips",
            headers=_headers(),
            json=[],
        )
        uppercase_key_headers = _headers()
        uppercase_key_headers["Idempotency-Key"] = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa".upper()
        noncanonical_key = self.client.post(
            "/api/v1/dispatcher/trips",
            headers=uppercase_key_headers,
            json=_create_payload(),
        )

        self.assertEqual(unsupported_media_type.status_code, 415)
        self.assertEqual(unsupported_media_type.json()["error"]["code"], "unsupported_media_type")
        self.assertEqual(non_object_json.status_code, 400)
        self.assertEqual(non_object_json.json()["error"]["code"], "invalid_json")
        self.assertEqual(noncanonical_key.status_code, 400)
        self.assertEqual(noncanonical_key.json()["error"]["code"], "invalid_idempotency_key")
        self.assertEqual(self.repository.created_commands, [])

    def test_missing_key_and_extra_field_do_not_call_repository(self) -> None:
        self._authenticate_dispatcher()
        no_key = self.client.post(
            "/api/v1/dispatcher/trips",
            headers={"Content-Type": "application/json"},
            json=_create_payload(),
        )
        payload = _create_payload()
        payload["unexpected"] = "private input"
        extra_field = self.client.post("/api/v1/dispatcher/trips", headers=_headers(), json=payload)

        self.assertEqual(no_key.status_code, 400)
        self.assertEqual(no_key.json()["error"]["code"], "invalid_idempotency_key")
        self.assertEqual(extra_field.status_code, 422)
        self.assertEqual(extra_field.json(), {
            "error": {
                "code": "validation_error",
                "message": "Проверьте поля запроса",
                "fields": [{"field": "unexpected", "code": "unknown_field"}],
            },
        })
        self.assertNotIn("private input", extra_field.text)
        self.assertEqual(self.repository.created_commands, [])

    def test_not_implemented_repository_returns_safe_storage_error(self) -> None:
        sessions = _FakeSessionRepository()
        sessions.user = _dispatcher()
        app = create_app(settings=self.settings, session_repository=sessions)
        app.include_router(create_dispatcher_trips_router(RepositoryTripService(NotImplementedTripRepository())))
        client = TestClient(app)
        client.cookies.set(self.settings.auth_cookie_name, "session-token")
        try:
            response = client.post("/api/v1/dispatcher/trips", headers=_headers(), json=_create_payload())
        finally:
            client.close()

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "storage_unavailable")

    def _authenticate_dispatcher(self) -> None:
        self.sessions.user = _dispatcher()
        self.client.cookies.set(self.settings.auth_cookie_name, "session-token")


class _FakeSessionRepository:
    def __init__(self) -> None:
        self.user: AuthenticatedUser | None = None
        self.token_hashes: list[str] = []

    async def find_active_user(self, token_hash: str) -> AuthenticatedUser | None:
        self.token_hashes.append(token_hash)
        return self.user


class _FakeTripRepository:
    """Тестовое хранилище с изоляцией идемпотентности по актору и ключу."""

    def __init__(self) -> None:
        self._records: dict[tuple[int, UUID], tuple[str, TripResponse]] = {}
        self.created_commands: list[CreateTripCommand] = []

    async def create_trip(self, command: CreateTripCommand) -> TripCreationResult:
        self._validate_references(command)
        record_key = (command.actor.account_id, command.idempotency_key)
        previous = self._records.get(record_key)
        if previous is not None:
            fingerprint, trip = previous
            if fingerprint != command.fingerprint:
                raise IdempotencyKeyReused()
            return TripCreationResult(trip=trip, replayed=True)

        trip = _trip_from_command(command)
        self._records[record_key] = (command.fingerprint, trip)
        self.created_commands.append(command)
        return TripCreationResult(trip=trip, replayed=False)

    @staticmethod
    def _validate_references(command: CreateTripCommand) -> None:
        if command.request.vehicle_id != 42:
            raise TripReferenceNotFound("vehicle")
        if command.request.carrier_id != 7:
            raise TripReferenceNotFound("carrier")
        if command.request.driver_id != 19:
            raise TripReferenceNotFound("driver")


def _dispatcher() -> AuthenticatedUser:
    return AuthenticatedUser(account_id=7, login="dispatcher", name="Диспетчер", role="dispatcher")


def _headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Idempotency-Key": "11111111-1111-4111-8111-111111111111",
    }


def _create_payload() -> dict[str, object]:
    return {
        "trip_date": "2026-10-13",
        "vehicle_id": 42,
        "carrier_id": 7,
        "driver_id": 19,
        "blocks": [{"letter": "a", "number": " 90 1\t", "suffix": ""}],
    }


def _trip_from_command(command: CreateTripCommand) -> TripResponse:
    block = command.request.blocks[0]
    return TripResponse(
        public_id=UUID("08f1b37d-a5a3-4d32-8ee2-4ccd31a0231f"),
        status="draft",
        trip_date=command.request.trip_date,
        vehicle=TripVehicleResponse(id=42, plate="А123ВС75"),
        carrier=TripCarrierResponse(id=7, legal_form="ООО", name="Перевозчик", inn="1234567890"),
        driver=TripDriverResponse(id=19, full_name="Иванов Иван Иванович", license_number="75 АА 123456"),
        block_count=1,
        total_weight_kg=7780,
        blocks=[
            TripBlockResponse(
                letter=block.letter,
                number=block.number,
                suffix=block.suffix,
                weight_kg=7780,
                price_rub="0.00",
            ),
        ],
        created_at=datetime(2026, 10, 13, 19, 27, 8, tzinfo=timezone.utc),
    )


def _expected_response() -> dict[str, object]:
    return {
        "trip": {
            "public_id": "08f1b37d-a5a3-4d32-8ee2-4ccd31a0231f",
            "status": "draft",
            "trip_date": "2026-10-13",
            "vehicle": {"id": 42, "plate": "А123ВС75"},
            "carrier": {"id": 7, "legal_form": "ООО", "name": "Перевозчик", "inn": "1234567890"},
            "driver": {"id": 19, "full_name": "Иванов Иван Иванович", "license_number": "75 АА 123456"},
            "block_count": 1,
            "total_weight_kg": 7780,
            "blocks": [{"letter": "A", "number": "901", "suffix": "", "weight_kg": 7780, "price_rub": "0.00"}],
            "created_at": "2026-10-13T19:27:08Z",
        },
    }


if __name__ == "__main__":
    unittest.main()
