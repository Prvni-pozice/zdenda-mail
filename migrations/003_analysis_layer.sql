-- 003: aktuální umístění mailu na serveru + obsahová analytická vrstva
-- Aplikováno na existující DB ručně (init_db spouští migrace jen u nové DB).

-- Kde mail aktuálně leží na serveru (sloupec `folder` = původní složka při stažení).
ALTER TABLE messages ADD COLUMN current_folder   TEXT;
ALTER TABLE messages ADD COLUMN current_uid      INTEGER;
ALTER TABLE messages ADD COLUMN folder_synced_at TEXT;

CREATE INDEX IF NOT EXISTS idx_messages_current_folder ON messages(current_folder);

-- Obsahová/sémantická analýza jednoho mailu. Víc verzí analýzy na 1 mail.
CREATE TABLE message_analysis (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id            INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    relationship          TEXT,   -- klient|dodavatel|urad|interni|banka|osobni|sluzba|neznamy
    intent                TEXT,   -- zadost|informace|faktura|notifikace|nabidka|stiznost|odpoved|automaticke|jine
    topic                 TEXT,   -- krátký štítek tématu
    summary               TEXT,   -- 1-2 věty shrnutí obsahu
    contact_person        TEXT,   -- jméno člověka, se kterým se komunikuje
    thread_key            TEXT,   -- normalizovaný subject pro seskupení vlákna
    needs_reply           INTEGER,-- 0/1 — vyžaduje reakci majitele
    suggested_category    TEXT,
    suggested_subcategory TEXT,
    suggested_folder      TEXT,
    confidence            REAL,
    analysis_version      TEXT NOT NULL,
    analyzed_by           TEXT NOT NULL DEFAULT 'claude_code',
    analyzed_at           TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(message_id, analysis_version)
);
CREATE INDEX idx_analysis_message      ON message_analysis(message_id);
CREATE INDEX idx_analysis_relationship ON message_analysis(relationship);
CREATE INDEX idx_analysis_needs_reply  ON message_analysis(needs_reply);
