"""Напоминания Склад Мастер: приём на склад ожидается в течение суток."""

from __future__ import annotations

import asyncio
import logging

from materials_chat import notify_event
from materials_receipt_chat import notify_materials_role_users
from sklad_master_store import list_eta_reminders_due, mark_eta_reminder_sent

logger = logging.getLogger(__name__)


def _reminder_lines(item: dict) -> list[str]:
    lines = [
        "⏰ Склад Мастер · приём завтра",
        f"Заявка №{item['id']} · {item.get('site_name') or 'площадка'}",
    ]
    for position in item.get("items") or []:
        lines.append(
            f"{position.get('material_name') or 'Материал'} — "
            f"{position.get('ordered', position.get('quantity'))} "
            f"{position.get('material_unit') or ''}".rstrip()
        )
    if item.get("eta_date_label"):
        lines.append(f"Ожидаем: {item['eta_date_label']}")
    if item.get("supplier_name"):
        lines.append(f"Поставщик: {item['supplier_name']}")
    return lines


async def sklad_eta_reminder_loop() -> None:
    """Проверка каждые 30 минут: напоминание за ~24 ч до ожидаемого приёма."""
    while True:
        await asyncio.sleep(1800)
        try:
            due = list_eta_reminders_due()
        except Exception:
            logger.exception("Склад Мастер: не удалось получить список ETA-напоминаний")
            continue
        for item in due:
            text = "\n".join(_reminder_lines(item)).strip()
            try:
                await notify_event(text)
            except Exception:
                logger.exception("Склад Мастер: ETA-напоминание в чат не отправлено")
            try:
                await notify_materials_role_users(text, role="supply")
            except Exception:
                logger.exception("Склад Мастер: ETA-напоминание снабжению не отправлено")
            try:
                mark_eta_reminder_sent(int(item["id"]))
                logger.info("Склад Мастер: ETA-напоминание заявка #%s", item["id"])
            except Exception:
                logger.exception("Склад Мастер: не удалось отметить ETA-напоминание")
