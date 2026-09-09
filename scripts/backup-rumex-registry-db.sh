#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$ROOT/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
  PYTHON="python3"
fi

"$PYTHON" - <<PY
import sys
from pathlib import Path

root = Path(${ROOT@Q})
sys.path.insert(0, str(root / "fotonych-bot"))
from rumex_registry_backup import backup_rumex_registry_db

print(backup_rumex_registry_db(reason="cron") or "no db")
PY
