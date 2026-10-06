"""Проверки переключения read-only legacy-просмотра на архивный снимок."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import os
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

import taksimo_legacy_archive as archive  # noqa: E402


class TaksimoLegacyArchiveTests(unittest.TestCase):
    def test_before_configured_cutover_reads_live_snapshot(self) -> None:
        collections = {name: [] for name in archive.COLLECTIONS}
        collections["vehicles"] = [{"plate": "А111АА"}]
        with patch.dict(os.environ, {"TAKSIMO_LEGACY_CUTOVER_AT": "2026-02-01T00:00:00+00:00"}, clear=False), patch.object(
            archive, "utc_now", return_value=datetime(2026, 1, 1, 0, 0, 0)
        ), patch.object(archive.legacy, "read_legacy_snapshot", return_value=collections) as read_snapshot:
            view = archive.current_view()

        self.assertEqual(view["mode"], "live")
        self.assertTrue(view["available"])
        self.assertEqual(view["collections"]["vehicles"], [{"plate": "А111АА"}])
        read_snapshot.assert_called_once()

    def test_after_cutover_does_not_save_empty_snapshot_when_legacy_unavailable(self) -> None:
        with patch.dict(os.environ, {"TAKSIMO_LEGACY_CUTOVER_AT": "2026-01-01T00:00:00+00:00"}, clear=False), patch.object(
            archive, "utc_now", return_value=datetime(2026, 1, 2, 0, 0, 0)
        ), patch.object(archive, "_archive_row", return_value=None), patch.object(
            archive.legacy, "read_legacy_snapshot", side_effect=archive.legacy.LegacyReadError("нет файла")
        ), patch.object(archive, "_save_snapshot") as save_snapshot:
            view = archive.current_view()

        self.assertEqual(view["mode"], "snapshot_pending")
        self.assertFalse(view["available"])
        self.assertEqual(view["collections"], {name: [] for name in archive.COLLECTIONS})
        save_snapshot.assert_not_called()

    def test_after_cutover_uses_existing_archive_without_reading_legacy(self) -> None:
        archived = {
            "payload_json": {"vehicles": [{"plate": "А111АА"}]},
            "cutover_at": datetime(2026, 1, 1, 0, 0, 0),
            "captured_at": datetime(2026, 1, 2, 0, 0, 0),
        }
        with patch.dict(os.environ, {"TAKSIMO_LEGACY_CUTOVER_AT": "2026-01-01T00:00:00+00:00"}, clear=False), patch.object(
            archive, "utc_now", return_value=datetime(2026, 1, 3, 0, 0, 0)
        ), patch.object(archive, "_archive_row", return_value=archived), patch.object(
            archive.legacy, "read_legacy_snapshot"
        ) as read_snapshot:
            view = archive.current_view()

        self.assertEqual(view["mode"], "archive")
        self.assertTrue(view["available"])
        self.assertEqual(view["collections"]["vehicles"], [{"plate": "А111АА"}])
        read_snapshot.assert_not_called()

    def test_invalid_cutover_configuration_is_reported_without_reading_legacy(self) -> None:
        with patch.dict(os.environ, {"TAKSIMO_LEGACY_CUTOVER_AT": "2026-01-01"}, clear=False), patch.object(
            archive.legacy, "read_legacy_snapshot"
        ) as read_snapshot:
            view = archive.current_view()

        self.assertEqual(view["mode"], "configuration_error")
        self.assertFalse(view["available"])
        read_snapshot.assert_not_called()
