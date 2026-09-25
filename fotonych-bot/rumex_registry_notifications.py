"""Уведомления и фоновая обработка отдельного реестра РУМЕКС."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
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
    """Уведомить только доступных бухгалтеров; ошибка DM не ломает реестр."""
    if _bot is None:
        return
    number = str(shipment.get("registry_number") or "погрузка")
    for accountant_name, user_id in _recipients().items():
        if not get_accountant_availability(accountant_name).get("available_now"):
            continue
        try:
            await _bot.send_message(
                user_id=user_id,
                text=(
                    "🚚 РУМЕКС · новая погрузка\n"
                    "📄 " + number + "\n\n"
                    "👤 Откройте кабинет бухгалтера и при необходимости нажмите «Взять в работу».\n"
                    "⏱️ Если статус не изменится за 10 минут, ТТН откроется автоматически."
                ),
            )
            logger.info("РУМЕКС: уведомление о погрузке %s доставлено бухгалтеру %s", number, accountant_name)
        except Exception:
            logger.exception("РУМЕКС: не удалось отправить уведомление бухгалтеру")


async def rumex_registry_deadline_loop() -> None:
    """Открывать просроченные ТТН, включая ожидания, пережившие перезапуск."""
    while True:
        try:
            released = release_due_test_shipments()
            if released:
                from rumex_registry_backup import backup_rumex_registry_db

                backup_rumex_registry_db(reason="overdue_test_shipments_released")
        except Exception:
            logger.exception("РУМЕКС: не удалось обработать срок ожидания бухгалтера")
        await asyncio.sleep(10)
