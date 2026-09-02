"""Tests for Rumex factory loading flow."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
BOT = ROOT / "fotonych-bot"
if str(BOT) not in sys.path:
    sys.path.insert(0, str(BOT))

import rumex_loading as rl  # noqa: E402
from rumex_loading import (  # noqa: E402
    STATUS_AWAITING,
    STATUS_KONTUR,
    STATUS_RELEASED,
    apply_loading_kontur,
    apply_loading_loaded,
    apply_loading_release,
    suggest_rumex_mode,
)


class RumexLoadingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.state_path = Path(self._tmpdir.name) / "drivers_chat.json"
        self.registry_path = Path(self._tmpdir.name) / "drivers_registry.json"
        self.registry_path.write_text(
            json.dumps(
                {
                    "drivers": [
                        {
                            "max_user_id": 41540658,
                            "plate_tail": "553",
                            "name": "Колмаков",
                            "vehicle": "HOWO К 553 МТ (03)",
                            "taksimo_plate": "К 553 МТ (03)",
                            "active": True,
                        },
                        {
                            "max_user_id": 0,
                            "plate_tail": "672",
                            "name": "Ловцов М.И.",
                            "vehicle": "Фав",
                            "taksimo_plate": "Фав 672",
                            "active": True,
                        },
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.state_path.write_text(
            json.dumps(
                {
                    "drivers": {
                        "41540658": {
                            "max_user_id": 41540658,
                            "name": "Колмаков",
                            "plate_tail": "553",
                            "vehicle": "HOWO К 553 МТ (03)",
                            "arrived_factory_at": "02.09 09:40",
                        },
                        "plate:672": {
                            "max_user_id": 0,
                            "name": "Ловцов М.И.",
                            "plate_tail": "672",
                            "vehicle": "Фав",
                            "left_taksimo_at": "29.08 18:22",
                            "awaiting_factory_docs_at": "29.08 18:22",
                        },
                    },
                    "rumex_loadings": [],
                    "events": [],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.patches = [
            patch.object(rl, "_REGISTRY_PATH", self.registry_path),
            patch("drivers_chat._STATE_PATH", self.state_path),
            patch.object(rl, "_load_state", rl._load_state),
            patch.object(rl, "_save_state", rl._save_state),
            patch.object(rl, "rumex_accountant_ids", lambda: {73966464}),
            patch.object(rl, "rumex_dispatcher_ids_for_dm", lambda: {111}),
        ]
        for p in self.patches:
            p.start()
        import drivers_chat

        drivers_chat._STATE_PATH = self.state_path

    def tearDown(self) -> None:
        for p in self.patches:
            p.stop()
        self._tmpdir.cleanup()

    def test_lookup_registry_by_tail(self) -> None:
        info = rl.lookup_registry_by_tail("553")
        self.assertIsNotNone(info)
        assert info is not None
        self.assertEqual(info["name"], "Колмаков")
        self.assertIn("553", info["plate"])

    def test_suggest_mode(self) -> None:
        self.assertEqual(suggest_rumex_mode("672"), "docs")
        self.assertEqual(suggest_rumex_mode("553"), "load")

    def test_loading_flow(self) -> None:
        blocks = [
            {"letter": "A", "number": "12"},
            {"letter": "B", "number": "34"},
            {"letter": "C", "number": "56"},
        ]
        loaded = apply_loading_loaded(
            111,
            "553",
            block_count=3,
            blocks=blocks,
            is_dispatcher=True,
        )
        self.assertTrue(loaded.ok)
        assert loaded.loading is not None
        self.assertEqual(loaded.loading["status"], STATUS_AWAITING)

        kontur = apply_loading_kontur(73966464, loading_id=loaded.loading["id"])
        self.assertTrue(kontur.ok)
        assert kontur.loading is not None
        self.assertEqual(kontur.loading["status"], STATUS_KONTUR)

        release = apply_loading_release(111, "553", is_dispatcher=True)
        self.assertTrue(release.ok)
        assert release.loading is not None
        self.assertEqual(release.loading["status"], STATUS_RELEASED)
        self.assertTrue(release.public_messages)


if __name__ == "__main__":
    unittest.main()
