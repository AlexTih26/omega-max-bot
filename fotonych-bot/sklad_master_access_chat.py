"""Заявки на доступ к Склад Мастер: уведомления супер-админам и callback-кнопки."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from maxapi import Bot
from maxapi.types.attachments.buttons.callback_button import CallbackButton
from maxapi.types.updates.message_callback import MessageCallback
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

from max_webapp import display_name_from_user
from sklad_master_store import (
    get_pending_access_request,
    resolve_access_request,
    save_access_request_notify_messages,
    submit_access_request,
)
from super_admin import is_super_admin, super_admin_ids

logger = logging.getLogger(__name__)

_bot: Bot | None = None
CB_PREFIX = "smacc:"
CB_DONE = "smacc:done"

ROLE_LABELS = {
    "master": "мастер",
    "supply": "снабжение",
    "manager": "руководитель",
}


def set_bot(bot: Bot) -> None:
    global _bot
    _bot = bot


def _tz_label() -> str:
    tz_name = (os.getenv("DRIVERS_TIMEZONE") or "Asia/Irkutsk").strip()
    try:
        return datetime.now(ZoneInfo(tz_name)).strftime("%d.%m %H:%M")
    except Exception:
        return datetime.now().strftime("%d.%m %H:%M")


def _request_message_text(*, display_name: str, max_id: int, footer: str = "") -> str:
    lines = [
        "📦 Склад Мастер · заявка на доступ",
        "",
        f"Имя в MAX: {display_name or '—'}",
        f"MAX id: {max_id}",
    ]
    if footer:
        lines.extend(["", footer])
    return "\n".join(lines)


def access_request_keyboard(max_id: int) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    base = f"{CB_PREFIX}{max_id}:"
    kb.row(
        CallbackButton(text="Мастер", payload=f"{base}master"),
        CallbackButton(text="Снабжение", payload=f"{base}supply"),
    )
    kb.row(
        CallbackButton(text="Руководитель", payload=f"{base}manager"),
        CallbackButton(text="Отклонить", payload=f"{base}reject"),
    )
    return kb


def access_request_processed_keyboard() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(CallbackButton(text="✓ Уже обработано", payload=CB_DONE))
    return kb


def _message_mid(msg) -> str | None:
    if msg is None:
        return None
    body = getattr(getattr(msg, "message", None), "body", None)
    mid = getattr(body, "mid", None) if body else None
    return str(mid) if mid else None


async def notify_super_admins_access_request(
    *,
    request_id: int,
    max_id: int,
    display_name: str,
) -> None:
    bot = _bot
    admin_ids = super_admin_ids()
    if bot is None or not admin_ids:
        logger.warning("Склад Мастер: нет бота или супер-админов для заявки %s", max_id)
        return
    text = _request_message_text(display_name=display_name, max_id=max_id)
    kb = access_request_keyboard(max_id).as_markup()
    messages: dict[int, str] = {}
    for uid in sorted(admin_ids):
        try:
            msg = await bot.send_message(user_id=uid, text=text, attachments=[kb])
            mid = _message_mid(msg)
            if mid:
                messages[uid] = mid
        except Exception:
            logger.exception("Склад Мастер: не удалось отправить заявку admin=%s", uid)
    if messages:
        save_access_request_notify_messages(request_id, messages)


async def submit_and_notify_access_request(
    *,
    max_id: int,
    display_name: str,
) -> tuple[bool, str]:
    ok, message, row = submit_access_request(max_id=max_id, display_name=display_name)
    if not ok:
        return False, message
    await notify_super_admins_access_request(
        request_id=int(row["id"]),
        max_id=max_id,
        display_name=display_name,
    )
    return True, message


def _resolved_footer(*, action: str, actor_name: str) -> str:
    stamp = _tz_label()
    if action == "reject":
        return f"✗ Отклонено · {actor_name} · {stamp}"
    role = ROLE_LABELS.get(action, action)
    return f"✓ Подключён как {role} · {actor_name} · {stamp}"


async def _edit_access_request_messages(
    request: dict,
    *,
    footer: str,
    actor_admin_id: int | None = None,
) -> None:
    bot = _bot
    if bot is None:
        return
    text = _request_message_text(
        display_name=str(request.get("display_name") or ""),
        max_id=int(request.get("max_id") or 0),
        footer=footer,
    )
    kb = access_request_processed_keyboard().as_markup()
    notify = request.get("notify_messages") or {}
    for uid_str, mid in notify.items():
        try:
            admin_id = int(uid_str)
        except (TypeError, ValueError):
            continue
        try:
            if actor_admin_id is not None and admin_id == actor_admin_id:
                continue
            await bot.edit_message(message_id=mid, text=text, attachments=[kb])
        except Exception:
            logger.exception(
                "Склад Мастер: не удалось обновить заявку admin=%s mid=%s",
                uid_str,
                mid,
            )


async def _notify_access_granted(*, max_id: int, role: str) -> None:
    bot = _bot
    if bot is None:
        return
    role_label = ROLE_LABELS.get(role, role)
    text = (
        f"✅ Склад Мастер · вам открыт доступ ({role_label}).\n"
        "Закройте и откройте приложение снова."
    )
    try:
        await bot.send_message(user_id=max_id, text=text)
    except Exception:
        logger.exception("Склад Мастер: не удалось уведомить user_id=%s", max_id)


async def _notify_access_rejected(*, max_id: int) -> None:
    bot = _bot
    if bot is None:
        return
    text = "❌ Склад Мастер · заявка на доступ отклонена администратором."
    try:
        await bot.send_message(user_id=max_id, text=text)
    except Exception:
        logger.exception("Склад Мастер: не удалось уведомить об отказе user_id=%s", max_id)


def _callback_user_name(user) -> str:
    if user is None:
        return "Администратор"
    return display_name_from_user(
        {
            "first_name": getattr(user, "first_name", "") or "",
            "last_name": getattr(user, "last_name", "") or "",
            "username": getattr(user, "username", "") or "",
        }
    )


async def handle_sklad_access_callback(event: MessageCallback, bot: Bot) -> bool:
    payload = (event.callback.payload if event.callback else "") or ""
    if not payload.startswith(CB_PREFIX):
        return False

    actor = event.callback.user if event.callback else None
    actor_id = actor.user_id if actor else None
    if actor_id is None or not is_super_admin(actor_id):
        await event.answer(notification="Нет доступа")
        return True

    if payload == CB_DONE:
        await event.answer(notification="Уже обработано")
        return True

    parts = payload.split(":")
    if len(parts) != 3 or parts[0] != "smacc":
        await event.answer(notification="Некорректная кнопка")
        return True

    try:
        target_id = int(parts[1])
    except ValueError:
        await event.answer(notification="Некорректная заявка")
        return True
    action = parts[2].strip().lower()
    actor_name = _callback_user_name(actor)

    ok, result, request = resolve_access_request(
        max_id=target_id,
        action=action,
        actor_max_id=int(actor_id),
        actor_name=actor_name,
    )
    if not ok:
        if result == "already_processed":
            footer = "✓ Уже обработано"
            pending = get_pending_access_request(target_id)
            if pending is None:
                if event.message is not None:
                    try:
                        await event.edit(
                            text=_request_message_text(
                                display_name="—",
                                max_id=target_id,
                                footer=footer,
                            ),
                            attachments=[access_request_processed_keyboard().as_markup()],
                        )
                    except Exception:
                        pass
            await event.answer(notification="Уже обработано")
            return True
        await event.answer(notification=result or "Ошибка")
        return True

    footer = _resolved_footer(action=action if action != "reject" else "reject", actor_name=actor_name)
    if request:
        if event.message is not None:
            try:
                await event.edit(
                    text=_request_message_text(
                        display_name=str(request.get("display_name") or ""),
                        max_id=target_id,
                        footer=footer,
                    ),
                    attachments=[access_request_processed_keyboard().as_markup()],
                )
            except Exception:
                logger.exception("Склад Мастер: edit текущего сообщения заявки")
        await _edit_access_request_messages(
            request,
            footer=footer,
            actor_admin_id=int(actor_id),
        )

    if action == "reject":
        await _notify_access_rejected(max_id=target_id)
        await event.answer(notification="Отклонено")
        return True

    await _notify_access_granted(max_id=target_id, role=action)
    role_label = ROLE_LABELS.get(action, action)
    await event.answer(notification=f"Подключён как {role_label}")
    return True
