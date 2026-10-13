"""Временные базовые права текущих ролей до миграции `014+`."""

from __future__ import annotations


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


def effective_permissions(role: str) -> dict[str, bool]:
    """Вернуть права текущей роли без учётных overrides из ещё не созданной `014+`."""
    return {section: role in allowed_roles for section, allowed_roles in SECTION_ROLES.items()}
