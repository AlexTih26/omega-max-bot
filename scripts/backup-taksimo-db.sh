#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/fotonych-bot"
.venv/bin/python -c "
from pathlib import Path
import sys
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv(Path('..') / '.env')
from taksimo_backup import backup_taksimo_db
p = backup_taksimo_db(reason='cron')
print(p or 'no db')
"
