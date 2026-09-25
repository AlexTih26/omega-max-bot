#!/usr/bin/env python3
"""Техническая утилита учётных записей нового контура Таксимо.

Не публикует PIN и не является HTTP-интерфейсом. Запускается владельцем
системы на сервере после явной передачи оператором нового PIN.
"""

from __future__ import annotations

import argparse
import getpass
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
BOT = ROOT / "fotonych-bot"
sys.path.insert(0, str(BOT))

from taksimo_new_auth import hash_pin  # noqa: E402
import taksimo_new_store as store  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Настройка трёх PIN новой Таксимо")
    parser.add_argument("role", choices=("operator1", "operator2", "operator3"))
    parser.add_argument("display_name", help="Отображаемое имя оператора")
    parser.add_argument("--disable", action="store_true", help="Отключить учётную запись")
    args = parser.parse_args()

    if args.disable:
        store.set_operator_account(args.role, args.display_name, pin_hash=None, active=False)
        print("Учётная запись отключена.")
        return 0

    first = getpass.getpass("Новый PIN (6–12 цифр): ")
    second = getpass.getpass("Повторите PIN: ")
    if first != second:
        raise SystemExit("PIN не совпадают")
    store.set_operator_account(args.role, args.display_name, pin_hash=hash_pin(first), active=True)
    print("Учётная запись сохранена. PIN на экран и в журнал не выводился.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
