"""Проверки личных MAX-уведомлений бухгалтерского реестра РУМЕКС."""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from unittest.mock import patch
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

    @staticmethod
    def _shipment() -> dict:
        return {
            "registry_number": "РМ-2026-000026",
            "loaded_at": 1_791_124_440.0,
            "document_snapshot": {
                "vehicle": {"full_plate": "Faw Т604ХН 196", "plate_tail": "604", "model": ""},
                "driver": {"full_name": "Николаев Р.Е"},
            },
            "items": [
                {"block_type_code": "E", "block_number": "3658", "weight_kg": 7900},
                {"block_type_code": "F", "block_number": "1086", "weight_kg": 7780},
                {"block_type_code": "K", "block_number": "5136", "weight_kg": 3850},
                {"block_type_code": "K", "block_number": "3163", "weight_kg": 3850},
            ],
            "total_weight_kg": 23380,
        }

    def test_new_shipment_notification_is_detailed(self) -> None:
        with patch.object(notifications, "get_accountant_availability", return_value={"available_now": True}):
            asyncio.run(notifications.notify_new_test_shipment(self._shipment()))
        text = self.bot.messages[0]["text"]
        self.assertEqual(self.bot.messages[0]["user_id"], 1)
        self.assertIn("🚚 РУМЕКС · новая погрузка", text)
        self.assertIn("📄 РМ-2026-000026", text)
        self.assertIn("Faw Т604ХН 196", text)
        self.assertIn("Николаев Р.Е", text)
        self.assertIn("• E-3658 — 7 900 кг", text)
        self.assertIn("• K-3163 — 3 850 кг", text)
        self.assertIn("⚖️ Итого: 23 380 кг", text)
        self.assertNotIn("ТТН", text)

    def test_claimed_notifies_other_accountants_only(self) -> None:
        os.environ["RUMEX_ACCOUNTANT_NOTIFIERS"] = "Бухгалтер 1:1,Бухгалтер 2:2"
        try:
            with patch.object(notifications, "get_accountant_availability", return_value={"available_now": True}):
                asyncio.run(notifications.notify_shipment_claimed(self._shipment(), accountant_name="Бухгалтер 2"))
        finally:
            os.environ["RUMEX_ACCOUNTANT_NOTIFIERS"] = "Бухгалтер 1:1"
        self.assertEqual([m["user_id"] for m in self.bot.messages], [1])
        self.assertIn("взята в работу", self.bot.messages[0]["text"])
        self.assertIn("Бухгалтер 2", self.bot.messages[0]["text"])

    def test_ttn_auto_opened_mentions_ttn(self) -> None:
        shipment = {**self._shipment(), "ttn_number": "ТТН №РМ-2026-000023"}
        with patch.object(notifications, "get_accountant_availability", return_value={"available_now": True}):
            asyncio.run(notifications.notify_shipment_ttn_auto_opened(shipment))
        text = self.bot.messages[0]["text"]
        self.assertIn("открыта автоматически", text)
        self.assertIn("ТТН №РМ-2026-000023", text)

    def test_er_sent_mentions_ttn(self) -> None:
        shipment = {**self._shipment(), "ttn_number": "ТТН №РМ-2026-000023"}
        with patch.object(notifications, "get_accountant_availability", return_value={"available_now": True}):
            asyncio.run(notifications.notify_shipment_er_sent(shipment))
        text = self.bot.messages[0]["text"]
        self.assertIn("ЭР отправлены в ЭДО", text)
        self.assertIn("ТТН №РМ-2026-000023", text)
