"""Регрессия дневного отчёта Таксимо по текущему составу вагонов."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
BOT = ROOT / "fotonych-bot"
if str(BOT) not in sys.path:
    sys.path.insert(0, str(BOT))

import taksimo_store as store  # noqa: E402


class TaksimoDailyReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._old_db_path = store.DB_PATH
        store.DB_PATH = Path(self._tmpdir.name) / "taksimo.db"
        store.init_taksimo_db()

    def tearDown(self) -> None:
        store.DB_PATH = self._old_db_path
        self._tmpdir.cleanup()

    def _add_slabs(
        self,
        wagon_number: str,
        blocks: list[tuple[str, str]],
        *,
        loading_date: str,
    ) -> None:
        store.create_session(
            unload_date="2026-09-13",
            trn="",
            vehicle_id=None,
            driver="",
            crane_start="",
            crane_end="",
            crane_minutes=None,
            riggers_count=0,
            riggers_pay=0,
            taxi_pay=0,
            notes="",
            slabs=[
                {
                    "letter": letter,
                    "number": number,
                    "platform_zone": "ГРУЗОВОЙ",
                    "wagon_number": wagon_number,
                    "loading_date": loading_date,
                }
                for letter, number in blocks
            ],
        )

    def test_daily_report_uses_full_current_wagon_for_today_activity(self) -> None:
        wagon = "42368498"
        old_blocks = [
            ("A", "236"),
            ("A", "3610"),
            ("B", "4572"),
            ("C", "429"),
            ("D", "2314"),
            ("E", "4323"),
            ("F", "2755"),
            ("F", "5027"),
        ]
        for block in old_blocks:
            self._add_slabs(wagon, [block], loading_date="12.09.2026 13:00")
        self._add_slabs(wagon, [("K", "5279")], loading_date="13.09.2026 13:33")
        self._add_slabs(
            "42369538",
            [("A", "4672")],
            loading_date="12.09.2026 12:00",
        )
        self._add_slabs(
            "54586581",
            [("A", "1429")],
            loading_date="13.09.2026 16:01",
        )

        report = store.list_wagon_loads_for_daily_report(
            "2026-09-13", end_hour=16, end_minute=0
        )

        self.assertEqual(len(report), 1)
        item = report[0]
        self.assertEqual(item["wagon_number"], wagon)
        self.assertEqual(item["zone"], "ГРУЗОВОЙ")
        self.assertEqual(item["count"], 9)
        self.assertEqual(item["max"], 9)
        self.assertEqual(
            set(item["labels"]),
            {"A236", "A3610", "B4572", "C429", "D2314", "E4323", "F2755", "F5027", "K5279"},
        )
        self.assertEqual(item["last_loading"], "13.09.2026 13:33")
