"""Чат уведомлений Таксимо: только отчёты + кнопка поиска (mini-app)."""

from __future__ import annotations

import logging
import re

from maxapi import Bot
from maxapi.types import MessageCreated

from keyboards import TAKSIMO_FIND_HINT, taksimo_find_attachments
from taksimo_notify import notify_chat_id
from taksimo_store import parse_slab_query, parse_wagon_query

logger = logging.getLogger(__name__)

_FIND_WORDS = re.compile(r"где\s+плит|найди\s+плит|поиск", re.I)


def is_taksimo_chat(chat_id: int | None) -> bool:
    taksimo = notify_chat_id()
    return chat_id is not None and taksimo is not None and chat_id == taksimo


def _looks_like_search(text: str) -> bool:
    t = text.strip()
    if not t:
        return False
    if _FIND_WORDS.search(t):
        return True
    if parse_wagon_query(t):
        return True
    letter, number = parse_slab_query(t)
    return bool(letter or number)


async def send_taksimo_welcome(bot: Bot, *, chat_id: int) -> None:
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=(
                "✅ Чат Таксимо подключён.\n"
                "Сюда — только отчёты о выгрузках и сводка в 17:00.\n\n"
                + TAKSIMO_FIND_HINT
            ),
            attachments=taksimo_find_attachments(),
        )
    except Exception:
        logger.exception("Не удалось отправить приветствие Таксимо chat_id=%s", chat_id)


async def handle_taksimo_chat_message(event: MessageCreated) -> None:
    body = event.message.body
    text = body.text.strip() if body and body.text else ""
    if not text:
        return
    if not text.startswith("/") and not _looks_like_search(text):
        return

    if text.startswith("/taksimo_chat"):
        chat_id = event.message.recipient.chat_id
        if chat_id is not None:
            await event.message.answer(
                f"TAKSIMO_NOTIFY_CHAT_ID={chat_id}\n\n"
                "Добавьте в .env и перезапустите бота."
            )
        return

    await event.message.answer(TAKSIMO_FIND_HINT, attachments=taksimo_find_attachments())


async def handle_taksimo_callback(event, bot: Bot) -> bool:
    return False
