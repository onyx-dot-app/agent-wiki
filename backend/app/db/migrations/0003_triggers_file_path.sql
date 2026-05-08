-- Triggers are now stored on disk as YAML files inside the wiki repo;
-- SQLite is a cache. Track the file path so we can resolve trigger_id
-- back to its YAML path (for git-history lookups, deletes, and rebuilds).
ALTER TABLE triggers ADD COLUMN file_path TEXT;
