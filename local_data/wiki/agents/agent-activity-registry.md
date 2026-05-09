# Agent Activity Registry

A small visibility layer that lets every wiki `.md` advertise the agents
currently working on it (or that recently read it). The DB is the source
of truth; the YAML frontmatter on each doc is rendered from there.

## What it looks like

When at least one agent is active on a doc, the doc opens with a
fenced YAML block:

```yaml
---
# DO NOT EDIT — managed by the agent activity registry.
# Direct edits to the `agents:` block will be rejected on write.
agents:
  - owner: Yuhong Sun
    agent: claude-code
    activity: read
    description: N/A
    expires_at: 2026-05-09T14:30:00+00:00
  - owner: Alice Chen
    agent: N/A
    activity: wrote
    description: Refactoring the trigger storage section
    expires_at: 2026-05-09T15:12:00+00:00
---
```

Per-entry fields:

| field         | meaning                                                                             |
|---------------|-------------------------------------------------------------------------------------|
| `owner`       | The user's display name, falling back to email. (DB stores `user_id`.)              |
| `agent`       | The named agent acting for the user; `N/A` if the agent didn't identify itself.     |
| `activity`    | Either `read` or `wrote`. Both expire on the same TTL.                              |
| `description` | The commit message for `wrote`; `N/A` for `read`.                                   |
| `expires_at`  | UTC ISO 8601. After this, a pgmq cleanup deletes the row and re-renders the block.  |

## How rows are created

* **`read`** — registered automatically when an agent successfully reads a
  doc through `read_page` or `read_doc` (HEAD reads only — historical
  reads with an explicit `sha` do not register).
* **`wrote`** — registered automatically when an agent's write succeeds
  through `write_doc`, `edit_doc`, `multi_edit`, or `apply_patch`. The
  commit message becomes the `description`.

Both go through the same `app.wiki.agent_activity.upsert_activity`. The
natural key is `(user_id, COALESCE(agent_name, ''), doc_path, activity)`,
so re-registering by the same user+agent+activity slides `expires_at`
forward and overwrites `description` — it does not create a duplicate row.

## TTL and cleanup

`DEFAULT_TTL = 24h` in `app/wiki/agent_activity.py`. On every UPSERT the
caller schedules a delayed task on `triggers_queue` (see
`app/tasks/agent_activity.py:cleanup_expired_activity`). The task fires
at `expires_at`, deletes the row if it's still the same one (renewals
look like a new `expires_at`, so the old fire detects the mismatch and
no-ops), and commits a frontmatter-only refresh of the doc.

On server restart, `app/main.py:create_app` calls
`schedule_all_pending_cleanups()`, which schedules a fresh cleanup for
every row in the table. Past-due rows fire immediately. This is the only
guarantee we have that an unclean shutdown doesn't leave entries stuck.

## Why direct edits are rejected

The on-disk YAML is rendered from the DB; if an agent could change it,
the file and DB would diverge until the next refresh. To keep the two
in sync we treat the `agents:` field as system-managed:

* Before any wiki write commits, `_doc_helpers.commit_and_fan_out` calls
  `agent_activity.assert_frontmatter_unchanged`. It parses both the
  incoming body's `agents:` block and the on-disk one. If they differ
  (modified, removed, added) the write is rejected with a `ToolError`.
* If the block is unchanged, the write proceeds: a `wrote` activity is
  upserted, and the body's `agents:` block is replaced wholesale with a
  fresh render from the now-updated DB state.

Other frontmatter keys (e.g. a `title:`) are ignored by the guard and
preserved through the rewrite.

## Frontmatter-only commits and triggers

When a `read` registration fires, the resulting commit only changes the
frontmatter — content is identical. To avoid spurious natural-language
trigger fires, the registry's refresh path (`refresh_doc_frontmatter`)
calls the FTS reindexer directly and **does not** invoke
`wiki_notify.after_doc_write`. Trigger fan-out only happens for real
content changes (writes through the doc-edit tools).

## What this does not (yet) handle

* **Move and delete** — when a doc is renamed or deleted, the registry
  rows for the old path are not yet rewritten. Follow-up: hook
  `agent_activity.rename_doc` / `delete_for_doc` into the move/delete
  paths in the wiki API.
* **MCP / external agents** — the activity registration runs from inside
  the chat agent's tool handlers under a Flask request context, so an
  authenticated `current_user()` is available. Tasks that don't have a
  request user (pgmq background work, the document-updater agent) skip
  registration silently.
* **Agent self-naming** — the chat and wiki-qa agents don't yet set
  `agent_activity.agent_name_var`, so `agent` renders as `N/A`. Wiring
  in a name is a one-line change in the agent entry point when we want
  to start distinguishing them.

## Files

* `backend/app/db/models.py` — schema.
* `backend/app/wiki/agent_activity.py` — repo, parser, renderer, guard.
* `backend/app/tasks/agent_activity.py` — pgmq cleanup + restart scan.
* `backend/app/llm/agents/tools/_doc_helpers.py` — wires read/write tools
  into the registry.
* `backend/app/main.py:create_app` — calls the restart scan.
