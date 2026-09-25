#!/usr/bin/env bash
set -euo pipefail

# Резервная копия нового изолированного контура Таксимо в MySQL.
# Пароль берётся из закрытого файла root, не из репозитория и не из окружения.
umask 077

CONFIG="/root/.config/taksimo-new/mysql-backup.cnf"
DESTINATION="${TAKSIMO_NEW_BACKUP_DIR:-/root/backups/taksimo-new-mysql}"
DATABASE="taksimo_new"
RETENTION_DAYS="${TAKSIMO_NEW_BACKUP_RETENTION_DAYS:-31}"

if [[ ! -r "$CONFIG" ]]; then
    echo "Не найден закрытый конфигурационный файл MySQL: $CONFIG" >&2
    exit 1
fi

mkdir -p "$DESTINATION"
STAMP="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
FINAL="$DESTINATION/taksimo-new-$STAMP.sql.gz"
TEMPORARY="$(mktemp "$DESTINATION/.taksimo-new-$STAMP.XXXXXX.sql.gz")"

cleanup() {
    rm -f "$TEMPORARY"
}
trap cleanup EXIT

mysqldump \
    --defaults-extra-file="$CONFIG" \
    --single-transaction \
    --routines \
    --events \
    --triggers \
    --no-tablespaces \
    --databases "$DATABASE" \
    | gzip -9 > "$TEMPORARY"

gzip -t "$TEMPORARY"
mv "$TEMPORARY" "$FINAL"
sha256sum "$FINAL" > "$FINAL.sha256"

find "$DESTINATION" -maxdepth 1 -type f \( -name 'taksimo-new-*.sql.gz' -o -name 'taksimo-new-*.sql.gz.sha256' \) \
    -mtime "+$RETENTION_DAYS" -delete

echo "Создана резервная копия MySQL: $FINAL"
