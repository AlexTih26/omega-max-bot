"""Доставка уведомлений нового контура Таксимо в знакомую группу MAX.

Состояние доставки находится только в MySQL-очереди ``taksimo_new``. Модуль не
использует SQLite-флаги или JSON-состояние старой Таксимо и не вызывает мост
чата водителей.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
import logging
import os
from typing import TYPE_CHECKING, Any, Mapping
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from maxapi import Bot

import taksimo_new_store as store


logger = logging.getLogger(__name__)

POLL_SECONDS = 20
REPORT_HOUR = 17
REPORT_MINUTE = 0
_bot: Bot | None = None


def set_bot(bot: Bot) -> None:
    global _bot
    _bot = bot


def _notify_chat_id() -> int | None:
    """Использовать знакомую группу, не раскрывая её идентификатор в журналах."""
    raw = (os.getenv("TAKSIMO_NOTIFY_CHAT_ID") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning("Новая Таксимо: группа MAX для уведомлений настроена некорректно")
        return None


def _report_timezone() -> ZoneInfo:
    name = (os.getenv("TAKSIMO_TIMEZONE") or "Europe/Moscow").strip()
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("Europe/Moscow")


def _date_label(value: Any) -> str:
    try:
        return date.fromisoformat(str(value)).strftime("%d.%m.%Y")
    except ValueError:
        return str(value or "—")


def _time_label(value: Any) -> str:
    if not isinstance(value, str) or not value:
        return "—"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(_report_timezone()).strftime("%d.%m %H:%M")
    except ValueError:
        return value


def _details(payload: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    ttn = str(payload.get("ttn_number") or "").strip()
    plate = str(payload.get("vehicle_plate") or "").strip()
    wagon = str(payload.get("wagon_number") or "").strip()
    if ttn:
        lines.append(f"ТТН: {ttn}")
    if plate:
        lines.append(f"Машина: {plate}")
    if wagon:
        lines.append(f"Вагон: {wagon}")
    blocks_count = payload.get("blocks_count")
    if isinstance(blocks_count, int):
        lines.append(f"Блоков: {blocks_count}")
    operator = payload.get("operator")
    if isinstance(operator, Mapping) and operator.get("name"):
        lines.append(f"Оператор: {operator['name']}")
    return lines


def format_notification(event_type: str, payload: Mapping[str, Any]) -> str:
    """Сформировать знакомое человеку сообщение с явной меткой нового контура."""
    headings = {
        "intake_arrived": "🚚 Машина прибыла · Новая Таксимо",
        "intake_crane_started": "🏗️ Кран начал работу · Новая Таксимо",
        "intake_crane_ended": "🏗️ Кран завершил работу · Новая Таксимо",
        "intake_confirmed": "✅ Приёмка завершена · Новая Таксимо",
        "intake_discrepancy": "⚠️ Приёмка с расхождением · Новая Таксимо",
        "wagon_loaded": "🚃 Вагон загружен · Новая Таксимо",
        "wagon_in_transit": "🚃 Вагон в пути · Новая Таксимо",
        "wagon_at_kodar": "📍 Вагон в Кодаре · Новая Таксимо",
        "wagon_unloaded_bts_east": "📦 Выгружен у БТС Восток · Новая Таксимо",
    }
    heading = headings.get(event_type, f"ℹ️ Событие · Новая Таксимо: {event_type}")
    lines = [heading, *_details(payload)]
    when = payload.get("occurred_at") or payload.get("confirmed_at") or payload.get("loaded_at") or payload.get("dispatched_at")
    if when:
        lines.append(f"Время: {_time_label(when)}")
    return "\n".join(lines)


def format_daily_report(payload: Mapping[str, Any]) -> str:
    intakes = payload.get("intakes") if isinstance(payload.get("intakes"), Mapping) else {}
    wagons = payload.get("wagons") if isinstance(payload.get("wagons"), list) else []
    lines = [
        f"📊 Таксимо · Новая Таксимо · отчёт за {_date_label(payload.get('date'))}",
        f"Приёмки: {int(intakes.get('confirmed') or 0)} · принятые блоки: {int(intakes.get('received_blocks') or 0)}",
    ]
    missing = int(intakes.get("missing_blocks") or 0)
    damaged = int(intakes.get("damaged_blocks") or 0)
    if missing or damaged:
        lines.append(f"Расхождения: недостача {missing} · повреждения {damaged}")
    if not wagons:
        lines.append("Вагонов по этапам сегодня не было.")
        return "\n".join(lines)
    lines.append("Вагоны:")
    statuses = {
        "loaded": "загружен",
        "in_transit": "в пути",
        "at_kodar": "в Кодаре",
        "unloaded_bts_east": "выгружен у БТС Восток",
    }
    for wagon in wagons:
        if not isinstance(wagon, Mapping):
            continue
        number = str(wagon.get("wagon_number") or "—")
        blocks = int(wagon.get("blocks_count") or 0)
        status = statuses.get(str(wagon.get("status") or ""), str(wagon.get("status") or "—"))
        lines.append(f"• {number} · {blocks} блоков · {status}")
    return "\n".join(lines)


async def deliver_pending_notifications() -> int:
    """Попытаться доставить записи очереди, не скрывая неудачные попытки."""
    sent = 0
    chat_id = _notify_chat_id()
    bot = _bot
    for entry in store.list_pending_notifications():
        public_id = str(entry.get("public_id") or "")
        event_type = str(entry.get("event_type") or "")
        payload = entry.get("payload")
        if not public_id or not isinstance(payload, Mapping):
            continue
        if chat_id is None:
            store.record_notification_delivery(
                public_id, success=False, result_note="Не настроена группа MAX для уведомлений"
            )
            break
        if bot is None:
            store.record_notification_delivery(
                public_id, success=False, result_note="MAX-бот нового контура не инициализирован"
            )
            break
        text = format_daily_report(payload) if event_type == "daily_report" else format_notification(event_type, payload)
        try:
            await bot.send_message(chat_id=chat_id, text=text)
        except Exception as exc:
            store.record_notification_delivery(
                public_id, success=False, result_note=f"Ошибка MAX: {type(exc).__name__}"
            )
            logger.warning("Новая Таксимо: не доставлено уведомление %s", public_id)
            break
        if store.record_notification_delivery(
            public_id, success=True, result_note="MAX: доставлено", receipt_reference="max:sent"
        ):
            sent += 1
    return sent


def _report_due(now: datetime) -> bool:
    return (now.hour, now.minute) >= (REPORT_HOUR, REPORT_MINUTE)


async def daily_report_loop() -> None:
    """Поставить ровно одну ежедневную сводку новой Таксимо и доставить очередь."""
    logger.info("Новая Таксимо: единый вечерний отчёт в %02d:%02d", REPORT_HOUR, REPORT_MINUTE)
    while True:
        try:
            now = datetime.now(_report_timezone())
            if _report_due(now):
                store.enqueue_daily_report(now.date().isoformat())
            await deliver_pending_notifications()
            await asyncio.sleep(POLL_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Новая Таксимо: ошибка очереди уведомлений")
            await asyncio.sleep(60)
