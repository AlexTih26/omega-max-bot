"""Обновление кнопки «Комментарии» под постом канала (счётчик)."""

from __future__ import annotations

import logging

from maxapi import Bot

from comments_store import count_comments, get_post
from keyboards import comments_keyboard
from post_attachments import (
    has_media_attachments,
    merge_attachments_with_keyboard,
    serialize_media_attachments,
)

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

    body = None
    try:
        msg = await _bot.get_message(post_id)
        body = msg.body
    except Exception:
        logger.debug("get_message для счётчика %s", post_id, exc_info=True)

    if body and has_media_attachments(body.attachments):
        attachments = merge_attachments_with_keyboard(body.attachments, kb)
        edit_text = (body.text or "").strip() or None
        media_json = serialize_media_attachments(body.attachments)
    else:
        from post_attachments import deserialize_media_attachments

        media = deserialize_media_attachments(post.get("media_attachments_json"))
        attachments = merge_attachments_with_keyboard(media, kb)
        text_raw = (post.get("message_text") or post.get("title") or "").strip()
        edit_text = text_raw if text_raw else None
        media_json = None

    if media_json:
        from comments_store import upsert_post

        upsert_post(
            post_id,
            post["chat_id"],
            post.get("title") or "Пост в канале",
            message_text=post.get("message_text"),
            media_attachments_json=media_json,
        )

    try:
        await _bot.edit_message(
            message_id=post_id,
            text=edit_text,
            attachments=attachments,
        )
        logger.info("Счётчик комментариев на посте %s: %s", post_id, count)
    except Exception:
        logger.exception("Не удалось обновить кнопку комментариев для %s", post_id)
