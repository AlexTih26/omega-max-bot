"""Проверки единого расчёта ролей и допуска к личному меню OMEGA."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BOT = Path(__file__).resolve().parents[1] / "fotonych-bot"
if str(BOT) not in sys.path:
    sys.path.insert(0, str(BOT))

import access_roles  # noqa: E402


class AccessRolesTests(unittest.TestCase):
    def test_resolves_roles_from_existing_sources(self):
        with (
            patch("access_roles._is_super_admin", return_value=True),
            patch("access_roles._stock_roles", return_value=["master", "unknown"]),
            patch("access_roles._is_rumex_dispatcher", return_value=True),
            patch("access_roles._is_rumex_accountant", return_value=True),
            patch("access_roles._is_driver", return_value=True),
            patch("access_roles._ipdocs_access", return_value={"allowed": True, "role": "contractor"}),
        ):
            roles = access_roles.roles_for_max_user(101)

        self.assertEqual(
            roles,
            frozenset(
                {
                    "omega_admin",
                    "stock_master",
                    "rumex_dispatcher",
                    "rumex_accountant",
                    "driver",
                    "ipdocs_contractor",
                }
            ),
        )

    def test_driver_or_disabled_ipdocs_role_does_not_open_private_menu(self):
        with patch("access_roles.roles_for_max_user", return_value=frozenset({"driver"})):
            self.assertTrue(access_roles.has_service_access(101))
            self.assertFalse(access_roles.has_private_menu_access(101))

        with patch("access_roles.roles_for_max_user", return_value=frozenset({"ipdocs_contractor"})):
            self.assertTrue(access_roles.has_service_access(101))
            self.assertFalse(access_roles.has_private_menu_access(101))

    def test_stock_role_opens_private_menu(self):
        with patch("access_roles.roles_for_max_user", return_value=frozenset({"stock_supply"})):
            self.assertTrue(access_roles.has_private_menu_access(101))


if __name__ == "__main__":
    unittest.main()
