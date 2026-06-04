# OMEGA AI LAB — бот MAX

AI-помощник и комментарии к постам канала в [MAX Messenger](https://max.ru).

## Структура

```
MAX_BOT/
├── .env                 # секреты (не в Git)
├── data/
│   ├── schema.sql       # схема SQLite
│   └── comments.db      # рабочая БД (не в Git)
├── fotonych-bot/        # Python 3.12, maxapi, OpenAI
└── web/                 # сайт и mini-app комментариев
```

## Запуск на сервере

```bash
cd fotonych-bot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example ../.env   # заполнить MAX_BOT_TOKEN, OPENAI_API_KEY
cd /MAX_BOT/fotonych-bot
nohup .venv/bin/python bot.py >> bot.log 2>&1 &
```

Проверка: `curl http://127.0.0.1:8765/api/health`

Сайт: `https://max.avtmsk.ru` — nginx отдаёт `web/`, проксирует `/api/` → `127.0.0.1:8765`.

## Mini-app комментариев

В панели MAX: URL мини-приложения `https://max.avtmsk.ru/comments.html`.

Под постами канала бот добавляет кнопку «Комментарии» (`OpenAppButton`).

## Git

- В репозитории: код, `web/`, `data/schema.sql`
- Не коммитить: `.env`, `data/*.db`, `bot.log`, `.venv`
