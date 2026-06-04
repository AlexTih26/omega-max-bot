-- Схема SQLite для OMEGA comments (создаётся автоматически в comments_store.init_db)
-- Файл для Git; рабочая БД: data/comments.db (в .gitignore)

CREATE TABLE IF NOT EXISTS posts (
    post_id TEXT PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id TEXT NOT NULL,
    max_user_id INTEGER,
    author TEXT NOT NULL DEFAULT 'Гость',
    text TEXT NOT NULL,
    created_at REAL NOT NULL,
    FOREIGN KEY (post_id) REFERENCES posts(post_id)
);

CREATE INDEX IF NOT EXISTS idx_comments_post ON comments(post_id);
