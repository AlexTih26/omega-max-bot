#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${1:-/root/backups/max-bot-env}"
mkdir -p "$DEST"
STAMP="$(date +%Y-%m-%d_%H-%M-%S)"
if [[ -f "$ROOT/.env" ]]; then
  cp "$ROOT/.env" "$DEST/.env-$STAMP"
  echo "Saved $DEST/.env-$STAMP"
else
  echo "No .env found" >&2
  exit 1
fi
