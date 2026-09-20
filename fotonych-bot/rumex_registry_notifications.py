"""Уведомления и фоновая обработка отдельного реестра РУМЕКС."""

from __future__ import annotations

import asyncio
import logging
import os

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
                    "РУМЕКС: новая погрузка " + number + ".\n"
                    "Откройте кабинет бухгалтера и при необходимости нажмите «Взять в работу». "
                    "Если статус не будет изменён за 10 минут, ТТН откроется автоматически."
                ),
            )
        except Exception:
            logger.exception("РУМЕКС: не удалось отправить уведомление бухгалтеру")


async def rumex_registry_deadline_loop() -> None:
    """Открывать просроченные ТТН, включая ожидания, пережившие перезапуск."""
    while True:
        try:
            release_due_test_shipments()
        except Exception:
            logger.exception("РУМЕКС: не удалось обработать срок ожидания бухгалтера")
        await asyncio.sleep(10)
