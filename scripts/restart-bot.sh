#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/fotonych-bot"
pkill -f "fotonych-bot/.venv/bin/python bot.py" 2>/dev/null || true
sleep 1
nohup .venv/bin/python bot.py >> bot.log 2>&1 &
sleep 2
curl -sf http://127.0.0.1:8765/api/health && echo " — API OK" || echo "API не отвечает"
