-- Users + auth
CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
    email       TEXT UNIQUE NOT NULL,
    name        TEXT,
    password_hash TEXT,        -- null when AUTH_MODE=oidc
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- MCP connections registered by users
CREATE TABLE IF NOT EXISTS mcp_connections (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    transport   TEXT NOT NULL,   -- "stdio" | "http"
    config_json TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Documents: the canonical content lives in git, this row is metadata + index pointer
CREATE TABLE IF NOT EXISTS documents (
    id          TEXT PRIMARY KEY,
    path        TEXT UNIQUE NOT NULL,    -- relative path inside the wiki repo
    title       TEXT,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Triggers (also git-backed; this table is a fast lookup cache)
CREATE TABLE IF NOT EXISTS triggers (
    id              TEXT PRIMARY KEY,
    owner_user_id   TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    scope_path      TEXT NOT NULL,        -- doc path or directory
    kind            TEXT NOT NULL,        -- "delta" | "schedule"
    nl_description  TEXT NOT NULL,
    action_json     TEXT NOT NULL,        -- webhook url, external service config, etc.
    schedule_cron   TEXT,                 -- only for kind="schedule"
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Audit log of events flowing through the system
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL DEFAULT (datetime('now')),
    kind        TEXT NOT NULL,            -- "doc.update" | "trigger.fire" | "webhook.in" ...
    actor       TEXT,                     -- user id or system source
    target      TEXT,                     -- doc id, trigger id, etc.
    payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_kind_ts ON events(kind, ts);

-- FTS5 index for wiki search (bm25 ranking)
CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    doc_id UNINDEXED,
    path,
    title,
    body,
    tokenize = "porter unicode61"
);
