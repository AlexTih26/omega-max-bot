#!/usr/bin/env python3
"""Проверка, что бот отвечает и читает БД Таксimo (GET /api/taksimo/stats)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "fotonych-bot"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from taksimo_auth import create_token, load_users  # noqa: E402

API = os.getenv("TAKSIMO_WATCHDOG_URL", "http://127.0.0.1:8765/api/taksimo/stats")
TIMEOUT = float(os.getenv("TAKSIMO_WATCHDOG_TIMEOUT", "15"))


def _pick_user() -> str:
    users = load_users()
    if not users:
        return "оператор"
    for name in users:
        if name == "оператор1":
            return name
    return next(iter(users))


def main() -> int:
    token = create_token(_pick_user())
    try:
        proc = subprocess.run(
            [
                "curl",
                "-sfS",
                "--max-time",
                str(int(TIMEOUT)),
                "-H",
                f"Cookie: taksimo_auth={token}",
                API,
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "curl failed").strip()
            print(err[:300])
            return 1
        body = proc.stdout
        data = json.loads(body)
        if "on_yard" not in data:
            print(f"unexpected JSON: {body[:200]}")
            return 1
        print(
            f"ok on_yard={data.get('on_yard')} "
            f"on_wagon={data.get('on_wagon')} "
            f"at_bts={data.get('at_bts_vostok')}"
        )
        return 0
    except json.JSONDecodeError as e:
        print(f"invalid JSON: {e}")
        return 1
    except Exception as e:
        print(f"error: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
