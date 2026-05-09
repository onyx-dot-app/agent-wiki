# Agent Activity Registry

A small visibility layer that lets every wiki `.md` advertise the
agents (and humans) currently working on it. The
`agent_activity` Postgres table is the source of truth — there is
**no** on-disk representation of the registry. State surfaces in two
places:

* The `read_page` / `read_doc` tool responses include an `agents`
  field, so a model picking up a doc immediately sees who else is
  reading or writing it.
* The wiki UI's `GET /api/documents/file/activity?path=...` endpoint
  drives a collapsible "Active agents" panel at the top of every
  page.

The doc body itself is committed verbatim. Reads do not touch the
file. This avoids the read→commit churn that previously made
`base_sha` optimistic concurrency unreliable.

## What the API returns

`GET /api/documents/file/activity?path=guide.md` →

```json
{
  "path": "guide.md",
  "agents": [
    {
      "owner_display": "Yuhong Sun",
      "agent_name": "claude-code",
      "activity": "read",
      "description": null,
      "registered_at": "2026-05-09T14:18:00+00:00",
      "expires_at":   "2026-05-10T14:18:00+00:00"
    },
    {
      "owner_display": "Alice Chen",
      "agent_name": null,
      "activity": "wrote",
      "description": "Refactoring the trigger storage section",
      "registered_at": "2026-05-09T15:12:00+00:00",
      "expires_at":   "2026-05-10T15:12:00+00:00"
    }
  ]
}
```

Per-row fields (defined in `app/models/document.py:ActivityRowView`):

| field            | meaning                                                                          |
|------------------|----------------------------------------------------------------------------------|
| `owner_display`  | The user's display name, falling back to email. (DB stores `user_id`.)           |
| `agent_name`     | The named agent acting for the user; `null` if the agent didn't identify itself. |
| `activity`       | `"read"` or `"wrote"`.                                                           |
| `description`    | Commit message for `wrote`; `null` for `read`.                                   |
| `registered_at`  | UTC ISO 8601 — when the row was last upserted.                                   |
| `expires_at`     | UTC ISO 8601 — when the cleanup task removes the row.                            |

The same shape rides on `read_page` / `read_doc` tool responses (under
the `agents` key) so models can coordinate without an extra tool
call. Historical reads (`read_doc` with `sha != HEAD`) return
`agents: []` — we don't preserve activity history alongside content.

## How rows are created

* **`read`** — registered automatically on successful HEAD reads
  through `read_page` / `read_doc`.
* **`wrote`** — registered automatically on successful writes through
  `write_doc`, `edit_doc`, `multi_edit`, or `apply_patch`. The commit
  message becomes the `description`.

Both go through `app.wiki.agent_activity.upsert_activity`. The
natural key is `(user_id, agent_name, doc_path, activity)` (Postgres
NULLS NOT DISTINCT), so re-registering by the same user+agent+activity
slides `expires_at` forward and overwrites `description` rather than
inserting a duplicate.

## TTL and cleanup

`DEFAULT_TTL = 24h`. On every UPSERT the caller schedules a delayed
task on `triggers_queue` (see
`app/tasks/agent_activity.py:cleanup_expired_activity`). At
`expires_at` the task fires, double-checks the row hasn't been
re-registered (renewals stamp a later `expires_at`, so the old fire
detects the mismatch and no-ops), then deletes it. Activity is
DB-only, so cleanup is a single `DELETE` — no body refresh, no
commit.

On server restart, `app/main.py:create_app` calls
`schedule_all_pending_cleanups()`, which schedules a fresh cleanup
for every active row. Past-due rows fire immediately. This is the
only thing keeping orphaned rows from outliving an unclean shutdown.

## Why DB-only (the previous frontmatter design)

We initially rendered the registry as YAML frontmatter on each `.md`.
That had two failure modes:

1. **Read = commit.** Every read upserted the row, slid `expires_at`,
   re-rendered the frontmatter, and committed. Every commit advanced
   the doc's HEAD, which silently invalidated any `base_sha` an agent
   was holding — producing `stale_base` errors with no real
   conflict.
2. **Tamper guard noise.** Agents had to round-trip the frontmatter
   exactly; the codebase carried a `assert_frontmatter_unchanged`
   guard, a tamper exception, and dual logic on every read/write
   path to keep things in sync.

The current DB-only design eliminates both. Reads are pure. Writes
commit the body the user/agent supplied. The `agents:` block doesn't
exist on disk for anyone to corrupt or mis-render.

## What this does not (yet) handle

* **Move and delete** — when a doc is renamed or deleted, the
  registry rows for the old path are not yet repointed. The
  `agent_activity.rename_doc` / `delete_for_doc` helpers exist; they
  just need to be wired into `app/wiki/notify.py:after_path_move` /
  `after_doc_delete`.
* **MCP / external agents** — registration runs from inside the chat
  agent's tool handlers under a Flask request context, so an
  authenticated `current_user()` is available. Tasks that don't have
  a request user (pgmq background work, the document-updater agent)
  skip registration silently.
* **Agent self-naming** — the chat and wiki-qa agents don't yet set
  `agent_activity.agent_name_var`, so `agent_name` renders as
  `null`. Wiring in a name is a one-line change in the agent entry
  point when we want to start distinguishing them.
* **Live UI updates** — the wiki page panel fetches on mount and on
  window focus. A websocket / polling layer would surface other
  agents arriving in near-real-time; currently the user sees them
  the next time the tab gains focus.

## Files

* `backend/app/db/models.py` — schema (`AgentActivity` table).
* `backend/app/wiki/agent_activity.py` — DB repo (no rendering, no
  parsing, no tamper guard).
* `backend/app/tasks/agent_activity.py` — pgmq cleanup task and
  startup re-scheduling.
* `backend/app/llm/agents/tools/_doc_helpers.py:mark_doc_read` /
  `commit_and_fan_out` — wires read / write tools into the
  registry.
* `backend/app/llm/agents/tools/read_page.py` /
  `backend/app/llm/agents/tools/read_doc.py` — populate the
  `agents` field on responses.
* `backend/app/api/documents.py:file_activity` —
  `GET /api/documents/file/activity` for the UI panel.
* `backend/app/models/document.py` — `ActivityRowView` /
  `DocumentActivityResponse`.
* `frontend/src/app/wiki/[[...slug]]/page.tsx:ActiveAgentsBar` —
  the collapsible panel at the top of every wiki doc.
* `backend/app/main.py:create_app` — calls the restart scan.
