# `015`: очередь машин и раздельные состояния рейса

**Статус: APPROVED — владелец утвердил `Q1A`, `Q2A`, `Q3A`, `Q4A`,
`Q5A`, `Q6A`, `Q7A`, `Q8B` 13.10.2026.**

Это утверждённый исходный контракт миграции `015`, а не подтверждение её
применения в конкретной MySQL-инсталляции. Миграция зависит от `001–014`, не
запускается приложением автоматически и не меняет legacy-код, FastAPI runtime
или опубликованные маршруты.

## 1. Подтверждённые локальные факты

| № | Факт | Локальный первоисточник |
| --- | --- | --- |
| F15-01 | Нормативный порядок: `014` — учётки и права, `015` — очередь и состояния рейса, `016` — аудит и идемпотентность, `017` — FastAPI-outbox. | [`README.md:657-672`](../README.md#L657-L672) |
| F15-02 | `tn_trips.status` имеет legacy `CHECK` только для `draft`, `ttn_issued`, `printed`, `er_sent`, `er_returned`, `cancelled`. В таблице нет жизненного цикла машины. | [`migrations/taksimo_new/012_trips_ttn_and_expedition_receipts.sql:10-39`](../migrations/taksimo_new/012_trips_ttn_and_expedition_receipts.sql#L10-L39) |
| F15-03 | `tn_vehicles`, `tn_intakes`, `tn_trips` и `tn_operator_accounts` уже существуют; их ключи имеют тип `BIGINT UNSIGNED`. | [`migrations/taksimo_new/001_initial.sql:3-73`](../migrations/taksimo_new/001_initial.sql#L3-L73), [`migrations/taksimo_new/010_admin_accounts_and_catalogs.sql:117-138`](../migrations/taksimo_new/010_admin_accounts_and_catalogs.sql#L117-L138), [`migrations/taksimo_new/012_trips_ttn_and_expedition_receipts.sql:10-39`](../migrations/taksimo_new/012_trips_ttn_and_expedition_receipts.sql#L10-L39) |
| F15-04 | `014` создаёт `tn_roles` и `tn_role_permissions`; текущий FastAPI runtime их не читает до `016`. | [`migrations/taksimo_new/014_accounts_and_permissions.sql:12-113`](../migrations/taksimo_new/014_accounts_and_permissions.sql#L12-L113), [`app/auth/permissions.py:34-55`](../app/auth/permissions.py#L34-L55) |
| F15-05 | `tn_notification_outbox` уже создаётся миграцией `005` (строки 58–71), а `006` добавляет для неё защитные триггеры. Это факт об исходнике, не доказательство применения в БД. | [`migrations/taksimo_new/005_rumex_bridge_and_physical_lifecycle.sql:58-71`](../migrations/taksimo_new/005_rumex_bridge_and_physical_lifecycle.sql#L58-L71), [`migrations/taksimo_new/006_rumex_bridge_and_physical_lifecycle_triggers.sql:135-174`](../migrations/taksimo_new/006_rumex_bridge_and_physical_lifecycle_triggers.sql#L135-L174) |

## 2. Утверждённый состав `015`

### Q1A и Q8B — документный статус без переписывания legacy-истории

`015` добавляет в `tn_trips` только `document_status VARCHAR(24) NULL` и
проверку допустимых непустых значений:

```text
draft -> ttn_issued -> ttn_printed -> accountant_review -> er_sent
      -> approved -> documents_printed -> handed_to_driver
      -> cancelled
```

Существующий `tn_trips.status` и его `CHECK` не удаляются, не расширяются и не
изменяются. Миграция не выполняет backfill или иной `UPDATE` исторических рейсов:
у всех старых и уже мигрированных строк `document_status` остаётся `NULL`.

В новом FastAPI-контуре writer пока отсутствует. Он сможет заполнять
`document_status` только после отдельного подходящего утверждения. До него
`tn_trips.status` остаётся compatibility-источником для legacy без выведенного
или неявного соответствия с новым документным автоматом.

### Q2A и Q3A — границы документного автомата

`er_returned` остаётся фактом `tn_expedition_receipts.status`; он не становится
вершиной `document_status`. Обратный переход `return-for-correction` в `015` не
добавляется. Документная отмена допускается только до `handed_to_driver` и
требует причины, но runtime-команды и их проверка не входят в `015`.

### Q4A и Q5A — отдельный цикл очереди

`tn_vehicle_queue_entries` — текущая проекция одного жизненного цикла машины.
Одна строка соответствует одному циклу и никогда не удаляется. Допустимые
состояния только такие:

```text
queued -> assigned_to_trip -> departed -> arrived_taksimo -> received
       -> returned_to_queue
queued -> cancelled
assigned_to_trip -> cancelled
```

`returned_to_queue` и `cancelled` терминальны для этой строки. Возврат создаёт
новый отдельный цикл с новой строкой `queued`; старый факт возврата остаётся в
истории. Generated-колонка `active_vehicle_id` делает `vehicle_id` уникальным
только в активных состояниях (`queued`, `assigned_to_trip`, `departed`,
`arrived_taksimo`, `received`); множество `NULL` для терминальных циклов
допустимо в MySQL.

`tn_vehicle_queue_transitions` фиксирует начальное создание и каждый последующий
переход, включая начальный факт `NULL -> queued`. `015` не добавляет триггеры
`UPDATE`/`DELETE` для этой таблицы. Схема сама не может обеспечить граф
переходов, атомарность проекции с историей или порядок временных меток: это
обязанность будущей команды/репозитория, а DB-защиты будут отдельно спроектированы
и утверждены для `016`.

### Q6A — существующий outbox не включается

Проверка исходников подтверждает, что `tn_notification_outbox` уже создан в
`005`; поэтому `015` не создаёт, не использует и не наполняет outbox. Выбор и
подключение FastAPI-outbox остаются отдельной задачей `017`. В `015` нет доставки,
строк outbox, endpoint-действий или автоматического возврата машины в runtime.

### Q7A — точные capability и базовые гранты

`015` добавляет в `tn_role_permissions` только следующие capability. Это данные
будущей матрицы прав, а не включение FastAPI-проверок или маршрутов.

| Capability | Уровень | Роли |
| --- | --- | --- |
| `queue.entry.create` | `edit` | `dispatcher` |
| `queue.entry.view` | `view` | `admin`, `dispatcher` |
| `queue.entry.cancel` | `edit` | `admin` |
| `queue.transition` | `edit` | `dispatcher`, `operator` |
| `queue.history.view` | `view` | `admin`, `dispatcher` |

Гранты сохраняют уже описанные границы ролей: администратор смотрит очередь и
отменяет запись, диспетчер выбирает машину и ведёт отправку, оператор подтверждает
физические переходы. `014` остаётся единственным источником ранее добавленного
`trip.create`; иных capability `015` не вводит.

## 3. Точные границы SQL-миграции

Миграция `015_vehicle_queue_and_trip_states.sql`:

1. является source-only, forward-only DDL MySQL 8 / InnoDB / `utf8mb4` после
   `001–014` и не применяется автоматически;
2. добавляет nullable `document_status` с отдельным `CHECK`, не меняя legacy
   `status` и не переписывая строки;
3. создаёт `tn_vehicle_queue_entries` с публичным UUID, FK на машину, рейс,
   приёмку и акторов, временными полями, причиной отмены, индексами и generated
   active-key;
4. создаёт `tn_vehicle_queue_transitions` с nullable `from_state`, обязательными
   `to_state` и актором, причиной, временем и optional JSON-деталями;
5. seed-ит только capability из Q7A и не изменяет auth runtime;
6. не добавляет триггеры, idempotency-хранилище, аудит-команды, outbox, SQL-
   репозиторий или регистрацию endpoint.

## 4. Матрица утверждения владельца

| Код | Утверждённое решение |
| --- | --- |
| Q1A | Nullable `document_status` без backfill. |
| Q2A | `er_returned` остаётся в экспедиторской расписке, а не в новом документном автомате. |
| Q3A | Нет `return-for-correction` в `015`; отмена документа ограничена будущей runtime-командой до `handed_to_driver`. |
| Q4A | Одна строка очереди — один неизменяемо сохранённый цикл; возврат начинает новую строку. |
| Q5A | `015` хранит схему и историю переходов, но не включает runtime-команду возврата или DB-триггеры переходов. |
| Q6A | Источник `tn_notification_outbox` — `005`; `015` его не трогает, FastAPI-outbox — `017`. |
| Q7A | Ровно пять capability и базовые гранты из таблицы выше. |
| Q8B | Старые `document_status` остаются `NULL`; writer отсутствует, а legacy `status` остаётся compatibility-источником без неявного маппинга. |
