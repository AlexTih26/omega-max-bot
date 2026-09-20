import os

from maxapi.types.attachments.buttons.callback_button import CallbackButton
from maxapi.types.attachments.buttons.open_app_button import OpenAppButton
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

from access_roles import has_private_menu_access, has_stock_access, roles_for_max_user
from post_payload import encode_post_id

MAX_BOT_USERNAME = os.getenv("MAX_BOT_USERNAME", "id5406829253_bot")

CB_CLEAR = "clear"
CB_HELP = "help"
CB_MENU = "menu"
TAKSIMO_FIND_PAYLOAD = "taksimo_find"
OMEGA_CHAT_PAYLOAD = "chat"
ADMIN_APP_PAYLOAD = "admin"
ACCOUNTANT_APP_PAYLOAD = "accountant"
RUMEX_DISPATCHER_APP_PAYLOAD = "rumex_dispatcher"
RUMEX_MANAGEMENT_APP_PAYLOAD = "rumex_management"
SKLAD_MASTER_PAYLOAD = "sklad_master"

TAKSIMO_FIND_HINT = (
    "🔍 Поиск плиты — кнопкой «Где плита» под отчётом.\n"
    "Откроется панель в MAX, чат не засоряется."
)


def main_menu_keyboard(user_id: int | None = None) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    roles = roles_for_max_user(user_id)
    if has_stock_access(roles):
        kb.row(
            OpenAppButton(
                text="📱 Склад Мастер",
                web_app=MAX_BOT_USERNAME,
                payload=SKLAD_MASTER_PAYLOAD,
            )
        )
    if "rumex_dispatcher" in roles:
        kb.row(
            OpenAppButton(
                text="🚚 Кабинет диспетчера РУМЕКС",
                web_app=MAX_BOT_USERNAME,
                payload=RUMEX_DISPATCHER_APP_PAYLOAD,
            )
        )
    if "rumex_accountant" in roles:
        kb.row(
            OpenAppButton(
                text="📋 Кабинет бухгалтера",
                web_app=MAX_BOT_USERNAME,
                payload=ACCOUNTANT_APP_PAYLOAD,
            )
        )
    if has_private_menu_access(user_id):
        kb.row(
            OpenAppButton(
                text="💬 OMEGA Chat",
                web_app=MAX_BOT_USERNAME,
                payload=OMEGA_CHAT_PAYLOAD,
            )
        )
        kb.row(CallbackButton(text="Очистить память", payload=CB_CLEAR))
    if "omega_admin" in roles:
        kb.row(
            OpenAppButton(
                text="🛡️ РУМЕКС · управление",
                web_app=MAX_BOT_USERNAME,
                payload=RUMEX_MANAGEMENT_APP_PAYLOAD,
            )
        )
        kb.row(
            OpenAppButton(
                text="⚙️ Админ",
                web_app=MAX_BOT_USERNAME,
                payload=ADMIN_APP_PAYLOAD,
            )
        )
    return kb


def back_menu_keyboard() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(CallbackButton(text="← Меню", payload=CB_MENU))
    return kb


def comments_button_label(count: int) -> str:
    if count <= 0:
        return "💬 Комментарии"
    return f"💬 Комментарии · {count}"


def taksimo_find_attachments() -> list:
    kb = InlineKeyboardBuilder()
    kb.row(
        OpenAppButton(
            text="🔍 Где плита",
            web_app=MAX_BOT_USERNAME,
            payload=TAKSIMO_FIND_PAYLOAD,
        )
    )
    return [kb.as_markup()]


def taksimo_menu_attachments() -> list:
    return taksimo_find_attachments()


def accountant_open_app_attachments() -> list:
    kb = InlineKeyboardBuilder()
    kb.row(
        OpenAppButton(
            text="📋 Кабинет бухгалтера",
            web_app=MAX_BOT_USERNAME,
            payload=ACCOUNTANT_APP_PAYLOAD,
        )
    )
    return [kb.as_markup()]


def admin_open_app_attachments() -> list:
    kb = InlineKeyboardBuilder()
    kb.row(
        OpenAppButton(
            text="⚙️ Админ",
            web_app=MAX_BOT_USERNAME,
            payload=ADMIN_APP_PAYLOAD,
        )
    )
    return [kb.as_markup()]


def comments_keyboard(post_id: str, count: int = 0) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(
        OpenAppButton(
            text=comments_button_label(count),
            web_app=MAX_BOT_USERNAME,
            payload=encode_post_id(post_id),
        )
    )
    return kb
