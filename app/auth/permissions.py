"""Совместимая UI-матрица и подтверждённые capability нового контура."""

from __future__ import annotations

from collections.abc import Mapping


SECTION_ROLES: dict[str, frozenset[str]] = {
    "dashboard": frozenset({
        "admin", "dispatcher", "accountant1", "accountant2", "accountant3",
        "operator1", "operator2", "operator3",
    }),
    "reception": frozenset({"operator1", "operator2", "operator3"}),
    "yard": frozenset({"dispatcher", "operator1", "operator2", "operator3"}),
    "wagons": frozenset({"dispatcher", "operator1", "operator2", "operator3"}),
    "journal": frozenset({
        "admin", "dispatcher", "accountant1", "accountant2", "accountant3",
        "operator1", "operator2", "operator3",
    }),
    "reports": frozenset({
        "admin", "dispatcher", "accountant1", "accountant2", "accountant3",
        "operator1", "operator2", "operator3",
    }),
    "search": frozenset({
        "admin", "dispatcher", "accountant1", "accountant2", "accountant3",
        "operator1", "operator2", "operator3",
    }),
    "corrections": frozenset({"operator1", "operator2", "operator3"}),
    "trips": frozenset({"admin", "dispatcher"}),
    "receipts": frozenset({"admin", "accountant1", "accountant2", "accountant3"}),
    "admin": frozenset({"admin"}),
}

# Этот перечень сохраняется только для отображения legacy-разделов в /auth/me.
# Серверные capability после подтверждения 016 читаются из role_permissions и
# account_permissions, поэтому этот словарь не участвует в авторизации маршрутов.
SUPPORTED_CAPABILITY_LEVELS = frozenset({
    ("trip.create", "edit"),
})

PERMISSION_COLUMNS: Mapping[str, str] = {
    "view": "can_view",
    "edit": "can_edit",
    "print": "can_print",
}


def effective_permissions(role: str) -> dict[str, bool]:
    """Вернуть переходный UI-снимок разделов без account-level overrides."""
    return {section: role in allowed_roles for section, allowed_roles in SECTION_ROLES.items()}


def is_supported_permission(capability: str, level: str) -> bool:
    """Проверить, что capability и уровень утверждены SQL-контрактом `014`."""
    return (capability, level) in SUPPORTED_CAPABILITY_LEVELS and level in PERMISSION_COLUMNS


def permission_column(level: str) -> str:
    """Вернуть имя безопасного поля снимка права для заданного уровня."""
    return PERMISSION_COLUMNS[level]
