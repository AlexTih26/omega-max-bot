# Гид rollout FastAPI-контура Таксимо

## 1. Назначение и текущий статус

Этот документ — актуальный operational guide для отдельного FastAPI-контура
Таксимо. Он фиксирует результат production-preflight и порядок последующих
действий. В нём нет паролей, токенов, содержимого `EnvironmentFile` или
команд, выводящих секреты.

Исходный FastAPI-код опубликован в коммите
`1ffeb42 feat(api): add CSRF origin gate, health/readiness, cryptography.`.
В нём есть строгий Origin gate, `GET /api/v1/healthz`,
`GET /api/v1/readyz`, а `cryptography` закреплена в
`requirements-fastapi.txt`.

**Шаг 2 — production-preflight выполнен. Deployment не выполнялся.**
FastAPI ещё не запущен, Nginx не изменён, а live API продолжает обслуживаться
legacy `bot.py` через порт `8765`. Внешний
`POST /api/v1/dispatcher/trips` до отдельного routing-change возвращает
ожидаемый `404`.

## 2. Неизменяемые границы

До отдельных разрешений запрещено:

- менять `fotonych-bot/bot.py`;
- менять существующую Nginx-маршрутизацию на `8765`;
- менять `max.avtmsk.ru`;
- создавать production-записи для E2E;
- удалять данные, делать ручной DDL или переписывать историю миграций;
- запускать migrations повторно: production-записи `014–016` уже применены;
- добавлять в Git секреты, `EnvironmentFile`, backup, restore-дампы или
  runtime-состояние.

Чужой `scripts/check-taksimo-new-sandbox.sh` не является частью FastAPI
изменения: его нельзя изменять, индексировать или коммитить.

## 3. Зафиксированный production-preflight

### 3.1. Backup и проверка восстановления

Создан полный логический backup `taksimo_new` вне `/MAX_BOT`:

```text
/root/backups/taksimo-new-preflight/taksimo-new-production-preflight-2026-10-15T13-35-41Z.sql.gz
размер: 1,134,990 bytes
SHA-256: 7b392d695fc765f132017f0992e2e706ab47e3db26220b5fbd4a276153350242
```

Рядом размещён файл `.sha256`. Успешно подтверждены `gzip -t` и
`sha256sum --check`. Права архива и отчёта — `0600 root:root`.

Архив восстановлен в новую отдельную базу
`taksimo_new_preflight_restore_20261014`, без перезаписи либо очистки
существующих stage3 и предыдущих restore-баз. В production и восстановленной
базе по 48 базовых таблиц, включая `tn_command_idempotency`.

Read-only сверка прошла **13/13**:

1. список `tn_*` базовых таблиц;
2. engine и collation таблиц;
3. журнал миграций;
4. inventory ограничений;
5. правила внешних ключей;
6. выражения `CHECK`;
7. определения триггеров;
8. столбцы `tn_command_idempotency`;
9. уникальная область ключа идемпотентности;
10. `CHECK` идемпотентности;
11. FK idempotency на account;
12. новые поля, индекс и FK `tn_action_log`;
13. все восемь append-only/transition-триггеров `016`.

Не содержащий секретов отчёт находится рядом с архивом:

```text
/root/backups/taksimo-new-preflight/taksimo-new-production-preflight-2026-10-15T13-35-41Z.restore-check.txt
```

Перед любым последующим production-routing change нужно создать новый свежий
backup и повторить как минимум проверку архива и SHA-256. Этот архив остаётся
контрольной точкой preflight, а не заменой свежего предизменительного backup.

### 3.2. MySQL-принцип наименьших привилегий

Создан отдельный пользователь только для будущего FastAPI-процесса:

```text
taksimo_new_fastapi@127.0.0.1
authentication plugin: caching_sha2_password
```

Ему выданы только следующие table-level grants на `taksimo_new`:

| Операция | Таблицы |
| --- | --- |
| `SELECT` | `tn_operator_sessions`, `tn_operator_accounts`, `tn_roles`, `tn_role_permissions`, `tn_account_permissions`, `tn_vehicles`, `tn_carriers`, `tn_drivers`, `tn_block_types`, `tn_block_registry`, `tn_trips`, `tn_command_idempotency` |
| `INSERT` | `tn_command_idempotency`, `tn_trips`, `tn_block_registry`, `tn_trip_blocks`, `tn_block_history`, `tn_action_log` |
| `UPDATE` | только `tn_command_idempotency` |

Подтверждено отдельными read-only проверками:

- точная матрица table-level grants совпадает с таблицей выше;
- schema-wide grants — `0`; унаследованные роли — `0`;
- нет `DELETE`, DDL, `CREATE TRIGGER`, `GRANT OPTION`, `GRANT ALL` или
  `taksimo_new.*`;
- видны только 15 разрешённых прикладных таблиц, `information_schema` и
  `performance_schema`;
- доступ к `tn_schema_migrations`, stage3 и отдельной restore-базе отклонён;
- TCP-подключение и реальный `AsyncMySessionRepository.check_ready()` через
  `asyncmy` успешно прошли с `caching_sha2_password`.

### 3.3. EnvironmentFile

Создан закрытый файл будущего сервиса:

```text
/etc/taksimo-new/fastapi.env
mode: 0640
owner: root
group: taksimo-fastapi
```

В нём ровно шесть ожидаемых ключей: общий
`TAKSIMO_NEW_AUTH_SECRET` и новые параметры подключения FastAPI к MySQL.
Общий auth-secret побайтно сверён с legacy-конфигурацией без вывода значений.
Пароль пользователя FastAPI отдельный, ни в Git, ни в журналах не находится.

## 4. Шаг 3 — запуск без внешнего трафика

Выполняется **только после отдельного разрешения**.

1. Создать отдельный systemd unit от непривилегированного пользователя и группы
   `taksimo-fastapi`; единственный источник закрытой конфигурации —
   `/etc/taksimo-new/fastapi.env`.
2. Запустить Uvicorn отдельным процессом, привязанным исключительно к
   `127.0.0.1:8000`; не использовать `0.0.0.0` и не встраивать FastAPI в
   `bot.py`.
3. Для reverse proxy доверять forwarded-заголовкам только от loopback:
   `--proxy-headers --forwarded-allow-ips=127.0.0.1,::1`.
4. До изменения Nginx проверить локально:
   - процесс жив и systemd сообщает `active`;
   - `GET http://127.0.0.1:8000/api/v1/healthz` возвращает `200`;
   - `GET http://127.0.0.1:8000/api/v1/readyz` возвращает `200`;
   - порт не доступен извне;
   - не выполняется создание рейсов и не создаются production-записи.
5. При любой ошибке остановить только новый FastAPI-unit и не менять legacy
   контур.

## 5. Шаг 4 — Nginx routing

Выполняется **только после отдельного разрешения** и только после успешного
локального шага 3.

На `avtmsk.ru` направить `/api/v1/*` на `127.0.0.1:8000`. При этом сохранить:

- `/api/taksimo/*` на legacy-контуре;
- остаточные legacy `/api/*` на `127.0.0.1:8765`;
- Nginx-конфигурацию и маршрутизацию `max.avtmsk.ru` без изменений.

Перед reload выполнить `nginx -t`; после reload проверить `/healthz` и
`/readyz` через публичный origin. Существующий listener `8765` не
переназначать и не перезапускать.

## 6. Шаг 5 — production E2E

Выполняется **только с отдельным разрешением на создание production-записи**.
До него достаточны liveness/readiness и проверки конфигурации.

Если разрешение дано, тест должен использовать утверждённую тестовую учётную
запись и валидные справочные данные; зафиксировать только безопасные
идентификаторы и HTTP-статусы. Проверить последовательность:

1. первый `POST` с `Origin: https://avtmsk.ru` и новым `Idempotency-Key` —
   `201`;
2. точный повтор — `200` и неизменное тело;
3. тот же ключ с иным нормализованным телом — `409`;
4. отсутствующий или иной Origin — `403` до обращения к сессии/БД.

Не удалять созданный факт для «очистки»: рейс, аудит и idempotency являются
append-only историей.

## 7. Мониторинг и откат

После routing-change контролировать `healthz`, `readyz`, HTTP-коды, ошибки
подключения MySQL и логи FastAPI без записи секретов.

При проблеме маршрутизации откатить только Nginx-маршрут `/api/v1/*` к прежнему
поведению либо остановить новый FastAPI-unit. Не удалять записи аудита, рейсы,
идемпотентность или миграции. Восстановление MySQL допустимо только как
отдельное аварийное решение из проверенного полного backup, а не ручным DDL/DML.
