PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;

-- Surová data, immutable
CREATE TABLE messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    uid             INTEGER NOT NULL,
    folder          TEXT NOT NULL DEFAULT 'INBOX',
    message_id      TEXT,
    in_reply_to     TEXT,
    thread_refs     TEXT,                       -- JSON array
    from_addr       TEXT NOT NULL,
    from_name       TEXT,
    to_addrs        TEXT,                       -- JSON array
    cc_addrs        TEXT,                       -- JSON array
    subject         TEXT,
    date_sent       TEXT,                       -- ISO 8601
    date_received   TEXT,                       -- ISO 8601
    body_text       TEXT,
    body_html       TEXT,
    headers_raw     TEXT,                       -- JSON
    has_attachments INTEGER NOT NULL DEFAULT 0,
    size_bytes      INTEGER,
    fetched_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(uid, folder)
);
CREATE INDEX idx_messages_date_sent ON messages(date_sent);
CREATE INDEX idx_messages_from ON messages(from_addr);

CREATE TABLE attachments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id  INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    filename    TEXT,
    mime_type   TEXT,
    size_bytes  INTEGER,
    saved_path  TEXT
);

-- Verze instrukcí (CLAUDE.md verze nebo manual tag)
CREATE TABLE prompt_versions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    version_tag  TEXT UNIQUE NOT NULL,           -- např. "v1-conservative-2026-05-11"
    instructions TEXT NOT NULL,                  -- snapshot klasifikační instrukce
    notes        TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Klasifikace (může být víc verzí na 1 mail)
CREATE TABLE classifications (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id         INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    prompt_version_id  INTEGER NOT NULL REFERENCES prompt_versions(id),
    category           TEXT NOT NULL,            -- invoice|important|unimportant|unsure
    confidence         REAL NOT NULL,
    reason             TEXT,
    sender_type        TEXT,
    classified_by      TEXT NOT NULL DEFAULT 'claude_code', -- pro audit (claude_code|human|rule)
    created_at         TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_class_message ON classifications(message_id);

-- Lidské labely (ground truth pro učení)
CREATE TABLE human_labels (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id    INTEGER NOT NULL UNIQUE REFERENCES messages(id) ON DELETE CASCADE,
    category      TEXT NOT NULL,
    note          TEXT,
    labeled_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Aplikované akce (audit)
CREATE TABLE actions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id    INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    action_type   TEXT NOT NULL,
    target        TEXT,
    dry_run       INTEGER NOT NULL,
    success       INTEGER,
    error         TEXT,
    applied_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE runs (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    command            TEXT NOT NULL,
    started_at         TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at        TEXT,
    last_uid           INTEGER,
    messages_processed INTEGER DEFAULT 0,
    errors_count       INTEGER DEFAULT 0,
    notes              TEXT
);
