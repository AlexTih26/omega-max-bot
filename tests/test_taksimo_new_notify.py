"""Проверки отдельной очереди MAX-уведомлений новой Таксимо."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
BOT = ROOT / "fotonych-bot"
if str(BOT) not in sys.path:
    sys.path.insert(0, str(BOT))

if "dotenv" not in sys.modules:
    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda *_args, **_kwargs: False
    sys.modules["dotenv"] = dotenv

import taksimo_new_notify as notifications  # noqa: E402


class _Bot:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.failure = failure
        self.messages: list[dict] = []

    async def send_message(self, **kwargs) -> None:
        if self.failure is not None:
            raise self.failure
        self.messages.append(kwargs)


class TaksimoNewNotificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_bot = notifications._bot
        self.old_chat_id = os.environ.get("TAKSIMO_NOTIFY_CHAT_ID")
        os.environ["TAKSIMO_NOTIFY_CHAT_ID"] = "987654"

    def tearDown(self) -> None:
        notifications.set_bot(self.old_bot)
        if self.old_chat_id is None:
            os.environ.pop("TAKSIMO_NOTIFY_CHAT_ID", None)
        else:
            os.environ["TAKSIMO_NOTIFY_CHAT_ID"] = self.old_chat_id

    def test_notification_contains_new_taksimo_marker(self) -> None:
        text = notifications.format_notification(
            "intake_arrived",
            {
                "ttn_number": "РМ-2026-000001",
                "vehicle_plate": "Т000ТТ 138",
                "operator": {"name": "Оператор 2"},
                "occurred_at": "2026-01-01T12:00:00Z",
            },
        )

        self.assertIn("· Новая Таксимо", text)
        self.assertIn("ТТН: РМ-2026-000001", text)
        self.assertIn("Машина: Т000ТТ 138", text)

    def test_formats_daily_report(self) -> None:
        text = notifications.format_daily_report(
            {
                "date": "2026-01-01",
                "intakes": {"confirmed": 2, "received_blocks": 5, "missing_blocks": 1, "damaged_blocks": 0},
                "wagons": [{"wagon_number": "123", "blocks_count": 5, "status": "in_transit"}],
            }
        )

        self.assertIn("· Новая Таксимо · отчёт за 01.01.2026", text)
        self.assertIn("Приёмки: 2 · принятые блоки: 5", text)
        self.assertIn("Расхождения: недостача 1 · повреждения 0", text)
        self.assertIn("• 123 · 5 блоков · в пути", text)

    def test_delivers_pending_notification_and_acknowledges_only_after_success(self) -> None:
        bot = _Bot()
        notifications.set_bot(bot)
        entry = {
            "public_id": "notification-1",
            "event_type": "wagon_loaded",
            "payload": {"wagon_number": "123", "blocks_count": 5, "loaded_at": "2026-01-01T12:00:00Z"},
        }
        with patch.object(notifications.store, "list_pending_notifications", return_value=[entry]), patch.object(
            notifications.store, "record_notification_delivery", return_value=True
        ) as delivered:
            count = asyncio.run(notifications.deliver_pending_notifications())

        self.assertEqual(count, 1)
        self.assertEqual(len(bot.messages), 1)
        self.assertEqual(bot.messages[0]["chat_id"], 987654)
        self.assertIn("· Новая Таксимо", bot.messages[0]["text"])
        self.assertTrue(delivered.call_args.kwargs["success"])
        self.assertEqual(delivered.call_args.kwargs["receipt_reference"], "max:sent")

    def test_keeps_notification_pending_after_max_failure(self) -> None:
        notifications.set_bot(_Bot(failure=RuntimeError("network unavailable")))
        entry = {"public_id": "notification-2", "event_type": "daily_report", "payload": {"date": "2026-01-01"}}
        with patch.object(notifications.store, "list_pending_notifications", return_value=[entry]), patch.object(
            notifications.store, "record_notification_delivery", return_value=True
        ) as delivered:
            count = asyncio.run(notifications.deliver_pending_notifications())

        self.assertEqual(count, 0)
        self.assertFalse(delivered.call_args.kwargs["success"])
        self.assertEqual(delivered.call_args.kwargs["result_note"], "Ошибка MAX: RuntimeError")

    def test_does_not_depend_on_legacy_notification_state_or_driver_bridge(self) -> None:
        source = (BOT / "taksimo_new_notify.py").read_text(encoding="utf-8")

        self.assertNotIn("taksimo_notify", source)
        self.assertNotIn("drivers_chat", source)
        self.assertNotIn("_STATE_PATH", source)
