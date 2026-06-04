#!/usr/bin/env bash
# Запустите ОДИН раз после создания пустого репозитория на GitHub.
# Использование:
#   ./scripts/push-to-github.sh YOUR_USERNAME YOUR_REPO_NAME
set -euo pipefail
if [[ $# -lt 2 ]]; then
  echo "Использование: $0 GITHUB_USER REPO_NAME"
  echo "Пример: $0 alexey omega-max-bot"
  exit 1
fi
USER="$1"
REPO="$2"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
if git remote get-url origin &>/dev/null; then
  echo "Remote origin уже есть: $(git remote get-url origin)"
else
  git remote add origin "https://github.com/${USER}/${REPO}.git"
  echo "Добавлен origin: https://github.com/${USER}/${REPO}.git"
fi
git push -u origin main
echo "Готово. Репозиторий: https://github.com/${USER}/${REPO}"
