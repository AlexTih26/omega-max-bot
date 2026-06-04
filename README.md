# OMEGA AI LAB — бот MAX

AI-помощник в чатах и **комментарии к постам канала** в [MAX Messenger](https://max.ru).  
Сайт и mini-app: `https://max.avtmsk.ru`

---

## Возможности

| Модуль | Что делает |
|--------|------------|
| **AI** | Ответы в личке и группах (OpenAI), память диалога, `/start`, `/clear`, меню |
| **Комментарии** | Кнопка под постом канала → mini-app внутри MAX |
| **Ветки** | Ответ на комментарий с цитатой «кому» |
| **Лайки** | 👍 только у того, кто лайкнул; счётчик и мини-аватары |
| **Счётчик** | На кнопке: `💬 Комментарии · N` (обновляется при новом комментарии) |

---

## Структура проекта

```
MAX_BOT/
├── .env                      # Секреты (НЕ в Git): токены, ключи
├── .gitignore
├── README.md
│
├── data/                     # Данные на сервере
│   ├── schema.sql            # Схема SQLite (в Git)
│   ├── .gitkeep
│   └── comments.db           # Рабочая БД (НЕ в Git)
│
├── web/                      # Статика для nginx
│   ├── index.html            # Лендинг OMEGA
│   ├── style.css
│   ├── comments.html         # Mini-app комментариев
│   ├── comments.css
│   └── comments.js           # MAX Bridge, лента, лайки, ответы
│
├── scripts/
│   ├── restart-bot.sh        # Перезапуск бота + проверка API
│   ├── push-to-github.sh     # Первый push на GitHub
│   └── github-deploy-key.pub # Публичный SSH-ключ сервера (опционально)
│
└── fotonych-bot/             # Python 3.12, long polling
    ├── bot.py                # Точка входа: polling + API комментариев
    ├── ai.py                 # OpenAI Responses API, история в RAM
    ├── keyboards.py          # Меню, OpenAppButton «Комментарии · N»
    ├── channel_posts.py      # Пост канала → кнопка под сообщением
    ├── comments_api.py       # HTTP API (aiohttp) :8765
    ├── comments_store.py     # SQLite: посты, комментарии, лайки
    ├── comments_button.py    # Обновление счётчика на кнопке в канале
    ├── max_webapp.py         # Проверка initData mini-app (HMAC)
    ├── post_payload.py       # encode/decode postId для open_app
    ├── requirements.txt
    ├── .env.example
    └── .venv/                # Виртуальное окружение (НЕ в Git)
```

---

## Как связаны части

```
Пост в канале MAX
    │
    ▼
bot.py → channel_posts.py → edit_message + OpenAppButton
    │                         (payload = postId = mid сообщения)
    ▼
Пользователь открывает mini-app
    │
    ▼
comments.html + comments.js  ←→  nginx /api/*  ←→  comments_api.py
    │                              (8765)              │
    │                              X-Max-Init-Data     ▼
    └──────────────────────────────────────────  comments.db
```

**Один процесс** `bot.py`: и long polling MAX, и API на `127.0.0.1:8765`.

---

## Переменные окружения (`.env`)

| Переменная | Назначение |
|------------|------------|
| `MAX_BOT_TOKEN` | Токен бота MAX |
| `OPENAI_API_KEY` | Ключ OpenAI |
| `OPENAI_MODEL` | Модель (например `gpt-4.1-mini`) |
| `SYSTEM_PROMPT` | Системный промпт OMEGA |
| `SITE_URL` | `https://max.avtmsk.ru` |
| `COMMENTS_PORT` | Порт API (по умолчанию `8765`) |
| `MAX_BOT_USERNAME` | Username бота для `open_app` (например `id5406829253_bot`) |

Шаблон: `fotonych-bot/.env.example`

---

## Запуск на сервере

```bash
cd /MAX_BOT/fotonych-bot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example ../.env   # заполнить значения

# Запуск
nohup .venv/bin/python bot.py >> bot.log 2>&1 &

# Или
/MAX_BOT/scripts/restart-bot.sh
```

**Проверка:**

```bash
curl http://127.0.0.1:8765/api/health          # {"ok": true}
curl https://max.avtmsk.ru/api/health
tail -f /MAX_BOT/fotonych-bot/bot.log
```

---

## Nginx

- Статика: корень `web/` → `https://max.avtmsk.ru/`
- API: `location /api/` → `proxy_pass http://127.0.0.1:8765;`

В панели MAX (mini-app): URL **`https://max.avtmsk.ru/comments.html`**

---

## HTTP API комментариев

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/api/health` | Проверка живости |
| GET | `/api/posts/{post_id}` | Пост + дерево комментариев (`replies`, `reply_to`, `likes`) |
| POST | `/api/posts/{post_id}/comments` | Новый комментарий. Header: `X-Max-Init-Data`. Body: `{ "text", "parent_id"? }` |
| POST | `/api/comments/{id}/like` | Вкл/выкл лайк. Header: `X-Max-Init-Data` |

`post_id` = `mid` сообщения поста в канале.

---

## База данных

Таблицы (см. `data/schema.sql`):

- **posts** — посты канала (`post_id`, `chat_id`, `title`, `message_text`)
- **comments** — комментарии (`parent_id` для ответов, `author_photo`)
- **comment_likes** — лайки по паре `(comment_id, max_user_id)`

Бэкап: копировать `data/comments.db` (не хранить в Git).

---

## Git

Репозиторий: https://github.com/AlexTih26/omega-max-bot

**В репозитории:** код, `web/`, `data/schema.sql`, `scripts/`, README  

**Не коммитить:** `.env`, `data/*.db`, `bot.log`, `.venv`

```bash
git add .
git commit -m "Описание изменений"
git push
```

---

## Требования к боту в канале

- Бот **администратор** канала  
- Право **редактировать сообщения** (для кнопки «Комментарии» под постом)

---

## Полезные команды

```bash
# Перезапуск
/MAX_BOT/scripts/restart-bot.sh

# Первый push (если настраиваете Git заново)
/MAX_BOT/scripts/push-to-github.sh AlexTih26 omega-max-bot
```
