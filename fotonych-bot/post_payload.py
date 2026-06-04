"""Кодирование postId для open_app payload (только A-Za-z0-9_-)."""

from __future__ import annotations

import base64

_PREFIX = "p"


def encode_post_id(post_id: str) -> str:
    raw = base64.urlsafe_b64encode(post_id.encode("utf-8")).decode("ascii")
    return _PREFIX + raw.rstrip("=")


def decode_post_id(payload: str) -> str | None:
    if not payload or not payload.startswith(_PREFIX):
        return None
    raw = payload[len(_PREFIX) :]
    pad = "=" * (-len(raw) % 4)
    try:
        return base64.urlsafe_b64decode(raw + pad).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
