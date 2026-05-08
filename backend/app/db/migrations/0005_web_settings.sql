-- Single-row table for runtime web search/crawl configuration. id=1 always.
-- Provider choice is fixed: Serper for search, Firecrawl for crawl.
CREATE TABLE IF NOT EXISTS web_settings (
    id                 INTEGER PRIMARY KEY CHECK (id = 1),
    serper_api_key     TEXT NOT NULL DEFAULT '',
    firecrawl_api_key  TEXT NOT NULL DEFAULT '',
    updated_at         TEXT NOT NULL DEFAULT (datetime('now'))
);
