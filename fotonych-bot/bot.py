import asyncio
import logging
from pathlib import Path

from dotenv import load_dotenv
from maxapi import Bot, Dispatcher
from maxapi.enums.chat_type import ChatType
from maxapi.filters import ChannelPostFilter
from maxapi.filters.command import Command, CommandStart
from maxapi.types import BotAdded, BotStarted, Command, MessageCreated
from maxapi.types.updates.message_edited import MessageEdited
from maxapi.types.command import BotCommand
from maxapi.types.updates.message_callback import MessageCallback

from ai import ask, clear_history
from omega_assistant import AI_HELP, WORK_BOT_REPLY, try_builtin_answer
from channel_posts import attach_comments_button
from comments_api import start_comments_api
from comments_button import set_bot
from taksimo_chat_handlers import (
    handle_taksimo_callback,
    handle_taksimo_chat_message,
    is_taksimo_chat,
    send_taksimo_welcome,
)
from drivers_chat import (
    drivers_chat_id,
    drivers_reminder_loop,
    handle_drivers_callback,
    handle_drivers_message,
    is_drivers_chat,
    send_drivers_buttons,
    refresh_drivers_menu,
    set_drivers_bot,
    sync_drivers_registry,
    drivers_menu_attachments,
    drivers_reminder_enabled,
)
from taksimo_backup import backup_taksimo_db, daily_backup_loop
from taksimo_notify import daily_report_loop, notify_chat_id, set_bot as set_taksimo_bot
from keyboards import (
    CB_CLEAR,
    CB_HELP,
    CB_MENU,
    TAKSIMO_FIND_HINT,
    admin_open_app_attachments,
    back_menu_keyboard,
    main_menu_keyboard,
    taksimo_find_attachments,
)
from super_admin import is_super_admin

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

bot = Bot()
dp = Dispatcher()

MENU_TEXT = (
    "OMEGA — рабочий бот.\n"
    "Кнопки меню — ниже.\n"
    "💬 OMEGA Chat — отдельный AI-чат.\n"
    "ИИ в чате: /ai (служебный режим)."
)

HELP_TEXT = (
    "OMEGA — рабочий бот OMEGA AI LAB.\n\n"
    "• Таксимо, FOTON, сайт — кнопки меню\n"
    "• ИИ только по команде /ai\n"
    "• /clear — очистить память диалога ИИ\n\n"
    "Команды: /start · /ai · /clear · /taksimo_chat"
)

MENU_WORDS = frozenset({"меню", "menu", "старт"})


def menu_attachments(user_id: int | None = None):
    return [main_menu_keyboard(user_id=user_id).as_markup()]


async def send_menu_message(
    *,
    chat_id: int | None = None,
    user_id: int | None = None,
    text: str = MENU_TEXT,
) -> None:
    await bot.send_message(
        chat_id=chat_id,
        user_id=user_id,
        text=text,
        attachments=menu_attachments(user_id=user_id),
    )
    logger.info("Меню отправлено chat_id=%s user_id=%s", chat_id, user_id)


@dp.bot_started()
async def on_bot_started(event: BotStarted) -> None:
    await send_menu_message(user_id=event.user.user_id)


@dp.bot_added()
async def on_bot_added(event: BotAdded) -> None:
    if event.is_channel:
        return
    logger.info("Бот добавлен в чат chat_id=%s", event.chat_id)
    taksimo_chat = notify_chat_id()
    if taksimo_chat is not None and event.chat_id == taksimo_chat:
        try:
            await send_taksimo_welcome(bot, chat_id=event.chat_id)
        except Exception:
            logger.exception("bot_added: приветствие Таксимо chat_id=%s", event.chat_id)
        return
    drivers_chat = drivers_chat_id()
    if drivers_chat is not None and event.chat_id == drivers_chat:
        try:
            await send_drivers_buttons(bot, chat_id=event.chat_id)
        except Exception:
            logger.exception("bot_added: приветствие водителей chat_id=%s", event.chat_id)
        return
    try:
        await send_menu_message(chat_id=event.chat_id)
    except Exception:
        logger.exception("bot_added: не удалось отправить меню в chat_id=%s", event.chat_id)


channel_post = ChannelPostFilter()


@dp.message_created(channel_post)
async def on_channel_post(event: MessageCreated) -> None:
    await attach_comments_button(bot, event.message)


@dp.message_edited(channel_post)
async def on_channel_post_edited(event: MessageEdited) -> None:
    """Пост дозагрузил фото — вешаем кнопку, не трогая медиа."""
    await attach_comments_button(bot, event.message)


@dp.message_created(CommandStart())
async def on_start(event: MessageCreated) -> None:
    if is_drivers_chat(event.message.recipient.chat_id):
        cid = event.message.recipient.chat_id
        if cid is not None:
            await send_drivers_buttons(bot, chat_id=cid)
        return
    if is_taksimo_chat(event.message.recipient.chat_id):
        await event.message.answer(TAKSIMO_FIND_HINT, attachments=taksimo_find_attachments())
        return
    sender = event.message.sender
    uid = sender.user_id if sender else None
    await event.message.answer(MENU_TEXT, attachments=menu_attachments(user_id=uid))


@dp.message_created(Command("admin"))
async def on_admin(event: MessageCreated) -> None:
    if is_drivers_chat(event.message.recipient.chat_id) or is_taksimo_chat(
        event.message.recipient.chat_id
    ):
        return
    sender = event.message.sender
    if not sender:
        await event.message.answer("Не удалось определить пользователя.")
        return
    if not is_super_admin(sender.user_id):
        await event.message.answer("Команда только для администратора.")
        return
    await event.message.answer(
        "⚙️ Админ-панель: объявления и парк водителей.\n"
        "После объявления меню в чате обновится автоматически.",
        attachments=admin_open_app_attachments(),
    )


@dp.message_created(Command("menu"))
@dp.message_created(Command("taksimo"))
@dp.message_created(Command("drivers"))
async def on_taksimo_menu_command(event: MessageCreated) -> None:
    if is_drivers_chat(event.message.recipient.chat_id):
        await send_drivers_buttons(bot, chat_id=event.message.recipient.chat_id or 0)
        return
    if is_taksimo_chat(event.message.recipient.chat_id):
        await event.message.answer(TAKSIMO_FIND_HINT, attachments=taksimo_find_attachments())
        return
    await event.message.answer(
        "Меню Таксимо — в чате отчётов площадки.\n"
        "Сервис: https://avtmsk.ru/taksimo.html"
    )


@dp.message_created(Command("taksimo_chat"))
async def on_taksimo_chat(event: MessageCreated) -> None:
    chat_id = event.message.recipient.chat_id
    if chat_id is None:
        await event.message.answer("Команда /taksimo_chat работает в групповом чате MAX.")
        return
    await event.message.answer(
        "Чат для уведомлений Таксимо:\n\n"
        f"TAKSIMO_NOTIFY_CHAT_ID={chat_id}\n\n"
        "Добавьте эту строку в .env на сервере и перезапустите бота."
    )


@dp.message_created(Command("drivers_chat"))
async def on_drivers_chat_cmd(event: MessageCreated) -> None:
    chat_id = event.message.recipient.chat_id
    if chat_id is None:
        await event.message.answer("Команда /drivers_chat — в групповом чате MAX.")
        return
    await event.message.answer(
        "Чат водителей:\n\n"
        f"DRIVERS_CHAT_ID={chat_id}\n\n"
        "Добавьте в .env и перезапустите бота."
    )


def _ai_question_from_message(event: MessageCreated) -> str:
    body = event.message.body
    text = body.text.strip() if body and body.text else ""
    if not text:
        return ""
    parts = text.split(None, 1)
    if len(parts) < 2:
        return ""
    return parts[1].strip()


@dp.message_created(Command("id"))
@dp.message_created(Command("myid"))
async def on_my_id(event: MessageCreated) -> None:
    if is_drivers_chat(event.message.recipient.chat_id) or is_taksimo_chat(
        event.message.recipient.chat_id
    ):
        return
    sender = event.message.sender
    if not sender:
        await event.message.answer("Не удалось определить пользователя.")
        return
    uid = sender.user_id
    name = (getattr(sender, "name", None) or getattr(sender, "first_name", None) or "—")
    await event.message.answer(
        f"Ваш MAX id: {uid}\nИмя: {name}\n\n"
        "Передайте id диспетчеру в личку (не в общий чат)."
    )


@dp.message_created(Command("ai"))
async def on_ai(event: MessageCreated) -> None:
    if is_drivers_chat(event.message.recipient.chat_id) or is_taksimo_chat(
        event.message.recipient.chat_id
    ):
        return

    question = _ai_question_from_message(event)
    if not question:
        await event.message.answer(AI_HELP)
        return

    builtin = try_builtin_answer(question)
    if builtin:
        await event.message.answer(builtin)
        return

    sender = event.message.sender
    user_id = str(sender.user_id) if sender else "unknown"
    try:
        async with event.message.typing():
            reply = await ask(user_id, question)
    except Exception:
        logger.exception("OpenAI request failed")
        reply = "ИИ недоступен. Повторите позже или откройте avtmsk.ru/taksimo.html"
    await event.message.answer(reply)


@dp.message_created(Command("clear"))
async def on_clear(event: MessageCreated) -> None:
    if is_drivers_chat(event.message.recipient.chat_id) or is_taksimo_chat(
        event.message.recipient.chat_id
    ):
        return
    sender = event.message.sender
    if sender:
        clear_history(str(sender.user_id))
        await event.message.answer("🧹 Память очищена. Начинаем с чистого листа!")
    else:
        await event.message.answer("Не удалось определить пользователя.")


@dp.message_callback()
async def on_callback(event: MessageCallback) -> None:
    payload = event.callback.payload if event.callback else None
    if not payload or event.message is None:
        await event.answer()
        return

    user_id = str(event.callback.user.user_id)
    chat_id = event.message.recipient.chat_id if event.message else None

    if is_drivers_chat(chat_id) and payload in (CB_CLEAR, CB_HELP, CB_MENU):
        await event.answer(notification="Меню закреплено сверху")
        return

    if payload == CB_CLEAR:
        clear_history(user_id)
        await event.answer(notification="Память очищена")
        uid = event.callback.user.user_id if event.callback and event.callback.user else None
        await event.edit(
            text="🧹 История диалога очищена.\nЗадайте новый вопрос или откройте меню.",
            attachments=[main_menu_keyboard(user_id=uid).as_markup()],
        )
        return

    if payload == CB_HELP:
        await event.edit(
            text=HELP_TEXT,
            attachments=[back_menu_keyboard().as_markup()],
        )
        return

    if payload == CB_MENU:
        uid = event.callback.user.user_id if event.callback and event.callback.user else None
        await event.edit(
            text="Главное меню OMEGA:",
            attachments=[main_menu_keyboard(user_id=uid).as_markup()],
        )
        return

    if await handle_drivers_callback(event, bot):
        return

    if await handle_taksimo_callback(event, bot):
        return

    await event.answer()


@dp.message_created()
async def on_message(event: MessageCreated) -> None:
    if event.message.recipient.chat_type == ChatType.CHANNEL:
        return

    if is_drivers_chat(event.message.recipient.chat_id):
        await handle_drivers_message(event, bot)
        return

    if is_taksimo_chat(event.message.recipient.chat_id):
        await handle_taksimo_chat_message(event)
        return

    sender = event.message.sender
    if sender and sender.is_bot:
        return

    body = event.message.body
    text = body.text.strip() if body and body.text else ""
    if not text or text.startswith("/"):
        return

    if text.lower() in MENU_WORDS:
        await event.message.answer(MENU_TEXT, attachments=menu_attachments())
        return

    await event.message.answer(WORK_BOT_REPLY)


async def main() -> None:
    await bot.delete_webhook()
    await bot.set_my_commands(
        BotCommand(name="start", description="Меню и сервисы"),
        BotCommand(name="id", description="Ваш MAX id (личный чат)"),
        BotCommand(name="ai", description="Служебный ИИ-помощник"),
        BotCommand(name="clear", description="Очистить память ИИ"),
        BotCommand(name="taksimo_chat", description="ID чата уведомлений Таксимо"),
        BotCommand(name="drivers_chat", description="ID чата водителей"),
        BotCommand(name="menu", description="Меню Таксимо (в чате отчётов)"),
        BotCommand(name="taksimo", description="Меню Таксимо"),
    )
    set_bot(bot)
    set_taksimo_bot(bot)
    set_drivers_bot(bot)
    backup_taksimo_db(reason="startup")
    api_runner = await start_comments_api()
    drivers_cid = drivers_chat_id()
    if drivers_cid is not None:
        try:
            n = sync_drivers_registry()
            await refresh_drivers_menu(bot, chat_id=drivers_cid)
            logger.info("Реестр водителей при старте: %s, chat_id=%s", n, drivers_cid)
        except Exception:
            logger.exception("Не удалось загрузить реестр водителей")
    report_task = asyncio.create_task(daily_report_loop())
    backup_task = asyncio.create_task(daily_backup_loop())
    drivers_remind_task = None
    if drivers_reminder_enabled():
        drivers_remind_task = asyncio.create_task(drivers_reminder_loop())
    try:
        await dp.start_polling(bot)
    finally:
        report_task.cancel()
        backup_task.cancel()
        if drivers_remind_task is not None:
            drivers_remind_task.cancel()
        await api_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
