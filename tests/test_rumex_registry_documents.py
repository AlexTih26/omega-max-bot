"""Tests for technical XLSX generation in the isolated RUMEX test flow."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parent.parent
BOT = ROOT / "fotonych-bot"
if str(BOT) not in sys.path:
    sys.path.insert(0, str(BOT))

import rumex_registry_documents as documents  # noqa: E402


class RumexRegistryDocumentsTests(unittest.TestCase):
    def test_technical_ttn_uses_snapshot_and_safe_filename(self) -> None:
        shipment = {
            "registry_number": "РМ-2026-000123",
            "loaded_at": 1_767_225_600.0,
            "items": [
                {
                    "block_type_code": "A",
                    "block_number": "3611",
                    "product_name": "Блок 9,7/8,8",
                    "weight_kg": 7780,
                }
            ],
            "document_snapshot": {
                "document_profile": {
                    "sender_name": "ООО «Омега-М»",
                    "sender_inn": "1234567890",
                    "sender_kpp": "123456789",
                    "sender_legal_address": "г. Иркутск",
                    "recipient_name": "ООО «Омега-М»",
                    "delivery_location": "Станция назначения",
                    "pickup_location": "Завод Румекс",
                },
                "carrier": {
                    "name": "ООО «Перевозчик»",
                    "inn": "1098765432",
                    "kpp": "987654321",
                    "legal_address": "г. Братск",
                },
                "vehicle": {"plate_tail": "553", "full_plate": "К553НХ 138", "model": "FAW J6"},
                "driver": {"full_name": "Иванов/Иван Иванович"},
                "document_binding_checked_at": 1_767_225_500.0,
            },
        }
        template = ROOT / "docs" / "registry" / "ТТН — шаблон автозаполнения.xlsx"
        if not template.is_file():
            self.skipTest("Технический шаблон ТТН отсутствует в рабочем окружении")

        content = documents.build_test_ttn_workbook(shipment, issued_at=1_767_228_300.0)
        self.assertGreater(len(content), 1000)
        with tempfile.NamedTemporaryFile(suffix=".xlsx") as generated:
            generated.write(content)
            generated.flush()
            with ZipFile(generated.name) as workbook:
                sheet = workbook.read("xl/worksheets/sheet1.xml").decode("utf-8")
        self.assertIn("Тестовая техническая форма", sheet)
        self.assertIn("РМ-2026-000123", sheet)
        self.assertIn("Иванов/Иван Иванович", sheet)
        self.assertIn("К553НХ 138", sheet)
        self.assertIn("3611", sheet)
        self.assertNotIn("{{TTN_NUMBER}}", sheet)

        filename = documents.test_ttn_filename(shipment, issued_at=1_767_228_300.0)
        self.assertTrue(filename.startswith("ТТН №РМ-2026-000123 — Иванов Иван Иванович — "))
        self.assertTrue(filename.endswith(".xlsx"))


if __name__ == "__main__":
    unittest.main()
