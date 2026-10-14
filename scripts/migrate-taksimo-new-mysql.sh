#!/usr/bin/env bash
set -euo pipefail

# Применяет версионированные миграции только технической MySQL-учётной записью.
# Этот скрипт не запускается от имени веб-приложения.
umask 077
export LC_ALL=C

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="${TAKSIMO_NEW_MYSQL_MIGRATOR_CONFIG:-/root/.config/taksimo-new/mysql-migrator.cnf}"
DATABASE="taksimo_new"
MIGRATIONS="$ROOT/migrations/taksimo_new"
stop_after=""

usage() {
    echo "Использование: $0 [--stop-after=NNN]" >&2
}

if (( $# > 1 )); then
    usage
    exit 2
fi
if (( $# == 1 )); then
    case "$1" in
        --stop-after=*)
            stop_after="${1#--stop-after=}"
            if [[ ! "$stop_after" =~ ^[0-9]{3}$ ]]; then
                echo "Параметр --stop-after должен содержать ровно три цифры" >&2
                exit 2
            fi
            ;;
        *)
            echo "Неизвестный аргумент: $1" >&2
            usage
            exit 2
            ;;
    esac
fi

shopt -s nullglob
if [[ -n "$stop_after" ]]; then
    matching_migrations=("$MIGRATIONS"/"${stop_after}"_*.sql)
    if (( ${#matching_migrations[@]} == 0 )); then
        echo "Не найдены миграции с префиксом $stop_after в $MIGRATIONS" >&2
        exit 2
    fi
fi

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

for migration in "$MIGRATIONS"/*.sql; do
    version="$(basename "$migration" .sql)"
    if [[ ! "$version" =~ ^([0-9]{3})_ ]]; then
        echo "Некорректное имя миграции: $(basename "$migration")" >&2
        exit 2
    fi
    migration_prefix="${BASH_REMATCH[1]}"
    if [[ -n "$stop_after" ]] && (( 10#$migration_prefix > 10#$stop_after )); then
        break
    fi

    applied="$("${mysql_cmd[@]}" --batch --skip-column-names         -e "SELECT version FROM tn_schema_migrations WHERE version = '$version'" || true)"
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

if [[ -n "$stop_after" ]]; then
    echo "Предел достигнут: --stop-after=$stop_after."
fi
