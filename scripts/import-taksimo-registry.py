#!/usr/bin/env python3
"""Разовый импорт реестра Таксимо. Перед импортом — бэкап и очистка тестовых данных."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "fotonych-bot"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from taksimo_backup import backup_taksimo_db  # noqa: E402
from taksimo_import import import_registry_xlsx  # noqa: E402
from taksimo_store import DB_PATH, _connect, init_taksimo_db  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Импорт реестра Таксимо из Excel")
    parser.add_argument(
        "--file",
        type=Path,
        default=ROOT / "docs" / "registry" / "Реестр (1).xlsx",
        help="Путь к xlsx",
    )
    parser.add_argument(
        "--no-wipe",
        action="store_true",
        help="Не очищать БД перед импортом",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать план, без записи",
    )
    args = parser.parse_args()

    if not args.file.is_file():
        print(f"Файл не найден: {args.file}", file=sys.stderr)
        return 1

    init_taksimo_db()
    backup = backup_taksimo_db(reason="pre-import")
    print(f"Бэкап: {backup}")

    if args.dry_run:
        rows = __import__("taksimo_import", fromlist=["_read_xlsx_rows"])._read_xlsx_rows(args.file)
        groups = __import__("taksimo_import", fromlist=["_group_sessions"])._group_sessions(rows[1:])
        print(f"Групп выгрузок: {len(groups)}, строк данных: {len(rows) - 1}")
        return 0

    stats = import_registry_xlsx(args.file, wipe_first=not args.no_wipe)
    print("Импорт завершён:")
    for k, v in stats.items():
        print(f"  {k}: {v}")

    with _connect() as conn:
        s = conn.execute("SELECT COUNT(*) FROM unload_sessions").fetchone()[0]
        sl = conn.execute("SELECT COUNT(*) FROM slabs").fetchone()[0]
        placed = conn.execute(
            "SELECT COUNT(*) FROM slabs WHERE pos_x > 0 AND pos_y > 0"
        ).fetchone()[0]
    print(f"Проверка БД {DB_PATH.name}: сессий={s}, плит={sl}, на плане={placed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
