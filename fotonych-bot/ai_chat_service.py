"""OMEGA Chat — вызов OpenAI (отдельно от служебного /ai и логистики)."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from openai import OpenAI

from ai_chat_store import (
    _is_pro,
    add_message,
    can_send_message,
    get_conversation,
    get_user,
    increment_usage,
    list_context_messages,
    log_event,
    upsert_user,
)

logger = logging.getLogger(__name__)

_client: OpenAI | None = None

DEFAULT_CHAT_PROMPT = """Ты OMEGA Chat — дружелюбный AI-ассистент OMEGA AI LAB.

Отвечай по-русски, ясно и по делу. Можешь помогать с идеями, текстами, объяснениями, планированием.

Это не служебная панель логистики: не выдумывай данные о рейсах, плитах и очередях завода.
Если спрашивают про работу на площадке — направь в avtmsk.ru/taksimo.html или в чат водителей.

Без политики, медицины и юриспруденции как замены специалисту. Будь вежлив и краток, если не просят развёрнуто."""


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY не задан")
        _client = OpenAI(api_key=api_key)
    return _client


def _system_prompt() -> str:
    return (os.getenv("OMEGA_CHAT_SYSTEM_PROMPT") or DEFAULT_CHAT_PROMPT).strip()


def _model_for_user(user: dict) -> str:
    if _is_pro(user):
        return (os.getenv("OMEGA_CHAT_MODEL_PRO") or "gpt-4.1").strip()
    return (os.getenv("OMEGA_CHAT_MODEL_FREE") or os.getenv("OPENAI_MODEL") or "gpt-4.1-mini").strip()


def _context_limit() -> int:
    raw = (os.getenv("OMEGA_CHAT_CONTEXT_MESSAGES") or "40").strip()
    try:
        return max(4, min(int(raw), 80))
    except ValueError:
        return 40


def _extract_output_text(response: Any) -> str:
    if getattr(response, "output_text", None):
        return str(response.output_text).strip()
    parts: list[str] = []
    for item in getattr(response, "output", []) or []:
        for block in getattr(item, "content", []) or []:
            text = getattr(block, "text", None)
            if text:
                parts.append(str(text))
    return "\n".join(parts).strip()


def _usage_tokens(response: Any) -> tuple[int | None, int | None]:
    usage = getattr(response, "usage", None)
    if not usage:
        return None, None
    pin = getattr(usage, "input_tokens", None)
    if pin is None:
        pin = getattr(usage, "prompt_tokens", None)
    pout = getattr(usage, "output_tokens", None)
    if pout is None:
        pout = getattr(usage, "completion_tokens", None)
    return (
        int(pin) if pin is not None else None,
        int(pout) if pout is not None else None,
    )


def _generate_sync(
    *,
    model: str,
    context: list[dict[str, str]],
    user_message: str,
) -> tuple[str, int | None, int | None]:
    input_items: list[dict[str, str]] = [{"role": "system", "content": _system_prompt()}]
    input_items.extend(context)
    input_items.append({"role": "user", "content": user_message})

    response = _get_client().responses.create(model=model, input=input_items)
    reply = _extract_output_text(response) or "Не удалось получить ответ."
    pin, pout = _usage_tokens(response)
    return reply, pin, pout


class ChatError(Exception):
    def __init__(self, message: str, *, code: str = "error") -> None:
        super().__init__(message)
        self.code = code


async def send_chat_message(
    *,
    max_user_id: int,
    display_name: str,
    conversation_id: int,
    user_message: str,
) -> dict:
    text = (user_message or "").strip()
    if not text:
        raise ChatError("Введите сообщение", code="empty")
    if len(text) > 4000:
        raise ChatError("Слишком длинное сообщение (макс. 4000)", code="too_long")

    user = upsert_user(max_user_id=max_user_id, display_name=display_name)
    if not get_conversation(conversation_id=conversation_id, max_user_id=max_user_id):
        raise ChatError("Диалог не найден", code="not_found")

    ok, reason, used, limit = can_send_message(max_user_id, user)
    if not ok:
        if reason == "daily_limit":
            log_event(max_user_id=max_user_id, event_type="limit_hit", meta={"used": used, "limit": limit})
            raise ChatError(
                f"Лимит на сегодня исчерпан ({used}/{limit}). Завтра будет новый лимит.",
                code="daily_limit",
            )
        raise ChatError(reason or "Отправка недоступна", code=reason or "blocked")

    context = list_context_messages(
        conversation_id=conversation_id,
        max_user_id=max_user_id,
        limit=_context_limit(),
    )
    model = _model_for_user(user)

    try:
        reply, pin, pout = await asyncio.to_thread(
            _generate_sync,
            model=model,
            context=context,
            user_message=text,
        )
    except RuntimeError as e:
        logger.exception("OMEGA Chat: OpenAI config")
        raise ChatError(str(e), code="openai_config") from e
    except Exception as e:
        logger.exception("OMEGA Chat: OpenAI request user=%s", max_user_id)
        raise ChatError("ИИ временно недоступен. Попробуйте позже.", code="openai_error") from e

    user_msg = add_message(
        conversation_id=conversation_id,
        max_user_id=max_user_id,
        role="user",
        content=text,
    )
    asst_msg = add_message(
        conversation_id=conversation_id,
        max_user_id=max_user_id,
        role="assistant",
        content=reply,
        model=model,
        prompt_tokens=pin,
        completion_tokens=pout,
    )
    increment_usage(max_user_id=max_user_id, prompt_tokens=pin or 0, completion_tokens=pout or 0)
    log_event(max_user_id=max_user_id, event_type="chat_send", meta={"conversation_id": conversation_id})

    _, _, used_after, limit_after = can_send_message(max_user_id, get_user(max_user_id))

    return {
        "user_message": user_msg,
        "assistant_message": asst_msg,
        "messages_today": used_after,
        "daily_limit": limit_after,
        "can_send": used_after < limit_after,
    }
