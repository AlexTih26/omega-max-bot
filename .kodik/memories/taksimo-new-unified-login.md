---
title: "Этап 2: единый вход по ролям, права разделов, журнал"
---

# Новая Таксимо, Этап 2: единый вход по ролям и журнал

Ветка `kodik/new-taksimo-integration`, коммит `ac6af44`.

## Решения владельца (12.10.2026)

- Объединяем **только новую Таксимо**. РУМЕКС (бухгалтеры, диспетчер,
  управление) и legacy Таксимо **не трогаются** — у них свои входы.
- Вход — **логин + PIN** (не перебор PIN: при 8 учётках PIN-ы пересекутся).
- PIN **6–12 цифр**, scrypt. Стандарт не менялся.

## Где что

- `fotonych-bot/taksimo_new_store.py`:
  - `_login(value)` — валидация `[a-z0-9][a-z0-9._-]{2,79}`;
  - `find_operator_for_login(login, pin, verifier)` — вход по логину,
    `WHERE login = %s AND active = 1`, затем проверка PIN;
  - `find_operator_for_pin(pin, verifier, login=None)` — при `login is None`
    берёт только учётки **без логина** (`login IS NULL OR login = ''`);
  - `set_operator_account(..., login=...)` — принимает логин;
  - `list_actions(limit, role, action, subject_type)` — фильтры журнала,
    отдаёт `a.login AS actor_login`;
  - `_operator_from_row` добавляет `login`, если он есть.
- `fotonych-bot/taksimo_new_auth.py`:
  - `SECTION_ROLES` — какие разделы открыты каждой роли;
  - `_permissions(operator)` — словарь `раздел → bool`;
  - `require_roles(*allowed)` — декоратор, 401 без входа, 403 без роли;
  - `/auth/me` отдаёт `permissions`;
  - вход пишет в журнал `details={"login","role"}`, текст ошибки — «Неверный
    логин или PIN».
- `fotonych-bot/taksimo_new_api.py` — `GET /admin/actions` принимает
  `role`, `action`, `subject_type`.
- `scripts/taksimo-new-operator.py` — флаг `--login`.
- `web/taksimo-new/login.html` — поле логина + PIN.
- `web/taksimo-new/app.js` — `state.permissions`, `applyPermissions()`,
  скрывает вкладки по правам.

## Разделы и роли (SECTION_ROLES)

| Раздел | Кто видит |
| --- | --- |
| dashboard, journal, reports, search | все восемь ролей |
| reception, corrections | операторы 1–3 |
| yard, wagons | диспетчер + операторы |
| admin | только admin |

## Миграция 011

`migrations/taksimo_new/011_unified_login_roles.sql` — **применена 12.10.2026**.
Проставляет логины `operator1..3`, заводит `admin`, `dispatcher`,
`accountant1..3` через `INSERT ... SELECT ... WHERE NOT EXISTS` (повторный
прогон не задваивает и не сбрасывает `active`/PIN), добавляет индексы
`idx_tn_action_log_role` и `idx_tn_action_log_subject_kind`.

**Важно:** новые учётки создаются `active = 0` с пустым `pin_hash` — войти по
ним нельзя, пока владелец не выдаст PIN утилитой. Это защита, а не баг.

Повторный прогон падает на `Duplicate key name` (индексы), как и остальные
ALTER-миграции проекта — защита через `tn_schema_migrations`.

## Проверено

12 миграций на копии базы: 8 учёток, три оператора сохранили `active=1` и PIN.
Права ролей проверены напрямую (aiohttp в песочнице нет — 6 тестов
пропускаются). Полный набор 18 модулей тестов зелёный.

## Дальше

Этапы 3–5: разделы диспетчера (приёмка, ТТН), бухгалтера (проверка, ЭР),
оператора (машина, вагон, тупики, Кодар). Сейчас у них есть права и место в
меню, но нет своих экранов.
