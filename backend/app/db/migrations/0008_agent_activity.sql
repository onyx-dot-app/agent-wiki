-- Agent activity registry: per (user, agent, doc, activity) row that
-- expires after a configured TTL. The DB is the source of truth; the
-- wiki frontmatter `agents:` block on each .md file is rendered from
-- here. See `app/wiki/agent_activity.py`.
CREATE TABLE IF NOT EXISTS agent_activity (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_name      TEXT,                              -- null when the agent didn't name itself
    doc_path        TEXT NOT NULL,                     -- wiki-relative .md path
    activity        TEXT NOT NULL CHECK (activity IN ('read', 'wrote')),
    description     TEXT,                              -- null = "N/A" in rendered frontmatter
    registered_at   TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at      TEXT NOT NULL                      -- ISO 8601 UTC
);

-- Natural-key uniqueness. SQLite treats NULL as distinct in normal UNIQUE
-- indexes, so we COALESCE the nullable agent_name to '' for the index.
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_activity_natural_key
    ON agent_activity (user_id, COALESCE(agent_name, ''), doc_path, activity);

CREATE INDEX IF NOT EXISTS idx_agent_activity_doc_path  ON agent_activity (doc_path);
CREATE INDEX IF NOT EXISTS idx_agent_activity_expires_at ON agent_activity (expires_at);
