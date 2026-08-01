#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/fotonych-bot"
fuser -k "${COMMENTS_PORT:-8765}/tcp" 2>/dev/null || true
sleep 1
nohup .venv/bin/python - <<'PY' >> bot.log 2>&1 &
from pathlib import Path
import asyncio
from dotenv import load_dotenv

load_dotenv(Path("/MAX_BOT") / ".env")

from comments_api import start_comments_api


async def main() -> None:
    await start_comments_api()
    print("Taksimo API started", flush=True)
    await asyncio.Event().wait()


asyncio.run(main())
PY
sleep 2
curl -sf "http://127.0.0.1:${COMMENTS_PORT:-8765}/api/health" && echo " — API OK" || echo "API не отвечает"
