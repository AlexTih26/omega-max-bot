#!/usr/bin/env bash
# Сторож Таксimo: проверка stats + перезапуск при сбое.
# Режимы: nightly (плановый рестарт), day (только при ошибке).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$ROOT/data/watchdog.log"
COOLDOWN="$ROOT/data/watchdog-cooldown"
COOLDOWN_SEC="${TAKSIMO_WATCHDOG_COOLDOWN_SEC:-900}"
PY="$ROOT/fotonych-bot/.venv/bin/python"
CHECK="$ROOT/scripts/check-taksimo-api.py"
RESTART="$ROOT/scripts/restart-bot.sh"
NOTIFY="$ROOT/scripts/watchdog-notify.py"

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S %Z') [$1] $2" | tee -a "$LOG"
}

check_api() {
  "$PY" "$CHECK" 2>&1
}

notify() {
  if [[ -x "$PY" && -f "$NOTIFY" ]]; then
    "$PY" "$NOTIFY" "$1" 2>/dev/null || true
  fi
}

cooldown_active() {
  [[ -f "$COOLDOWN" ]] || return 1
  local last now
  last=$(cat "$COOLDOWN" 2>/dev/null || echo 0)
  now=$(date +%s)
  (( now - last < COOLDOWN_SEC ))
}

mark_restart() {
  date +%s > "$COOLDOWN"
}

do_restart() {
  log "$MODE" "перезапуск bot.py…"
  bash "$RESTART" >> "$LOG" 2>&1 || true
  sleep 3
}

MODE="${1:-day}"

case "$MODE" in
  nightly)
    log nightly "плановый ночной перезапуск (03:05 MSK)"
    do_restart
    if check_api; then
      log nightly "после рестарта: OK"
    else
      log nightly "после рестарта: ошибка, повторный рестарт"
      do_restart
      if check_api; then
        log nightly "повторный рестарт: OK"
      else
        log nightly "КРИТИЧНО: stats не отвечает после 2 рестартов"
        notify "⚠️ Таксimo: ночной рестарт — stats не отвечает после 2 попыток. Нужна ручная проверка сервера."
      fi
    fi
    ;;
  day)
    if check_api >/dev/null; then
      exit 0
    fi
    log day "stats недоступен"
    if cooldown_active; then
      log day "cooldown ${COOLDOWN_SEC}s — рестарт пропущен"
      exit 0
    fi
    do_restart
    mark_restart
    if check_api; then
      log day "рестарт помог: OK"
      notify "ℹ️ Таксimo: был сбой БД/API, автоперезапуск помог. Работа восстановлена."
    else
      log day "КРИТИЧНО: рестарт не помог"
      notify "⚠️ Таксimo: сбой API/БД, автоперезапуск не помог. Проверьте сервер или restart-bot.sh вручную."
    fi
    ;;
  *)
    echo "usage: $0 nightly|day" >&2
    exit 2
    ;;
esac
