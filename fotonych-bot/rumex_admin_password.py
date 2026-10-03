"""Хэширование паролей отдельного браузерного входа управления РУМЕКС."""

from __future__ import annotations

import hashlib
import hmac
import secrets


def hash_browser_password(password: str) -> str:
    """Подготовить scrypt-хэш для отдельного браузерного входа администратора."""
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise ValueError("Пароль должен содержать от 12 до 256 символов")
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt.encode("ascii"), n=2**14, r=8, p=1
    ).hex()
    return salt + "$" + digest


def verify_browser_password(password: str, password_hash: str) -> bool:
    """Сверить пароль с scrypt-хэшем без раскрытия его содержимого."""
    if not isinstance(password, str) or not isinstance(password_hash, str):
        return False
    try:
        salt, expected = password_hash.split("$", 1)
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=salt.encode("ascii"), n=2**14, r=8, p=1
        ).hex()
    except (ValueError, UnicodeEncodeError):
        return False
    return hmac.compare_digest(actual, expected)
