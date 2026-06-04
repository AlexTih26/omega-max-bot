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
    parent_id INTEGER,
    max_user_id INTEGER,
    author TEXT NOT NULL DEFAULT 'Гость',
    author_photo TEXT,
    text TEXT NOT NULL,
    created_at REAL NOT NULL,
    FOREIGN KEY (post_id) REFERENCES posts(post_id),
    FOREIGN KEY (parent_id) REFERENCES comments(id)
);

CREATE TABLE IF NOT EXISTS comment_likes (
    comment_id INTEGER NOT NULL,
    max_user_id INTEGER NOT NULL,
    author TEXT NOT NULL DEFAULT '',
    author_photo TEXT,
    created_at REAL NOT NULL,
    PRIMARY KEY (comment_id, max_user_id),
    FOREIGN KEY (comment_id) REFERENCES comments(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_comments_post ON comments(post_id);
CREATE INDEX IF NOT EXISTS idx_comments_parent ON comments(parent_id);
CREATE INDEX IF NOT EXISTS idx_likes_comment ON comment_likes(comment_id);
