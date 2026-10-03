#!/usr/bin/env python3
"""Root-only выпуск отдельной парольной учётной записи управления РУМЕКС.

Пароль запрашивается интерактивно и не передаётся в аргументах командной строки,
файлах конфигурации или журнале.
"""

from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
BOT = ROOT / "fotonych-bot"
sys.path.insert(0, str(BOT))

from rumex_admin_password import hash_browser_password  # noqa: E402
from rumex_registry_store import provision_rumex_admin_account  # noqa: E402


def main() -> int:
    if os.geteuid() != 0:
        raise SystemExit("Утилиту нужно запускать от root.")

    parser = argparse.ArgumentParser(
        description="Создать или перевыпустить парольный вход управления РУМЕКС"
    )
    parser.add_argument("username", help="Логин администратора")
    parser.add_argument("full_name", help="ФИО администратора")
    parser.add_argument("max_user_id", type=int, help="Связанный MAX ID администратора")
    args = parser.parse_args()

    first = getpass.getpass("Новый пароль (12–256 символов): ")
    second = getpass.getpass("Повторите пароль: ")
    if first != second:
        raise SystemExit("Пароли не совпадают.")

    try:
        provision_rumex_admin_account(
            username=args.username,
            full_name=args.full_name,
            max_user_id=args.max_user_id,
            password_hash=hash_browser_password(first),
            actor_name="Технический владелец",
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    print("Учётная запись сохранена. Пароль не выводился и не был записан в журнал.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
