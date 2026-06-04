"""Кнопка «Комментарии» под постами канала MAX."""

from __future__ import annotations

import logging

from maxapi import Bot
from maxapi.enums.attachment import AttachmentType
from maxapi.enums.button_type import ButtonType
from maxapi.types.message import Message

from comments_store import count_comments, upsert_post
from keyboards import comments_keyboard
from post_attachments import (
    merge_attachments_with_keyboard,
    serialize_media_attachments,
)
from post_payload import decode_post_id

logger = logging.getLogger(__name__)

_processed_mids: set[str] = set()
_MAX_CACHE = 2000


def message_has_comments_button(message: Message) -> bool:
    body = message.body
    if not body or not body.attachments:
        return False
    for att in body.attachments:
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


def post_title_from_message(message: Message) -> str:
    body = message.body
    if body and body.text and body.text.strip():
        line = body.text.strip().split("\n", 1)[0]
        return line[:200]
    return "Пост в канале"


def _edit_text_from_body(body) -> str | None:
    if body.text and body.text.strip():
        return body.text
    return None


async def attach_comments_button(bot: Bot, message: Message) -> bool:
    body = message.body
    if not body or not body.mid:
        return False

    mid = body.mid
    if mid in _processed_mids or message_has_comments_button(message):
        _remember(mid)
        return False

    recipient = message.recipient
    chat_id = recipient.chat_id
    if chat_id is None:
        return False

    title = post_title_from_message(message)
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

    try:
        await bot.edit_message(
            message_id=mid,
            text=edit_text,
            attachments=attachments,
        )
        _remember(mid)
        logger.info(
            "Кнопка комментариев добавлена к посту %s (медиа: %s)",
            mid,
            len(attachments) - 1,
        )
        return True
    except Exception:
        logger.exception("Не удалось добавить кнопку к посту %s", mid)
        return False


def _remember(mid: str) -> None:
    _processed_mids.add(mid)
    if len(_processed_mids) > _MAX_CACHE:
        _processed_mids.clear()
