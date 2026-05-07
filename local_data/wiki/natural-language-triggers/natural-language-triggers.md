# Natural-Language Triggers

> **Part of agent-workspace v0.** See the master doc
> [`../architecture_and_progress.md`](../architecture_and_progress.md) for
> the cross-area map. This doc owns trigger storage (git-backed YAML +
> SQLite cache), CRUD API, matching engine, NL evaluation, and the
> post-commit fan-out that fires them. Trigger UI lives in
> [frontend/frontend.md](../frontend/frontend.md). Outbound dispatch
> (webhooks / external services / agent messages) is **deferred** — see
> the TBD callout in `../architecture_and_progress.md` §1.

_Last updated: 2026-05-06_

---

## Design

### Two trigger kinds (schema already supports both)

| `kind` | Fires when… | v0? |
|---|---|---|
| `delta`    | A doc within `scope_path` changes (or new file added in a directory scope) | **yes** |
| `schedule` | Cron matches | wire up in `time_based.py`; v0 still record-only |

### Storage: SQLite-only (v0)

The original design called for git-backed YAML at
`<wiki>/.triggers/<id>.yaml` as the source of truth with SQLite as a cache.
**That was dropped in v0** (decision: 2026-05-06) — triggers live only in
the `triggers` table, mutated through `app/triggers/repo.py`. We trade
trigger history/auditability via git for a simpler write path; can revisit
if/when the design is needed.

`app/triggers/storage.py` is the dead leftover — it can go on a future
cleanup pass. The reference YAML shape (kept here for when YAML is
revisited):

```yaml
id: trg_<uuid>
owner_user_id: usr_…
scope_path: projects/foo.md       # or a directory: projects/
kind: delta                       # or schedule
nl_description: |
  Fire when this project's status changes from green to yellow.
action:                           # v0: ignored; required by schema only when used later
  kind: agent_message             # or webhook | http
  config: {}
schedule_cron: null               # only for kind=schedule
enabled: true
created_at: 2026-05-06T...
```

### Scope resolution

When a doc at `<path>` commits, evaluate triggers attached to `<path>`
**and** every ancestor directory (`app/wiki/filesystem.py:parent_dirs`
already returns these closest-first). This matches the V0 brief: "run
checks on the directories above the file that changed."

For directory triggers, the eval input also includes "this commit added a
new file" as a distinct change type — the NL prompt sees that signal and
can match descriptions like "when a new doc lands in this dir."

### Matching engine

`app/triggers/engine.py:find_matching_triggers(doc_path)`:

```sql
SELECT * FROM triggers
WHERE kind = 'delta'
  AND enabled = 1
  AND scope_path IN (?, ?, ...)   -- doc_path + parent_dirs
```

### NL evaluation

`app/triggers/natural_language.py:matches(nl_description, before, after,
*, change_kind)` runs a single `client.complete()` call (via the LLM seam
described in [agents/chat-agent.md](../agents/chat-agent.md)) with a
tool / JSON-schema-shaped output:

```json
{ "matches": true, "reason": "Status table flipped from green to yellow." }
```

Hard rules (in the prompt):
- **Conservative.** False positives are louder than false negatives in v0.
- **Cite the change.** The `reason` should quote or paraphrase what changed.
- **No outside knowledge.** Only what's in `before`/`after`.

### Trigger evaluation flow (designed; implementation is stub)
1. A doc commit lands (via API or the editor's Save).
2. Worker enqueues reindex + trigger evaluation.
3. For the changed file path **and each parent directory**, load enabled
   triggers from cache.
4. For each, run a small LLM call: "given this change and this NL
   description, did it match?" → yes/no + one-line reason.
5. On match, write an `events` row of kind `trigger.fire`.
6. **No external dispatch in v0.** Surface in the Events tab.

### Fan-out (post-commit)

A new Huey task — likely `tasks/triggers.py:fan_out(doc_path, before, after,
change_kind)` — runs **after** every successful `commit_file` (see
[background-tasks](../background-tasks/background-tasks.md)):

1. `find_matching_triggers(doc_path)`.
2. For each, call `matches(...)`.
3. If `matches=true`, write `events` row of kind `trigger.fire` with
   `payload_json = {trigger_id, doc_path, change_kind, reason, verdict}`.

This is the only thing v0 does on a fire. **Do not add outbound dispatch.**

### Cost (watch this)
- Every doc commit × every matching trigger = an LLM call.
- Mitigate with: tight prompts, small models (`claude-haiku-*` is fine for
  yes/no), short `before`/`after` slices (truncate or diff-only).
- Track per-fire input/output tokens in the event payload so we can
  compute spend later.

### File-by-file (current state)

- `app/triggers/storage.py` — paths only (`<wiki>/.triggers/<id>.yaml`);
  `ensure_triggers_dir`. Real read/write/delete TODO.
- `app/triggers/engine.py` — `find_matching_triggers` (SQL over `triggers`
  cache, includes parent dirs) and `evaluate_delta` (thin wrapper over
  `natural_language.matches`) live. `dispatch` left stubbed (deferred).
- `app/triggers/natural_language.py:matches` — live; single `complete()`
  call with a `report` tool returning `{matches, reason}`. LLM errors
  surface as `(False, "llm_error:<code>")`.
- `app/triggers/diff.py` — builds the BEFORE/AFTER snippets: unified diff
  for edits, full body for creates, full-body fallback when diff covers
  >50% of the file. Truncates each side at 8KB.
- `app/tasks/triggers.py:fan_out_trigger_eval` — Huey task wired into the
  human-edit path (`api/documents.py:put_document_by_path` after
  `commit_file`). Reads BEFORE from `sha^`, AFTER from `sha`, writes
  `trigger.fire` events on match. Registered in `run_worker.py`.
- `app/triggers/time_based.py:due_triggers` — stub.
- `app/api/triggers.py` — all CRUD endpoints still raise. To exercise the
  fire-path today, seed a row directly in the `triggers` SQLite table.

### Out of scope (do not build)
- Outbound webhooks / HTTP calls / agent messages on fire.
- Ambient UI surfacing (badges/toasts) — Events tab only.
- Editing triggers' `action_json` from the UI — v0 has no actions.

---

## Progress

### Working
- Schema in place (migration `0001_init.sql`): `triggers` table with all
  needed columns; `events` table for fires.
- `wiki.filesystem.parent_dirs` — used for ancestor lookup.
- `wiki.git.commit_file` / `delete_path` — needed for YAML mutations.
- LLM client + `LLMError` taxonomy — usable from `matches`.
- **Fire-path on human edits** (post-commit fan-out): `engine.find_matching_triggers`,
  `engine.evaluate_delta`, `natural_language.matches`, `diff.build_payload`,
  and `tasks.triggers.fan_out_trigger_eval` are all live and tested
  (`backend/tests/test_triggers_*`). Triggered from
  `api/documents.py:put_document_by_path` after `commit_file`.

### Stubbed, not wired
- `app/triggers/storage.py` real read/write/delete (only path helpers exist).
- `app/triggers/time_based.py:due_triggers`.
- `app/api/triggers.py` — all CRUD endpoints raise (so triggers must be
  seeded via SQL until the API is wired).
- `engine.dispatch` — deferred per v0 scope (no outbound action).

---

## Work breakdown (Next up)

### D. Triggers (CRUD + storage + engine + fan-out)

1. **Storage (real)**
   - `write_yaml(trigger)` → write file + commit.
   - `read_yaml(trigger_id)` → parse file.
   - `delete_yaml(trigger_id)` → `wiki.git.delete_path` + commit.
   - `list_yaml()` → walk `<wiki>/.triggers/`.

2. **Repo (SQLite cache)**
   - `upsert(trigger)`, `delete(trigger_id)`, `list_for_paths(paths)`,
     `list_for_owner(user_id)`. Pattern: `app/auth/users.py`.
   - `rebuild_from_yaml()` for crash recovery.

3. **CRUD API** (`app/api/triggers.py`)
   - `GET /` (owner-scoped), `POST /`, `PUT /<id>`, `DELETE /<id>`,
     `GET /<id>/history` (git log on the YAML path).
   - v0 only honors `kind=delta`. Reject other kinds with 400 until
     time-based is wired.

4. **Matching engine**
   - `find_matching_triggers(doc_path)` per the SQL above.
   - `evaluate_delta(trigger, before, after, change_kind)` → returns
     `(matched: bool, reason: str)`.

5. **NL evaluator**
   - `matches(...)` with the JSON-schema tool output.
   - Use the configured cheap model unless overridden — these run hot.

6. **Fan-out task**
   - New `app/tasks/triggers.py:fan_out_trigger_eval(doc_path, before,
     after, change_kind)`.
   - Wire into the wiki write path (after `commit_file` succeeds; see
     [flask-and-apis B](../flask-and-apis/flask-and-apis.md#b-wiki-write-path)
     and [background-tasks](../background-tasks/background-tasks.md)).
   - Records `trigger.fire` events.

7. **Triggers UI** — owned by [frontend](../frontend/frontend.md);
   inline on the doc and directory pages. Lists, add (NL description input),
   edit, delete, enable/disable.

### L. Time-based checks (V0 brief)
- Implement `due_triggers(now_iso)` matching enabled `kind=schedule`
  triggers due now.
- Wire `tasks.periodic.evaluate_scheduled_triggers` (already on a 5-min
  cron).
- For v0, "fires" still just record events.

### Open questions
- Should `before` and `after` be the full body or a unified diff? Diff is
  cheaper and usually enough; but some triggers ("status changed") want
  enough surrounding context to reason. Default: pass diff + the changed
  paragraph(s); fall back to full body if the diff covers >50% of the
  file.
- Snapshot of `before` body — the simplest source is `wiki.git.read_file(path,
  ref="HEAD~1")` after the commit. Cleaner than holding before/after in
  memory.
- What happens when a trigger fires repeatedly on a chatty doc? v0:
  nothing. Later: per-trigger per-window debounce.
