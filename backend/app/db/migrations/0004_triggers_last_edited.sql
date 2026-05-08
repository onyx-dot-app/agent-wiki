-- Denormalized "most recent edit" timestamp on the trigger cache row so the
-- triggers list can show a "last edited" badge without per-row git log calls.
ALTER TABLE triggers ADD COLUMN last_edited_at TEXT;
UPDATE triggers SET last_edited_at = created_at WHERE last_edited_at IS NULL;
