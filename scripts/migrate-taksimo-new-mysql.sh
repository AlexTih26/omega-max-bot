#!/usr/bin/env bash
set -euo pipefail

# Применяет версионированные миграции только технической MySQL-учётной записью.
# Этот скрипт не запускается от имени веб-приложения.
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="/root/.config/taksimo-new/mysql-migrator.cnf"
DATABASE="taksimo_new"
MIGRATIONS="$ROOT/migrations/taksimo_new"

if [[ ! -r "$CONFIG" ]]; then
    echo "Не найден закрытый конфигурационный файл мигратора: $CONFIG" >&2
    exit 1
fi

mysql_cmd=(mysql --defaults-extra-file="$CONFIG" --database="$DATABASE")
root_mysql_cmd=(mysql --protocol=socket --user=root --database="$DATABASE")
"${mysql_cmd[@]}" <<'SQL'
CREATE TABLE IF NOT EXISTS tn_schema_migrations (
    version VARCHAR(100) NOT NULL PRIMARY KEY,
    applied_at DATETIME(6) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
SQL

shopt -s nullglob
for migration in "$MIGRATIONS"/*.sql; do
    version="$(basename "$migration" .sql)"
    applied="$("${mysql_cmd[@]}" --batch --skip-column-names \
        -e "SELECT version FROM tn_schema_migrations WHERE version = '$version'" || true)"
    if [[ "$applied" == "$version" ]]; then
        echo "Уже применена: $version"
        continue
    fi
    echo "Применяется: $version"
    if [[ "$version" == *_triggers ]]; then
        # Бинарное логирование включено глобально. Привилегия нужна только
        # локальному root-сеансу на время создания триггеров, не приложению.
        "${root_mysql_cmd[@]}" --init-command="SET SESSION sql_log_bin = 0" < "$migration"
    else
        "${mysql_cmd[@]}" < "$migration"
    fi
    "${mysql_cmd[@]}" -e "INSERT INTO tn_schema_migrations (version, applied_at) VALUES ('$version', UTC_TIMESTAMP(6))"
done
