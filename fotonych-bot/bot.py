import asyncio
import logging
from pathlib import Path

from dotenv import load_dotenv
from maxapi import Bot, Dispatcher
from maxapi.enums.chat_type import ChatType
from maxapi.filters import ChannelPostFilter
from maxapi.filters.command import CommandStart
from maxapi.types import BotAdded, BotStarted, Command, MessageCreated
from maxapi.types.command import BotCommand
from maxapi.types.updates.message_callback import MessageCallback

from ai import ask, clear_history
from channel_posts import attach_comments_button
from comments_api import start_comments_api
from keyboards import (
    CB_CLEAR,
    CB_HELP,
    CB_MENU,
    back_menu_keyboard,
    main_menu_keyboard,
)

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

bot = Bot()
dp = Dispatcher()

MENU_TEXT = (
    "OMEGA — AI-помощник в MAX.\n"
    "Кнопки меню — под этим сообщением.\n"
    "Или напишите вопрос текстом."
)

HELP_TEXT = (
    "OMEGA — AI-помощник в MAX.\n\n"
    "• Напишите вопрос текстом — отвечу с учётом диалога\n"
    "• «Очистить память» — начать разговор заново\n"
    "• «Сайт OMEGA» — страница в стиле MAX\n\n"
    "Команды: /start — меню, /clear — очистить память"
)

MENU_WORDS = frozenset({"меню", "menu", "старт"})


def menu_attachments():
    return [main_menu_keyboard().as_markup()]


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
        attachments=menu_attachments(),
    )
    logger.info("Меню отправлено chat_id=%s user_id=%s", chat_id, user_id)


@dp.bot_started()
async def on_bot_started(event: BotStarted) -> None:
    await send_menu_message(user_id=event.user.user_id)


@dp.bot_added()
async def on_bot_added(event: BotAdded) -> None:
    if event.is_channel:
        return
    try:
        await send_menu_message(chat_id=event.chat_id)
    except Exception:
        logger.exception("bot_added: не удалось отправить меню в chat_id=%s", event.chat_id)


channel_post = ChannelPostFilter()


@dp.message_created(channel_post)
async def on_channel_post(event: MessageCreated) -> None:
    await attach_comments_button(bot, event.message)


@dp.message_created(CommandStart())
async def on_start(event: MessageCreated) -> None:
    await event.message.answer(MENU_TEXT, attachments=menu_attachments())


@dp.message_created(Command("clear"))
async def on_clear(event: MessageCreated) -> None:
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

    if payload == CB_CLEAR:
        clear_history(user_id)
        await event.answer(notification="Память очищена")
        await event.edit(
            text="🧹 История диалога очищена.\nЗадайте новый вопрос или откройте меню.",
            attachments=[main_menu_keyboard().as_markup()],
        )
        return

    if payload == CB_HELP:
        await event.edit(
            text=HELP_TEXT,
            attachments=[back_menu_keyboard().as_markup()],
        )
        return

    if payload == CB_MENU:
        await event.edit(
            text="Главное меню OMEGA:",
            attachments=[main_menu_keyboard().as_markup()],
        )
        return

    await event.answer()


@dp.message_created()
async def on_message(event: MessageCreated) -> None:
    if event.message.recipient.chat_type == ChatType.CHANNEL:
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

    user_id = str(sender.user_id) if sender else "unknown"

    try:
        async with event.message.typing():
            reply = await ask(user_id, text)
    except Exception:
        logger.exception("OpenAI request failed")
        reply = "Сейчас не могу ответить — попробуйте чуть позже."

    await event.message.reply(reply)


async def main() -> None:
    await bot.delete_webhook()
    await bot.set_my_commands(
        BotCommand(name="start", description="Показать меню"),
        BotCommand(name="clear", description="Очистить память"),
    )
    api_runner = await start_comments_api()
    try:
        await dp.start_polling(bot)
    finally:
        await api_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
