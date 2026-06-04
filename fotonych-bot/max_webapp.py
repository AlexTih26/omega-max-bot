"""Валидация initData мини-приложения MAX (WebAppData)."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import unquote

_MAX_AGE_SEC = 3600


def _build_launch_params(pairs: list[tuple[str, str]]) -> str:
    filtered = [(k, v) for k, v in pairs if k != "hash"]
    filtered.sort(key=lambda x: x[0])
    return "\n".join(f"{k}={v}" for k, v in filtered)


def validate_init_data(init_data: str, bot_token: str) -> dict | None:
    if not init_data or not bot_token:
        return None

    pairs: list[tuple[str, str]] = []
    hash_value: str | None = None

    for part in init_data.split("&"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        value = unquote(value)
        if key == "hash":
            if hash_value is not None:
                return None
            hash_value = value
        else:
            pairs.append((key, value))

    if not hash_value:
        return None

    launch_params = _build_launch_params(pairs)
    secret_key = hmac.new(
        b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256
    ).digest()
    calculated = hmac.new(
        secret_key, launch_params.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    if calculated != hash_value:
        return None

    result: dict = {"hash": hash_value}
    for key, value in pairs:
        if key in ("user", "chat"):
            try:
                result[key] = json.loads(value)
            except json.JSONDecodeError:
                return None
        elif key == "auth_date":
            try:
                result[key] = int(value)
            except ValueError:
                return None
        else:
            result[key] = value

    auth_date = result.get("auth_date")
    if isinstance(auth_date, int) and time.time() - auth_date > _MAX_AGE_SEC:
        return None

    return result


def display_name_from_user(user: dict | None) -> str:
    if not user or not isinstance(user, dict):
        return "Пользователь MAX"
    first = (user.get("first_name") or "").strip()
    last = (user.get("last_name") or "").strip()
    username = (user.get("username") or "").strip()
    if first and last:
        return f"{first} {last}"
    if first:
        return first
    if username:
        return f"@{username}"
    return "Пользователь MAX"
