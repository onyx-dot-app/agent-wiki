# Triggers

Tools for creating, updating, and listing natural-language triggers on wiki pages and folders. A trigger watches a wiki file or directory and fires when an update matches a natural-language condition (or, for time-based triggers, on an interval).

## Tools

- `get_trigger_destinations()` — list the available trigger destinations (id, name, description). Call before `create_trigger` / `update_trigger` when the user wants to choose where a fire is delivered, or when they ask what destinations are available. Read-only.
- `create_trigger(scope_path, trigger_nl_condition, trigger_fire_message, destination?)` — register a natural-language trigger. Three parts: **if** (`trigger_nl_condition` — when to fire), **fire message** (`trigger_fire_message` — what to deliver), **destination** (slug from `get_trigger_destinations`; defaults to `event_log` → records to the Event Log). **Only call after the user has explicitly asked for a trigger.**
- `update_trigger(trigger_id, ...partial fields)` — modify an existing trigger you own. Pass any of `scope_path`, `trigger_nl_condition`, `trigger_fire_message`, `destination`, `enabled`. Omit fields you don't want to change.
