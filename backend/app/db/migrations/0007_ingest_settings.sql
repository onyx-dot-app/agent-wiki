-- Single-row table for runtime ingest configuration. id=1 always.
-- max_doc_chars: hard cap on the size of pushed document content.
-- Pushes whose content exceeds this are rejected with 413 at the API.
CREATE TABLE IF NOT EXISTS ingest_settings (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    max_doc_chars   INTEGER NOT NULL DEFAULT 100000,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
