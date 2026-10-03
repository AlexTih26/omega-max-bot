"""Проверки долговечного серверного моста РУМЕКС и новой Таксимо."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
BOT = ROOT / "fotonych-bot"
if str(BOT) not in sys.path:
    sys.path.insert(0, str(BOT))

import rumex_taksimo_bridge as bridge  # noqa: E402


class _Response:
    def __init__(self, status: int, body: object) -> None:
        self.status = status
        self.body = body

    async def json(self, **_kwargs) -> object:
        return self.body

    async def text(self) -> str:
        return str(self.body)


class _Request:
    def __init__(self, response: _Response) -> None:
        self.response = response

    async def __aenter__(self) -> _Response:
        return self.response

    async def __aexit__(self, _type, _value, _traceback) -> None:
        return None


class _Session:
    def __init__(self, responses: list[_Response], order: list[str] | None = None) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.order = order

    def post(self, url: str, **kwargs) -> _Request:
        self.calls.append({"url": url, **kwargs})
        if self.order is not None:
            self.order.append("ack" if url.endswith("/ack") else "failure" if url.endswith("/failure") else "post")
        return _Request(self.responses.pop(0))


class RumexTaksimoBridgeTests(unittest.TestCase):
    def test_snapshot_delivery_records_new_taksimo_receipt(self) -> None:
        session = _Session([_Response(201, {"intake": {"public_id": "intake-1"}})])
        entry = {
            "outbox_id": 12,
            "idempotency_key": "rumex-shipment:1:v1",
            "snapshot": {"contract_version": 1, "rumex_shipment_id": 1},
        }
        with patch.object(bridge.rumex_store, "record_taksimo_snapshot_delivery") as recorded:
            asyncio.run(bridge._post_snapshot(session, entry))

        self.assertTrue(session.calls[0]["url"].endswith("/integration/rumex/expected-intakes"))
        self.assertEqual(session.calls[0]["headers"]["Idempotency-Key"], "rumex-shipment:1:v1")
        self.assertEqual(session.calls[0]["json"], entry["snapshot"])
        recorded.assert_called_once_with(
            12,
            success=True,
            http_status=201,
            result_note="HTTP 201",
            delivery_reference="intake-1",
        )

    def test_physical_event_is_acknowledged_only_after_rumex_write(self) -> None:
        order: list[str] = []
        session = _Session([_Response(200, {"ok": True})], order)
        event = {
            "public_id": "event-1",
            "event_type": "taksimo.wagon.in_transit",
            "created_at": "2026-01-01T12:00:00Z",
            "payload": {
                "rumex_shipment_id": 1,
                "rumex_document_version": 1,
                "physical_owner": "taksimo_new",
            },
        }

        def record(**kwargs) -> bool:
            order.append("rumex-write")
            self.assertEqual(kwargs["source_event_public_id"], "event-1")
            return True

        with patch.object(bridge.rumex_store, "record_taksimo_physical_event", side_effect=record):
            accepted = asyncio.run(bridge._record_or_reject_physical_event(session, event))

        self.assertTrue(accepted)
        self.assertEqual(order, ["rumex-write", "ack"])
        self.assertTrue(session.calls[0]["url"].endswith("/integration/outbox/event-1/ack"))
        self.assertEqual(
            session.calls[0]["json"], {"receipt_reference": "rumex-physical-event:event-1"}
        )

    def test_invalid_physical_event_is_journaled_without_acknowledgement(self) -> None:
        session = _Session([_Response(200, {"ok": True})])
        event = {
            "public_id": "event-2",
            "event_type": "taksimo.wagon.in_transit",
            "created_at": "2026-01-01T12:00:00Z",
            "payload": {"rumex_shipment_id": 1},
        }
        with patch.object(
            bridge.rumex_store,
            "record_taksimo_physical_event",
            side_effect=ValueError("Факт не связан с подтверждённым снимком РУМЕКС"),
        ):
            accepted = asyncio.run(bridge._record_or_reject_physical_event(session, event))

        self.assertFalse(accepted)
        self.assertEqual(len(session.calls), 1)
        self.assertTrue(session.calls[0]["url"].endswith("/integration/outbox/event-2/failure"))
        self.assertEqual(
            session.calls[0]["json"], {"result_note": "Факт не связан с подтверждённым снимком РУМЕКС"}
        )

