---
title: "Новая Таксимо: роли, справочники админа, журнал действий"
---

# Новая Таксимо: роли, справочники администратора, журнал (Этап 1)

Ветка `kodik/new-taksimo-integration`. Коммиты: `5acc49f` (миграции 009/010),
`5924ab8` (store + API справочников).

## Роли

Одна таблица `tn_operator_accounts`. Роли (миграция `010`, CHECK):
`admin`, `dispatcher`, `accountant1..3`, `operator1..3` — 8 учёток, 4 роли.

В `fotonych-bot/taksimo_new_store.py`:
- `ROLE_CODES` — кортеж всех девяти кодов;
- `OPERATOR_ROLES`, `ACCOUNTANT_ROLES`, `ADMIN_ROLES` — наборы для проверок;
- `operator_accounts_ready()` возвращает `OPERATOR_ROLES.issubset(roles)`
  (раньше требовалось **ровно** три роли — с появлением админа вход ломался);
- `_require_role(operator, allowed, label)` — бросает `PermissionError`.

## Справочники (миграция `010`, таблицы `tn_*`)

`tn_block_types` (A=7780 B=7900 C=8080 D=8080 E=7900 F=7780 K=3850),
`tn_block_suffixes`, `tn_wagon_schemes` (scheme1=9, scheme2=16 K, scheme3=8),
`tn_carriers`, `tn_drivers`, `tn_vehicles`, `tn_wagon_fleet`, `tn_dead_ends`
(`gruzovoy`/`turan`/`third` по 10 мест), `tn_yard_slots` (13×25, максимум 4),
`tn_action_log`.

Ключевой принцип: справочник = значения по умолчанию, история хранит снимок.
Правка справочника не переписывает прошлое.

## Store API (taksimo_new_store.py, ~2240+)

Чтение: `catalogs()`, `block_types()`, `block_suffixes()`, `wagon_schemes()`,
`carriers()`, `drivers()`, `vehicles()`, `wagon_fleet()`, `dead_ends()`,
`yard_grid()`, `account_roles()`, `list_actions(limit)`.

Запись (все требуют роль `admin` через `_require_role`, пишут `tn_action_log`
через `_append_action_log`): `save_block_type`, `save_wagon_scheme`,
`save_carrier`, `save_driver`, `save_vehicle`, `save_wagon`.

`record_action(operator=, action=, subject_type=, subject_id=, details=,
remote_address=)` — публичная точка записи в журнал.

## API-роуты (taksimo_new_api.py, регистрация в конце файла)

```
GET  /api/taksimo-new/admin/catalogs
GET  /api/taksimo-new/admin/accounts
GET  /api/taksimo-new/admin/actions
POST /api/taksimo-new/admin/block-types/{letter}
POST /api/taksimo-new/admin/wagon-schemes/{code}
POST /api/taksimo-new/admin/carriers
POST /api/taksimo-new/admin/drivers
POST /api/taksimo-new/admin/vehicles
POST /api/taksimo-new/admin/wagons
```

Хелпер `_admin(request)` → 401 без входа, 403 без роли admin.
`_catalog_write(request, writer)` — общий контур: роль, тело, единый разбор
ошибок (`_catalog_error`: PermissionError→403, KeyError→404, иначе 400).
Ответы записи — 201 `{"ok": true, "record": {...}}`.

Авторизация: cookie `taksimo_new_auth`, middleware
`taksimo_new_auth_middleware` (`taksimo_new_auth.py:111`). Пути
`/api/taksimo-new/integration/` middleware пропускает — у них Bearer.

Вход/выход (`auth_login`, `auth_logout`) тоже пишутся в `tn_action_log`.

## Утилита

`scripts/taksimo-new-operator.py` — `choices=store.ROLE_CODES`, то есть любая
из девяти ролей, включая `admin`. PIN вводится через getpass, не печатается.

## Тесты

`tests/test_taksimo_new_catalogs.py` (10 проверок) — права ролей, валидация
ИНН/веса, запись в журнал, `ROLE_CODES`, `operator_accounts_ready` с админом.
В `tests/test_taksimo_new_api.py` — 3 проверки админских роутов.
Итого 29 тестов новой Таксимо, 9 пропускаются без aiohttp в песочнице.

Запуск: `python3 -m unittest tests.test_taksimo_new_catalogs` (pytest в
проекте не установлен).

## Состояние

Миграции `009` и `010` **применены 12.10.2026** — см. заметку
`taksimo-new-migrations-applied.md`. Рабочая база: 12 миграций, 35 таблиц.

## Фронтенд (коммит `76e901b`)

Вкладка `data-tab="admin"` («Администрирование») в `index.html`, видна только
роли `admin` через атрибут `data-admin-only`. Вкладка `manage` («Управление»)
осталась за Оператором 1 (`data-operator1-only`) — это разные разделы.

В `web/taksimo-new/app.js`:
- `roleLabels` — подписи всех восьми ролей (было жёстко три оператора);
- `isAdmin()`, `hasRole()`; `loadIdentity()` управляет `[data-admin-only]`;
- `loadAdmin()` грузит `/admin/catalogs`, `/admin/accounts`,
  `/admin/actions?limit=50`; загрузчик в `loadPanel` под ключом `admin`;
- формы: `adminCarrierForm`, `adminDriverForm`, `adminVehicleForm`,
  `adminWagonForm` через общий `submitAdminForm`; плиты и схемы правятся
  через `window.prompt`.

Фронтенд раздаёт **Nginx** из `root /MAX_BOT/web` (`scripts/nginx-*.conf:13`),
не aiohttp: на порту 8765 `/taksimo-new/...` отдаёт 404 — это норма.
Проверять страницу только через домен, а не через 127.0.0.1:8765.

## Дальше

Роли диспетчера и бухгалтеров на фронте пока только подписаны, но у них нет
своих разделов — этапы 3–5 плана (диспетчер, бухгалтеры, операторы).
