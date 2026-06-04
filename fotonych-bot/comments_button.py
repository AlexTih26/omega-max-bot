"""Обновление кнопки «Комментарии» под постом канала (счётчик)."""

from __future__ import annotations

import logging

from maxapi import Bot

from comments_store import count_comments, get_post
from keyboards import comments_keyboard

logger = logging.getLogger(__name__)

_bot: Bot | None = None


def set_bot(bot: Bot) -> None:
    global _bot
    _bot = bot


async def refresh_comments_button(post_id: str) -> None:
    if _bot is None:
        return
    post = get_post(post_id)
    if post is None:
        return
    count = count_comments(post_id)
    kb = comments_keyboard(post_id, count).as_markup()
    text = (post.get("message_text") or post.get("title") or "").strip()
    if not text:
        text = " "
    try:
        await _bot.edit_message(
            message_id=post_id,
            text=text,
            attachments=[kb],
        )
        logger.info("Счётчик комментариев на посте %s: %s", post_id, count)
    except Exception:
        logger.exception("Не удалось обновить кнопку комментариев для %s", post_id)
