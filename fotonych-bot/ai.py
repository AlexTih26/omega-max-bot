import asyncio
import os
import time
from typing import Any

from openai import OpenAI

from omega_assistant import DEFAULT_SYSTEM_PROMPT

DEFAULT_MODEL = "gpt-4.1-mini"

MAX_MESSAGES = 40
HISTORY_TTL_SEC = 2 * 3600

_client: OpenAI | None = None
_history: dict[str, list[dict[str, str]]] = {}
_history_ts: dict[str, float] = {}


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY не задан")
        _client = OpenAI(api_key=api_key)
    return _client


def _system_prompt() -> str:
    return (os.getenv("SYSTEM_PROMPT") or DEFAULT_SYSTEM_PROMPT).strip()


def _model() -> str:
    return (os.getenv("OPENAI_MODEL") or DEFAULT_MODEL).strip()


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


def _prune_history(user_id: str) -> None:
    ts = _history_ts.get(user_id)
    if ts and time.time() - ts > HISTORY_TTL_SEC:
        _history.pop(user_id, None)
        _history_ts.pop(user_id, None)


def _add_to_history(user_id: str, role: str, content: str) -> None:
    _prune_history(user_id)
    msgs = _history.setdefault(user_id, [])
    msgs.append({"role": role, "content": content})
    if len(msgs) > MAX_MESSAGES:
        _history[user_id] = msgs[-MAX_MESSAGES:]
    _history_ts[user_id] = time.time()


def clear_history(user_id: str) -> None:
    _history.pop(user_id, None)
    _history_ts.pop(user_id, None)


def _generate_sync(user_id: str, user_message: str) -> str:
    _add_to_history(user_id, "user", user_message)
    input_items = [{"role": "system", "content": _system_prompt()}]
    input_items.extend(_history.get(user_id, []))

    response = _get_client().responses.create(
        model=_model(),
        input=input_items,
    )
    reply = _extract_output_text(response) or "Пустой ответ от модели."
    _add_to_history(user_id, "assistant", reply)
    return reply


async def ask(user_id: str, user_message: str) -> str:
    return await asyncio.to_thread(_generate_sync, user_id, user_message)
