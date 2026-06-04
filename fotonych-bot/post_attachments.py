"""Сохранение медиа при редактировании поста канала (кнопка комментариев)."""

from __future__ import annotations

import json
from typing import Any

from maxapi.enums.attachment import AttachmentType
from maxapi.types.attachments.attachment import Attachment


def _is_inline_keyboard(att: Any) -> bool:
    att_type = getattr(att, "type", None)
    return att_type == AttachmentType.INLINE_KEYBOARD or str(att_type) == "inline_keyboard"


def merge_attachments_with_keyboard(
    original: list[Any] | None,
    keyboard: Any,
) -> list[Any]:
    """Оставить фото/видео/файлы и заменить только inline-клавиатуру."""
    merged: list[Any] = []
    if original:
        for att in original:
            if _is_inline_keyboard(att):
                continue
            merged.append(att)
    merged.append(keyboard)
    return merged


def serialize_media_attachments(attachments: list[Any] | None) -> str | None:
    """JSON для БД (без клавиатуры)."""
    if not attachments:
        return None
    items: list[dict] = []
    for att in attachments:
        if _is_inline_keyboard(att):
            continue
        if hasattr(att, "model_dump"):
            items.append(att.model_dump(mode="json"))
        elif isinstance(att, dict):
            items.append(att)
    if not items:
        return None
    return json.dumps(items, ensure_ascii=False)


def deserialize_media_attachments(raw: str | None) -> list[Attachment]:
    if not raw:
        return []
    data = json.loads(raw)
    if not isinstance(data, list):
        return []
    return [Attachment.model_validate(item) for item in data]
