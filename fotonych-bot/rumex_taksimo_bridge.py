"""Однонаправленный серверный мост между РУМЕКС и новой Таксимо.

Мост работает только с неизменяемыми очередями двух контуров. Он не использует
браузерные сессии, PIN сотрудников, старую Таксимо или прямое соединение между
базами данных.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import os
from typing import Any, Mapping

try:
    from aiohttp import ClientError, ClientSession, ClientTimeout
except ModuleNotFoundError:  # Проверяется при запуске воркера.
    ClientError = Exception
    ClientSession = None
    ClientTimeout = None

import rumex_registry_store as rumex_store


logger = logging.getLogger(__name__)

DEFAULT_BRIDGE_URL = "http://127.0.0.1:8765"
POLL_SECONDS = 20
REQUEST_TIMEOUT_SECONDS = 15


def _setting(name: str, *, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def bridge_configured() -> bool:
    """Проверить конфигурацию, не раскрывая служебный токен в журналах."""
    return bool(_setting("TAKSIMO_NEW_INTEGRATION_TOKEN"))


def _base_url() -> str:
    return _setting("RUMEX_TAKSIMO_BRIDGE_URL", default=DEFAULT_BRIDGE_URL).rstrip("/")


def _headers(*, idempotency_key: str | None = None) -> dict[str, str]:
    headers = {
        "Authorization": "Bearer " + _setting("TAKSIMO_NEW_INTEGRATION_TOKEN"),
        "Accept": "application/json",
    }
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def _response_note(status: int | None, body: object) -> str:
    if isinstance(body, Mapping):
        text = body.get("error") or body.get("message") or body.get("detail") or ""
    else:
        text = str(body or "")
    normalized = " ".join(str(text).split())[:400]
    return (f"HTTP {status}: {normalized}" if normalized else f"HTTP {status}")[:500]


def _iso_to_timestamp(value: Any) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Некорректное время физического факта новой Таксимо") from exc
    if parsed.tzinfo is None:
        raise ValueError("Время физического факта новой Таксимо указано без часового пояса")
    return parsed.astimezone(timezone.utc).timestamp()


async def _post_snapshot(session: Any, entry: Mapping[str, Any], *, record_delivery) -> None:
    outbox_id = int(entry["outbox_id"])
    key = str(entry["idempotency_key"])
    try:
        async with session.post(
            _base_url() + "/api/taksimo-new/integration/rumex/expected-intakes",
            headers=_headers(idempotency_key=key),
            json=entry["snapshot"],
        ) as response:
            try:
                body = await response.json(content_type=None)
            except Exception:
                body = await response.text()
            status = response.status
    except (ClientError, asyncio.TimeoutError) as exc:
        record_delivery(
            outbox_id, success=False, result_note=f"Ошибка сети: {type(exc).__name__}"
        )
        return
    success = status in {200, 201}
    intake = body.get("intake") if isinstance(body, Mapping) else None
    delivery_reference = str(intake.get("public_id") or "") if isinstance(intake, Mapping) else ""
    record_delivery(
        outbox_id,
        success=success,
        http_status=status,
        result_note=_response_note(status, body),
        delivery_reference=delivery_reference,
    )


async def _post_document(session: Any, entry: Mapping[str, Any]) -> None:
    """Передать одну копию ТТН и записать попытку доставки в очереди РУМЕКС."""
    outbox_id = int(entry["outbox_id"])
    key = str(entry["idempotency_key"])
    try:
        async with session.post(
            _base_url() + "/api/taksimo-new/integration/rumex/expected-documents",
            headers=_headers(idempotency_key=key),
            json=entry["document"],
        ) as response:
            try:
                body = await response.json(content_type=None)
            except Exception:
                body = await response.text()
            status = response.status
    except (ClientError, asyncio.TimeoutError) as exc:
        rumex_store.record_test_taksimo_document_delivery(
            outbox_id, success=False, result_note=f"Ошибка сети: {type(exc).__name__}"
        )
        return
    success = status in {200, 201}
    document = body.get("document") if isinstance(body, Mapping) else None
    reference = str(document.get("public_id") or "") if isinstance(document, Mapping) else ""
    rumex_store.record_test_taksimo_document_delivery(
        outbox_id,
        success=success,
        http_status=status,
        result_note=_response_note(status, body),
        delivery_reference=reference,
    )


async def deliver_rumex_documents(session: Any) -> int:
    """Передать неподтверждённые копии ТТН нового реестра в новую Таксимо."""
    delivered = 0
    for entry in rumex_store.list_pending_test_taksimo_documents():
        await _post_document(session, entry)
        delivered += 1
    return delivered


async def deliver_rumex_snapshots(session: Any) -> int:
    """Передать неподтверждённые снимки РУМЕКС и записать каждую попытку.

    Отдельно из боевого реестра (shipments) и отдельно из нового диспетчерского
    реестра (test_shipments) — обе очереди идут на один и тот же endpoint новой
    Таксимо, но хранятся независимо и не мешают друг другу.
    """
    delivered = 0
    for entry in rumex_store.list_pending_taksimo_snapshots():
        await _post_snapshot(session, entry, record_delivery=rumex_store.record_taksimo_snapshot_delivery)
        delivered += 1
    for entry in rumex_store.list_pending_test_taksimo_snapshots():
        await _post_snapshot(session, entry, record_delivery=rumex_store.record_test_taksimo_snapshot_delivery)
        delivered += 1
    return delivered


async def _record_or_reject_physical_event(session: Any, event: Mapping[str, Any]) -> bool:
    public_id = str(event.get("public_id") or "")
    payload = event.get("payload")
    if not public_id or not isinstance(payload, Mapping):
        return False
    try:
        rumex_store.record_taksimo_physical_event(
            source_event_public_id=public_id,
            event_type=event.get("event_type"),
            payload=payload,
            source_occurred_at=_iso_to_timestamp(event.get("created_at")),
        )
    except ValueError as exc:
        note = str(exc)[:500]
        try:
            async with session.post(
                _base_url() + f"/api/taksimo-new/integration/outbox/{public_id}/failure",
                headers=_headers(),
                json={"result_note": note},
            ) as response:
                if response.status >= 400:
                    logger.warning("Мост РУМЕКС/Таксимо: не сохранён отказ события status=%s", response.status)
        except (ClientError, asyncio.TimeoutError):
            logger.warning("Мост РУМЕКС/Таксимо: не удалось сохранить отказ физического события")
        return False
    try:
        async with session.post(
            _base_url() + f"/api/taksimo-new/integration/outbox/{public_id}/ack",
            headers=_headers(),
            json={"receipt_reference": f"rumex-physical-event:{public_id}"},
        ) as response:
            if response.status in {200, 404}:
                return True
            logger.warning("Мост РУМЕКС/Таксимо: событие не подтверждено status=%s", response.status)
    except (ClientError, asyncio.TimeoutError):
        logger.warning("Мост РУМЕКС/Таксимо: не удалось подтвердить физическое событие")
    return False


async def receive_taksimo_physical_events(session: Any) -> int:
    """Зафиксировать факты новой Таксимо в РУМЕКС и лишь затем подтвердить их."""
    try:
        async with session.get(
            _base_url() + "/api/taksimo-new/integration/outbox", headers=_headers()
        ) as response:
            if response.status != 200:
                logger.warning("Мост РУМЕКС/Таксимо: очередь физических фактов недоступна status=%s", response.status)
                return 0
            body = await response.json(content_type=None)
    except (ClientError, asyncio.TimeoutError):
        logger.warning("Мост РУМЕКС/Таксимо: ошибка запроса физических фактов")
        return 0
    events = body.get("events") if isinstance(body, Mapping) else None
    if not isinstance(events, list):
        logger.warning("Мост РУМЕКС/Таксимо: получен некорректный формат физических фактов")
        return 0
    recorded = 0
    for event in events:
        if isinstance(event, Mapping) and await _record_or_reject_physical_event(session, event):
            recorded += 1
    return recorded


async def run_bridge_once() -> None:
    """Сделать один безопасный проход моста; удобно для тестов и планировщика."""
    if not bridge_configured():
        return
    if ClientSession is None or ClientTimeout is None:
        logger.error("Мост РУМЕКС/Таксимо не запущен: не установлен aiohttp")
        return
    timeout = ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
    async with ClientSession(timeout=timeout) as session:
        rumex_store.enqueue_pending_test_taksimo_documents()
        await deliver_rumex_snapshots(session)
        await deliver_rumex_documents(session)
        await receive_taksimo_physical_events(session)


async def rumex_taksimo_bridge_loop() -> None:
    """Фоновый повторяемый обмен с долговечными очередями и без скрытых повторов."""
    warned_unconfigured = False
    while True:
        try:
            if not bridge_configured():
                if not warned_unconfigured:
                    logger.warning("Мост РУМЕКС/Таксимо не запущен: не задан служебный токен")
                    warned_unconfigured = True
            else:
                warned_unconfigured = False
                await run_bridge_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Мост РУМЕКС/Таксимо: непредвиденная ошибка прохода")
        await asyncio.sleep(POLL_SECONDS)
