import os

from maxapi.types.attachments.buttons.callback_button import CallbackButton
from maxapi.types.attachments.buttons.link_button import LinkButton
from maxapi.types.attachments.buttons.open_app_button import OpenAppButton
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

from post_payload import encode_post_id

SITE_URL = os.getenv("SITE_URL", "https://max.avtmsk.ru").rstrip("/")
MAX_BOT_USERNAME = os.getenv("MAX_BOT_USERNAME", "id5406829253_bot")

CB_CLEAR = "clear"
CB_HELP = "help"
CB_MENU = "menu"


def main_menu_keyboard() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(CallbackButton(text="Очистить память", payload=CB_CLEAR))
    kb.row(CallbackButton(text="Помощь", payload=CB_HELP))
    kb.row(LinkButton(text="Сайт OMEGA", url=SITE_URL))
    return kb


def back_menu_keyboard() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(CallbackButton(text="← Меню", payload=CB_MENU))
    return kb


def comments_keyboard(post_id: str) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(
        OpenAppButton(
            text="💬 Комментарии",
            web_app=MAX_BOT_USERNAME,
            payload=encode_post_id(post_id),
        )
    )
    return kb
