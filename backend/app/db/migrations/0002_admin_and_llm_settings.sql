ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0;

-- Single-row table for runtime LLM configuration. id=1 always.
CREATE TABLE IF NOT EXISTS llm_settings (
    id                INTEGER PRIMARY KEY CHECK (id = 1),
    provider          TEXT NOT NULL,        -- "anthropic" | "openai"
    model             TEXT NOT NULL,
    anthropic_api_key TEXT NOT NULL DEFAULT '',
    openai_api_key    TEXT NOT NULL DEFAULT '',
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
