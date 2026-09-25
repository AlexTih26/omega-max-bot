"""Единый расчёт MAX-ролей для видимости служебного меню.

Этот модуль не заменяет авторизацию приложений. Он отвечает только на вопрос,
какие пункты можно показать пользователю MAX. Каждый API и веб-кабинет
продолжает самостоятельно проверять PIN, пароль, сессию и права на действие.
"""

from __future__ import annotations

STOCK_ROLES = frozenset({"master", "supply", "manager", "admin"})
PRIVATE_MENU_ROLE_PREFIXES = ("omega_admin", "stock_", "rumex_")


def _is_super_admin(user_id: int) -> bool:
    from super_admin import is_super_admin

    return is_super_admin(user_id)


def _stock_roles(user_id: int) -> list[str]:
    from sklad_master_store import get_access

    return get_access(user_id).get("roles", [])


def _is_rumex_dispatcher(user_id: int) -> bool:
    from rumex_chat import is_rumex_dispatcher

    return is_rumex_dispatcher(user_id)


def _is_rumex_accountant(user_id: int) -> bool:
    from rumex_loading import is_rumex_accountant

    return is_rumex_accountant(user_id)


def _is_driver(user_id: int) -> bool:
    from drivers_chat import _driver_record, _load_state

    return bool(_driver_record(_load_state(), user_id))


def _ipdocs_access(user_id: int) -> dict:
    from ipdocs_store import resolve_access

    return resolve_access(user_id)


def roles_for_max_user(user_id: int | None) -> frozenset[str]:
    """Вернуть все служебные роли MAX-пользователя из действующих источников."""
    if user_id is None:
        return frozenset()

    roles: set[str] = set()
    if _is_super_admin(user_id):
        roles.add("omega_admin")

    roles.update(
        "stock_" + role
        for role in _stock_roles(user_id)
        if role in STOCK_ROLES
    )

    if _is_rumex_dispatcher(user_id):
        roles.add("rumex_dispatcher")
    if _is_rumex_accountant(user_id):
        roles.add("rumex_accountant")
    if _is_driver(user_id):
        roles.add("driver")

    ipdocs_access = _ipdocs_access(user_id)
    if ipdocs_access.get("allowed"):
        roles.add("ipdocs_" + str(ipdocs_access.get("role") or "contractor"))

    return frozenset(roles)


def has_service_access(user_id: int | None) -> bool:
    """Допущен ли пользователь хотя бы к одному служебному разделу."""
    return bool(roles_for_max_user(user_id))


def has_private_menu_access(user_id: int | None) -> bool:
    """Можно ли показать личное меню и OMEGA Chat.

    Водительская панель доступна только в чате водителей, а раздел документов
    ИП временно отключён. Эти роли не открывают личное меню бота.
    """
    return any(
        role.startswith(PRIVATE_MENU_ROLE_PREFIXES)
        for role in roles_for_max_user(user_id)
    )


def has_stock_access(roles: frozenset[str]) -> bool:
    return any(role.startswith("stock_") for role in roles)
