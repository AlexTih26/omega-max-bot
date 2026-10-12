---
title: "Вечерний отчёт о погрузке: источник — legacy Таксимо, 16:00 МСК"
---

# Вечерний отчёт о погрузке (правка 08.10.2026, проверена 12.10.2026)

## Решение

Отчёт в чат MAX строится **по старой (legacy) Таксимо**, а не по новой.
Время — **16:00 МСК** из `.env` (`TAKSIMO_REPORT_HOUR=16`,
`TAKSIMO_REPORT_MINUTE=0`, `TAKSIMO_TIMEZONE=Europe/Moscow`), а не из дефолта
кода (`17`).

## Где что

- `fotonych-bot/bot.py:49` — `from taksimo_notify import daily_report_loop as
  taksimo_daily_report_loop`;
- `fotonych-bot/bot.py:51` — `set_taksimo_new_bot` **сохранён** (событийные
  уведомления новой Таксимо живы);
- `fotonych-bot/bot.py:596` — `asyncio.create_task(taksimo_daily_report_loop())`;
- `fotonych-bot/taksimo_notify.py:61-64` — `_report_time()` читает env;
- `fotonych-bot/taksimo_notify.py:280-287` — тексты берут час из `_report_time()`,
  зашитого «16:00» нет.

Цикл новой Таксимо (`taksimo_new_notify.daily_report_loop`, жёстко 17:00) больше
не запускается.

## Проверено на сервере (12.10.2026)

Отчёты уходят: `за 2026-10-09`, `за 2026-10-10`, `за 2026-10-11` — каждая в
16:00:19–20 МСК, чат `-75600324831299`. Последнее упоминание
`taksimo_new_notify` — 08.10 00:05, после правки ни разу.
Стейт: `data/taksimo_notify_state.json` → `last_daily_report` совпадает с логом.

## Осторожно

- Не путать: `TAKSIMO_REPORT_HOUR` читает **только** legacy-цикл.
- Если править время — править `.env`, не дефолт в коде.
- Рядом лежит `data/taksimo_notify_state.json.bak` (от 08.10) — остаток ручной
  операции, к удалению.
- С включением единой модели новая программа начнёт считать **свой** отчёт с
  11.10.2026 параллельно со старым — это ожидаемая сверка, а не дубль.
