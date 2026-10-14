"""Проверки аргументов MySQL-runner-а без подключения к MySQL."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "scripts" / "migrate-taksimo-new-mysql.sh"
MIGRATIONS = sorted(path.stem for path in (ROOT / "migrations" / "taksimo_new").glob("*.sql"))


class MigrateRunnerArgumentsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self._temporary_path = Path(self._temporary_directory.name)
        self._config = self._temporary_path / "mysql-migrator.cnf"
        self._config.write_text("[client]\n", encoding="utf-8")
        self._state = self._temporary_path / "applied-versions.txt"
        self._log = self._temporary_path / "mysql-invocations.txt"
        self._fake_mysql = self._temporary_path / "mysql"
        self._fake_mysql.write_text(
            """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' \"$*\" >> \"$MYSQL_FAKE_LOG\"
query=\"\"
while (( $# > 0 )); do
    case \"$1\" in
        -e)
            query=\"$2\"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done
if [[ -n \"$query\" ]]; then
    if [[ \"$query\" == *\"SELECT version FROM tn_schema_migrations WHERE version = \"* ]]; then
        version=\"$(printf '%s' \"$query\" | sed -n \"s/.*WHERE version = '\\\\([^']*\\\\)'.*/\\\\1/p\")\"
        if grep -Fqx -- \"$version\" \"$MYSQL_FAKE_STATE\"; then
            printf '%s\\n' \"$version\"
        fi
    elif [[ \"$query\" == *\"INSERT INTO tn_schema_migrations\"* ]]; then
        version=\"$(printf '%s' \"$query\" | sed -n \"s/.*VALUES ('\\\\([^']*\\\\)'.*/\\\\1/p\")\"
        printf '%s\\n' \"$version\" >> \"$MYSQL_FAKE_STATE\"
    fi
else
    cat >/dev/null
fi
""",
            encoding="utf-8",
        )
        self._fake_mysql.chmod(0o755)

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def test_stop_after_014_applies_only_014_files(self) -> None:
        result = self._run("--stop-after=014", applied_through=13)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._applied_versions(result.stdout), ["014_accounts_and_permissions"])
        self.assertIn("Предел достигнут: --stop-after=014.", result.stdout)

    def test_stop_after_015_skips_014_and_applies_015_files(self) -> None:
        result = self._run("--stop-after=015", applied_through=14)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Уже применена: 014_accounts_and_permissions", result.stdout)
        self.assertEqual(self._applied_versions(result.stdout), ["015_vehicle_queue_and_trip_states"])

    def test_stop_after_016_applies_both_016_files_in_lexicographical_order(self) -> None:
        result = self._run("--stop-after=016", applied_through=15)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self._applied_versions(result.stdout),
            ["016_audit_and_idempotency", "016_audit_and_idempotency_triggers"],
        )
        self.assertIn("Предел достигнут: --stop-after=016.", result.stdout)

    def test_stop_after_013_applies_nothing_when_013_is_current(self) -> None:
        result = self._run("--stop-after=013", applied_through=13)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._applied_versions(result.stdout), [])
        self.assertIn("Предел достигнут: --stop-after=013.", result.stdout)

    def test_rejects_non_numeric_stop_after_before_mysql_connection(self) -> None:
        result = self._run("--stop-after=abc", applied_through=13)

        self.assertEqual(result.returncode, 2)
        self.assertIn("ровно три цифры", result.stderr)
        self.assertEqual(self._mysql_invocations(), [])

    def test_rejects_stop_after_without_three_digits_before_mysql_connection(self) -> None:
        result = self._run("--stop-after=14", applied_through=13)

        self.assertEqual(result.returncode, 2)
        self.assertIn("ровно три цифры", result.stderr)
        self.assertEqual(self._mysql_invocations(), [])

    def test_rejects_unknown_argument_before_mysql_connection(self) -> None:
        result = self._run("--unknown", applied_through=13)

        self.assertEqual(result.returncode, 2)
        self.assertIn("Неизвестный аргумент: --unknown", result.stderr)
        self.assertEqual(self._mysql_invocations(), [])

    def test_rejects_stop_after_without_a_matching_migration_before_mysql_connection(self) -> None:
        result = self._run("--stop-after=017", applied_through=13)

        self.assertEqual(result.returncode, 2)
        self.assertIn("Не найдены миграции с префиксом 017", result.stderr)
        self.assertEqual(self._mysql_invocations(), [])

    def test_without_parameter_applies_all_missing_migrations(self) -> None:
        result = self._run(applied_through=0)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._applied_versions(result.stdout), MIGRATIONS)
        self.assertNotIn("Предел достигнут", result.stdout)

    def _run(self, *arguments: str, applied_through: int) -> subprocess.CompletedProcess[str]:
        self._state.write_text(
            "\n".join(version for version in MIGRATIONS if int(version[:3]) <= applied_through) + "\n",
            encoding="utf-8",
        )
        self._log.write_text("", encoding="utf-8")
        environment = os.environ | {
            "PATH": f"{self._temporary_path}{os.pathsep}{os.environ['PATH']}",
            "MYSQL_FAKE_LOG": str(self._log),
            "MYSQL_FAKE_STATE": str(self._state),
            "TAKSIMO_NEW_MYSQL_MIGRATOR_CONFIG": str(self._config),
        }
        return subprocess.run(
            ["bash", str(RUNNER), *arguments],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    @staticmethod
    def _applied_versions(output: str) -> list[str]:
        return re.findall(r"^Применяется: (.+)$", output, flags=re.MULTILINE)

    def _mysql_invocations(self) -> list[str]:
        return self._log.read_text(encoding="utf-8").splitlines()


if __name__ == "__main__":
    unittest.main()
