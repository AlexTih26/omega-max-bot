"""Временные базовые права текущих ролей до миграции `016`."""

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

# Миграция 014 подготовила таблицы role_permissions/account_permissions,
# но текущий runtime сохраняет временное сопоставление до миграции 016.
# Единственная подтверждённая capability нового контура не читает новый SQL-контракт.
TEMPORARY_CAPABILITY_SECTIONS: Mapping[tuple[str, str], str] = {
    ("trip.create", "edit"): "trips",
}


def effective_permissions(role: str) -> dict[str, bool]:
    """Вернуть временные права роли без учётных overrides до миграции `016`."""
    return {section: role in allowed_roles for section, allowed_roles in SECTION_ROLES.items()}


def is_permission_granted(role: str, capability: str, level: str) -> bool:
    """Проверить временное capability-право по существующей матрице разделов."""
    section = TEMPORARY_CAPABILITY_SECTIONS.get((capability, level))
    return section is not None and effective_permissions(role).get(section, False)


def is_supported_permission(capability: str, level: str) -> bool:
    """Проверить, что capability имеет подтверждённое переходное сопоставление."""
    return (capability, level) in TEMPORARY_CAPABILITY_SECTIONS
