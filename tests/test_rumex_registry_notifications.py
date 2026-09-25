"""Проверки личных MAX-уведомлений бухгалтерского реестра РУМЕКС."""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
BOT = ROOT / "fotonych-bot"
if str(BOT) not in sys.path:
    sys.path.insert(0, str(BOT))

import rumex_registry_notifications as notifications  # noqa: E402


class _Bot:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_message(self, **kwargs) -> None:
        self.messages.append(kwargs)


class RumexRegistryNotificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_bot = notifications._bot
        self.old_recipients = os.environ.get("RUMEX_ACCOUNTANT_NOTIFIERS")
        self.bot = _Bot()
        notifications.set_bot(self.bot)
        os.environ["RUMEX_ACCOUNTANT_NOTIFIERS"] = "Бухгалтер 1:1"

    def tearDown(self) -> None:
        notifications.set_bot(self.old_bot)
        if self.old_recipients is None:
            os.environ.pop("RUMEX_ACCOUNTANT_NOTIFIERS", None)
        else:
            os.environ["RUMEX_ACCOUNTANT_NOTIFIERS"] = self.old_recipients

    def test_notifies_accountant_one_about_new_carrier(self) -> None:
        asyncio.run(notifications.notify_carrier_created({
            "name": "ООО «Перевозчик»",
            "inn": "1234567890",
            "confirmation_reference": "Карточка контрагента",
            "created_at": 1_767_228_000.0,
        }, actor_name="Бухгалтер 1"))

        self.assertEqual(self.bot.messages[0]["user_id"], 1)
        self.assertIn("🏢 РУМЕКС · добавлен новый перевозчик", self.bot.messages[0]["text"])
        self.assertIn("👤 Добавил: Бухгалтер 1", self.bot.messages[0]["text"])

    def test_notifies_accountant_one_about_document_vehicle_binding(self) -> None:
        asyncio.run(notifications.notify_document_vehicle_binding_confirmed({
            "vehicle_snapshot": {"full_plate": "А113НХ /138/", "model": "Faw"},
            "carrier_snapshot": {"name": "ООО «Перевозчик»"},
            "driver_full_name": "Иванов Иван Иванович",
            "driver_license_number": "38 12 123456",
            "confirmed_by": "Бухгалтер 1",
            "checked_at": 1_767_228_000.0,
        }))

        self.assertEqual(self.bot.messages[0]["user_id"], 1)
        self.assertIn("🚛 РУМЕКС · подтверждена документная связь", self.bot.messages[0]["text"])
        self.assertIn("🚚 Автомобиль: А113НХ /138/ · Faw", self.bot.messages[0]["text"])
        self.assertIn("🕒 Когда:", self.bot.messages[0]["text"])
