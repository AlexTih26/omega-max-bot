"""Кнопка «Комментарии» под постами канала MAX."""

from __future__ import annotations

import asyncio
import logging

from maxapi import Bot
from maxapi.enums.attachment import AttachmentType
from maxapi.enums.button_type import ButtonType
from maxapi.types.message import Message

from comments_store import count_comments, upsert_post
from keyboards import comments_keyboard
from post_attachments import (
    has_media_attachments,
    merge_attachments_with_keyboard,
    serialize_media_attachments,
)
from post_payload import decode_post_id

logger = logging.getLogger(__name__)

_processed_mids: set[str] = set()
_pending_tasks: dict[str, asyncio.Task] = {}
_MAX_CACHE = 2000

# MAX иногда присылает message_created до загрузки фото — ждём, иначе edit сотрёт медиа
_MEDIA_RETRY_DELAYS_SEC = (2.0, 3.0, 5.0)


def attachments_have_comments_button(attachments: list | None) -> bool:
    if not attachments:
        return False
    for att in attachments:
        att_type = getattr(att, "type", None)
        if att_type != AttachmentType.INLINE_KEYBOARD and str(att_type) != "inline_keyboard":
            continue
        payload = getattr(att, "payload", None)
        buttons = getattr(payload, "buttons", None) if payload else None
        if not buttons:
            continue
        for row in buttons:
            for btn in row:
                btn_type = getattr(btn, "type", None)
                if btn_type == ButtonType.OPEN_APP or str(btn_type) == "open_app":
                    pl = getattr(btn, "payload", None)
                    if pl and decode_post_id(pl):
                        return True
    return False


def message_has_comments_button(message: Message) -> bool:
    body = message.body
    if not body:
        return False
    return attachments_have_comments_button(body.attachments)


def post_title_from_message(body) -> str:
    if body and body.text and body.text.strip():
        line = body.text.strip().split("\n", 1)[0]
        return line[:200]
    return "Пост в канале"


def _edit_text_from_body(body) -> str | None:
    if body and body.text and body.text.strip():
        return body.text
    return None


async def _load_fresh_body(bot: Bot, mid: str):
    try:
        msg = await bot.get_message(mid)
        return msg.body
    except Exception:
        logger.debug("get_message не удался для %s", mid, exc_info=True)
        return None


def _cancel_pending(mid: str) -> None:
    task = _pending_tasks.pop(mid, None)
    if task and not task.done():
        task.cancel()


def _remember(mid: str) -> None:
    _processed_mids.add(mid)
    _cancel_pending(mid)
    if len(_processed_mids) > _MAX_CACHE:
        _processed_mids.clear()


async def _retry_later(bot: Bot, mid: str, chat_id: int, delay: float, attempt: int) -> None:
    try:
        await asyncio.sleep(delay)
        await attach_comments_button(bot, mid=mid, chat_id=chat_id, attempt=attempt)
    except asyncio.CancelledError:
        pass


async def attach_comments_button(
    bot: Bot,
    message: Message | None = None,
    *,
    mid: str | None = None,
    chat_id: int | None = None,
    attempt: int = 0,
) -> bool:
    if message is not None:
        body = message.body
        if not body or not body.mid:
            return False
        mid = body.mid
        chat_id = message.recipient.chat_id

    if not mid or chat_id is None:
        return False

    if message and message_has_comments_button(message):
        _remember(mid)
        return False

    if mid in _processed_mids:
        return False

    body = message.body if message else None
    fresh = await _load_fresh_body(bot, mid)
    if fresh is not None:
        body = fresh

    if body is None:
        return False

    if attachments_have_comments_button(body.attachments):
        _remember(mid)
        return False

    has_media = has_media_attachments(body.attachments)

    if not has_media and attempt < len(_MEDIA_RETRY_DELAYS_SEC):
        if mid not in _pending_tasks:
            delay = _MEDIA_RETRY_DELAYS_SEC[attempt]
            logger.info(
                "Пост %s: медиа ещё нет — отложим кнопку на %.0f с (попытка %s)",
                mid,
                delay,
                attempt + 1,
            )
            _pending_tasks[mid] = asyncio.create_task(
                _retry_later(bot, mid, chat_id, delay, attempt + 1)
            )
        return False

    _cancel_pending(mid)

    title = post_title_from_message(body)
    text = body.text or ""
    media_json = serialize_media_attachments(body.attachments)
    upsert_post(
        mid,
        chat_id,
        title,
        message_text=text or None,
        media_attachments_json=media_json,
    )
    kb = comments_keyboard(mid, count_comments(mid)).as_markup()
    attachments = merge_attachments_with_keyboard(body.attachments, kb)
    edit_text = _edit_text_from_body(body)
    media_count = len(attachments) - 1

    try:
        await bot.edit_message(
            message_id=mid,
            text=edit_text,
            attachments=attachments,
        )
        _remember(mid)
        logger.info(
            "Кнопка комментариев на посте %s (медиа: %s, попытка %s)",
            mid,
            media_count,
            attempt,
        )
        return True
    except Exception:
        logger.exception("Не удалось добавить кнопку к посту %s", mid)
        return False
