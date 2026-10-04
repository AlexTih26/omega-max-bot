"""Уведомления и фоновая обработка отдельного реестра РУМЕКС."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import Mapping
from zoneinfo import ZoneInfo

from rumex_registry_store import get_accountant_availability, release_due_test_shipments

logger = logging.getLogger(__name__)

_bot = None


def set_bot(bot) -> None:
    global _bot
    _bot = bot


def _recipients() -> dict[str, int]:
    """Прочитать сопоставление кабинета и MAX только из окружения."""
    recipients: dict[str, int] = {}
    for chunk in (os.getenv("RUMEX_ACCOUNTANT_NOTIFIERS") or "").split(","):
        name, separator, raw_id = chunk.strip().partition(":")
        if not separator or not name:
            continue
        try:
            user_id = int(raw_id.strip())
        except ValueError:
            continue
        if user_id > 0:
            recipients[name.strip()] = user_id
    return recipients


def _format_time(timestamp: object) -> str:
    try:
        value = float(timestamp)
    except (TypeError, ValueError):
        return "—"
    try:
        timezone = ZoneInfo((os.getenv("RUMEX_TIMEZONE") or "Asia/Irkutsk").strip())
    except Exception:
        timezone = ZoneInfo("Asia/Irkutsk")
    return datetime.fromtimestamp(value, tz=timezone).strftime("%d.%m.%Y %H:%M")


def _format_datetime(timestamp: object) -> str:
    try:
        value = float(timestamp)
    except (TypeError, ValueError):
        return "—"
    try:
        timezone = ZoneInfo((os.getenv("RUMEX_TIMEZONE") or "Asia/Irkutsk").strip())
    except Exception:
        timezone = ZoneInfo("Asia/Irkutsk")
    return datetime.fromtimestamp(value, tz=timezone).strftime("%d.%m.%Y, %H:%M")


def _format_weight(value: object) -> str:
    try:
        weight = int(value)
    except (TypeError, ValueError):
        return "—"
    return f"{weight:,}".replace(",", " ")


def _vehicle_plate(snapshot: object) -> str:
    vehicle = snapshot.get("vehicle") if isinstance(snapshot, Mapping) else None
    if not isinstance(vehicle, Mapping):
        return "—"
    return str(vehicle.get("full_plate") or vehicle.get("plate_tail") or "—").strip()


def _driver_name(snapshot: object) -> str:
    driver = snapshot.get("driver") if isinstance(snapshot, Mapping) else None
    if not isinstance(driver, Mapping):
        return "—"
    return str(driver.get("full_name") or "—").strip()


def _block_lines(items: object) -> list[str]:
    lines: list[str] = []
    if not isinstance(items, list):
        return lines
    for item in items:
        if not isinstance(item, Mapping):
            continue
        letter = str(item.get("block_type_code") or "—").strip()
        number = str(item.get("block_number") or "—").strip()
        lines.append(f"• {letter}-{number} — {_format_weight(item.get('weight_kg'))} кг")
    return lines


def _available_accountants(*, exclude: str | None = None) -> list[tuple[str, int]]:
    result: list[tuple[str, int]] = []
    for name, user_id in _recipients().items():
        if exclude is not None and name == exclude:
            continue
        if not get_accountant_availability(name).get("available_now"):
            continue
        result.append((name, user_id))
    return result


async def _send_accountant_messages(text: str, *, event_name: str, exclude: str | None = None) -> None:
    if _bot is None:
        return
    for name, user_id in _available_accountants(exclude=exclude):
        try:
            await _bot.send_message(user_id=user_id, text=text)
            logger.info("РУМЕКС: уведомление %s доставлено бухгалтеру %s", event_name, name)
        except Exception:
            logger.exception("РУМЕКС: не удалось отправить уведомление %s бухгалтеру %s", event_name, name)


async def _notify_accountant_one(text: str, *, event_name: str) -> None:
    """Отправить личное информационное сообщение бухгалтеру 1 без влияния на запись."""
    if _bot is None:
        return
    user_id = _recipients().get("Бухгалтер 1")
    if user_id is None:
        logger.warning("РУМЕКС: не настроен получатель Бухгалтер 1 для %s", event_name)
        return
    try:
        await _bot.send_message(user_id=user_id, text=text)
        logger.info("РУМЕКС: уведомление %s доставлено бухгалтеру 1", event_name)
    except Exception:
        logger.exception("РУМЕКС: не удалось отправить уведомление %s бухгалтеру 1", event_name)


async def notify_carrier_created(carrier: dict, *, actor_name: str) -> None:
    """Сообщить бухгалтеру 1 о новой карточке перевозчика."""
    await _notify_accountant_one(
        "🏢 РУМЕКС · добавлен новый перевозчик\n\n"
        "📌 Наименование: " + str(carrier.get("name") or "—") + "\n"
        "🧾 ИНН: " + str(carrier.get("inn") or "—") + "\n"
        "✅ Источник: " + str(carrier.get("confirmation_reference") or "—") + "\n"
        "👤 Добавил: " + (actor_name or "—") + "\n"
        "🕒 Когда: " + _format_time(carrier.get("created_at")),
        event_name="новый перевозчик",
    )


async def notify_document_vehicle_binding_confirmed(binding: dict) -> None:
    """Сообщить бухгалтеру 1 о подтверждённой документной связи машины."""
    vehicle = binding.get("vehicle_snapshot") or {}
    carrier = binding.get("carrier_snapshot") or {}
    vehicle_label = " · ".join(
        value for value in (str(vehicle.get("full_plate") or "").strip(), str(vehicle.get("model") or "").strip()) if value
    ) or str(binding.get("plate_tail_snapshot") or "—")
    await _notify_accountant_one(
        "🚛 РУМЕКС · подтверждена документная связь\n\n"
        "🚚 Автомобиль: " + vehicle_label + "\n"
        "🏢 Перевозчик: " + str(carrier.get("name") or "—") + "\n"
        "👤 Водитель: " + str(binding.get("driver_full_name") or "—") + "\n"
        "🪪 Удостоверение: " + str(binding.get("driver_license_number") or "—") + "\n"
        "✅ Подтвердил: " + str(binding.get("confirmed_by") or "—") + "\n"
        "🕒 Когда: " + _format_time(binding.get("checked_at")),
        event_name="документная связь машины",
    )


async def notify_new_test_shipment(shipment: dict) -> None:
    """Подробно сообщить доступным бухгалтерам о новой погрузке; ошибка DM не ломает реестр."""
    if _bot is None:
        return
    number = str(shipment.get("registry_number") or "погрузка")
    snapshot = shipment.get("document_snapshot") or {}
    items = shipment.get("items") or []
    lines = [
        "🚚 РУМЕКС · новая погрузка",
        "📄 " + number,
        "📅 " + _format_datetime(shipment.get("loaded_at")),
        "🚛 Машина: " + _vehicle_plate(snapshot),
        "👤 Водитель: " + _driver_name(snapshot),
        f"🧱 Блоки ({len(items)}):",
        *_block_lines(items),
        f"⚖️ Итого: {_format_weight(shipment.get('total_weight_kg'))} кг",
        "👤 Зайдите в кабинет — проверьте погрузку и передайте ЭР в ЭДО",
    ]
    await _send_accountant_messages("\n".join(lines), event_name="новая погрузка")


async def notify_shipment_claimed(shipment: dict, *, accountant_name: str) -> None:
    """Сообщить остальным доступным бухгалтерам, кто взял погрузку в работу."""
    if _bot is None:
        return
    number = str(shipment.get("registry_number") or "погрузка")
    lines = [
        "✅ РУМЕКС · погрузка взята в работу",
        "📄 " + number,
        "👤 Бухгалтер: " + (accountant_name or "—"),
        "📅 " + _format_datetime(shipment.get("loaded_at")),
    ]
    await _send_accountant_messages("\n".join(lines), event_name="взятие в работу", exclude=accountant_name)


async def notify_shipment_ttn_auto_opened(shipment: dict) -> None:
    """Повторно сообщить бухгалтерам, что ТТН открылась автоматически, но проверка всё равно нужна."""
    if _bot is None:
        return
    number = str(shipment.get("registry_number") or "погрузка")
    ttn = str(shipment.get("ttn_number") or "—")
    lines = [
        "⏱️ РУМЕКС · погрузка не взята в работу",
        "📄 " + number,
        f"🧾 ТТН {ttn} открыта автоматически",
        "❗ Всё равно проверьте погрузку и передайте ЭР в ЭДО",
    ]
    await _send_accountant_messages("\n".join(lines), event_name="автооткрытие ТТН")


async def notify_shipment_er_sent(shipment: dict) -> None:
    """Сообщить бухгалтерам, что ЭР отправлена в ЭДО, и показать номер ТТН."""
    if _bot is None:
        return
    number = str(shipment.get("registry_number") or "погрузка")
    ttn = str(shipment.get("ttn_number") or "—")
    lines = [
        "📨 РУМЕКС · ЭР отправлены в ЭДО",
        "📄 " + number,
        f"🧾 ТТН {ttn}",
    ]
    await _send_accountant_messages("\n".join(lines), event_name="ЭР отправлена в ЭДО")


async def rumex_registry_deadline_loop() -> None:
    """Открывать просроченные ТТН, включая ожидания, пережившие перезапуск."""
    while True:
        try:
            released = release_due_test_shipments()
            for shipment in released:
                await notify_shipment_ttn_auto_opened(shipment)
            if released:
                from rumex_registry_backup import backup_rumex_registry_db

                backup_rumex_registry_db(reason="overdue_test_shipments_released")
        except Exception:
            logger.exception("РУМЕКС: не удалось обработать срок ожидания бухгалтера")
        await asyncio.sleep(10)
