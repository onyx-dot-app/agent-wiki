# Natural-Language Triggers

> **Part of agent-wiki v0.** See the master doc
> [`../architecture_and_progress.md`](../architecture_and_progress.md) for
> the cross-area map. This doc owns trigger storage (git-backed YAML +
> SQLite cache), CRUD API, matching engine, NL evaluation, and the
> post-commit fan-out that fires them. Trigger UI lives in
> [frontend/frontend.md](../frontend/frontend.md). Outbound dispatch
> (webhooks / external services / agent messages) is **deferred** — see
> the TBD callout in `../architecture_and_progress.md` §1.

_Last updated: 2026-05-08_

---

## Design

### Ownership and visibility — per-user (v0)

Every trigger has an **owner** (`owner_user_id`). Visibility, edit rights,
and event delivery are **all gated on ownership**:

- The trigger's owner is the only one who can **see** it (in the
  Triggers tab, in the inline panels on doc/dir pages, anywhere).
- The trigger's owner is the only one who can **edit / disable / delete** it.
- A trigger fires when its scoped path changes **regardless of which user
  edited the doc** — but the resulting `events` row is owned by the
  trigger's user, and the Events tab is owner-scoped.
- Two users with the same NL description on the same path are
  **independent** triggers — each evaluates and emits its own event.

This means every read in `app/api/triggers.py` is filtered by
`owner_user_id = current_user.id`; every write stamps the current user.

**Sharing / collaboration** (multi-user triggers, group ownership, "see
team triggers") is **backlog** — see below. Don't implement in v0.

### Required fields — both `nl_description` and `message` (2026-05-08)

Every saved trigger must carry **both** a non-empty firing condition
(`nl_description`, the **if**) and a non-empty fire message (`message`,
the **what**). Half-configured triggers are not persisted.

The invariant is enforced at the repo layer (`app/triggers/repo.py:create`
and `update` raise `ValueError`). The HTTP API and both LLM agent tools
(`create_trigger`, `update_trigger`) translate that into a 400 / `{error}`
response, so a partially-filled call gets a clear rejection instead of a
silently-saved zombie row.

`repo.purge_invalid_triggers()` runs once on app startup (in
`main.create_app`, before `rebuild_from_filesystem`) and removes any YAML
files in the wiki repo that pre-date this rule. The deletion is a normal
git commit, so the history is preserved.

### Two trigger kinds (schema already supports both)

| `kind` | Fires when… | v0? |
|---|---|---|
| `delta`    | A doc within `scope_path` changes (or new file added in a directory scope) | **yes** |
| `schedule` | Cron matches | wire up in `time_based.py`; v0 still record-only |

### Storage: file-system as source of truth, SQLite as cache (2026-05-07)

A trigger's YAML file in the wiki repo is canonical. SQLite mirrors it for
fast fan-out lookup and id→path resolution. This re-litigates the
2026-05-06 SQLite-only call so trigger config has git history.

**Layout** (per `../difficult_separable_work.md`): the file sits inside
the directory it acts on — no centralized `.triggers/` dir.

| scope        | filename                                | example                              |
|---           |---                                      |---                                   |
| doc          | `.trigger_<id>_<docbase>.yaml`          | `projects/.trigger_trg_ab12_foo.yaml` |
| folder       | `.trigger_<id>.yaml`                    | `projects/.trigger_trg_cd34.yaml`     |

The doc-suffix (`_<docbase>`) is a human hint; the canonical id lives in
the YAML. `kind_of_scope` is heuristic on the scope path: `*.md` → doc,
otherwise dir.

The trigger boundary accepts a leading `/` (or a bare `/`) as a synonym
for the wiki root and collapses it to `""` before storage —
`storage.normalize_scope_path` is the entry point for the API and both
agent tools, so the rest of the wiki path utilities never see an
absolute path.

**File contents:**

```yaml
id: trg_ab12cd34
owner_user_id: usr_…
scope_path: projects/foo.md       # or a directory: projects
kind: delta                       # schedule still deferred
nl_description: |
  Fire when this project's status changes from green to yellow.
enabled: true
created_at: 2026-05-07T...
```

**Mutation order** (`app/triggers/repo.py`): write/delete the file
first, then upsert the SQLite row. If the row write fails after the file
commit, `repo.rebuild_from_filesystem()` re-converges the cache by
walking `git ls-files` for `.trigger_*.yaml`.

**Scope changes** rename the file (delete old path, write new) so the
filename stays a useful hint.

`migrations/0003_triggers_file_path.sql` adds the `file_path` column on
the `triggers` cache row.

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
  AND scope_path IN (?, ?, ...)   -- doc_path + parent_dirs (incl. ""=root)
```

### NL evaluation — two phases

A trigger has two natural-language fields: an **if** (`nl_description` —
when to fire) and a **message** (what to deliver). Each gets its own
LLM call. Both calls share the same context payload built by
`app/triggers/diff.py:build_payload`:

1. The whole wiki at its latest version (`=== WIKI (latest version) ===`,
   each `.md` file with its current body, bounded by per-doc and total
   character budgets).
2. The change view (`=== CHANGE ===`), which is a unified `+/-` diff
   for edits, the full body for new files, or both bodies side-by-side
   for high-density rewrites.

**Phase 1** — `app/triggers/natural_language.py:matches(nl_description,
payload)` runs one `client.complete()` call with a `report` tool that
returns:

```json
{ "matches": true, "reason": "Status table flipped from green to yellow." }
```

Hard rules (in the prompt):
- **Diff-first.** Triggers should typically be evaluated against the
  diff. Only evaluate against overall current state when the description
  is clearly about state ("when status is yellow") rather than an update
  ("when status flips to yellow"). When in doubt, evaluate the diff.
- **Conservative.** False positives are louder than false negatives in v0.
- **Cite the change.** The `reason` should quote or paraphrase what changed.
- **No outside knowledge.** Only what's in the payload.

**Phase 2** — `render_message(message_instruction, payload, *, reason)`
runs only on a phase-1 match. It uses a `render` tool to compose the
final notification text from the owner's instruction, grounded in the
same payload plus the phase-1 reason. Plain text or markdown out, no
meta-commentary. On LLM error or missing tool call, we fall back to the
raw instruction so the Event Log still receives something the owner
authored.

#### New-file-in-dir variant (single combined call)

When a directory-scoped trigger fires on a `change_kind = "create"` —
i.e. a brand-new file appears under the scoped directory — the standard
diff payload is misleading: every line of the new file would be a `+`
line, with nothing on the BEFORE side. Showing it as a diff just makes
the model re-read the same body twice.

For this case we use a different payload and a single LLM call:

* **Payload** — `app/triggers/diff.py:build_new_file_payload` returns
  the wiki snapshot followed by a `=== NEW FILE ===` block with the
  path and the file's full body. No diff section.
* **Call** — `app/triggers/natural_language.py:evaluate_new_file_in_dir`
  combines the firing-condition check and the message render into one
  `client.complete()` call. The model is asked to emit a single JSON
  object as its entire response:

  ```json
  {"triggered": true, "trigger_message": "..."}
  ```

  We strip optional markdown fences and tolerate stray prose around the
  object, but require valid JSON. On parse failure or LLM error we drop
  the fire (`(False, "")`) — better than sending a confused message. If
  the model says `triggered=true` with a blank message, we fall back to
  the owner's raw instruction.

  Routing logic lives in `tasks/triggers.py:fan_out_trigger_eval`:
  `change_kind == "create"` AND `trigger.scope_path != doc_path` →
  new-file-in-dir path; everything else → standard two-phase path.

### Trigger evaluation flow
1. A doc commit lands (via API or the editor's Save).
2. Worker enqueues reindex + trigger evaluation.
3. For the changed file path **and each parent directory**, load enabled
   triggers from cache.
4. Build the wiki snapshot **once** per fan-out (same context for every
   trigger on a given commit) and combine it with the change view into
   a single payload.
5. For each trigger, run phase 1 (`matches`); on match, run phase 2
   (`render_message`) to produce the delivered text.
6. Write a `trigger.fire` event with the rendered message and the raw
   instruction (kept for audit / re-render later).
7. **No external dispatch in v0.** Surface in the Events tab.

### Fan-out (post-commit)

`tasks/triggers.py:fan_out_trigger_eval(doc_path, sha, change_kind, actor)`
runs **after** every successful `commit_file` (see
[background-tasks](../background-tasks/background-tasks.md)):

1. `find_matching_triggers(doc_path)` (SQL over the cache, includes parent dirs).
2. Read BEFORE/AFTER from git at `{sha}^`/`{sha}`.
3. `diff.build_wiki_snapshot()` once, then `diff.build_payload(...)` and
   (when `change_kind == "create"`) `diff.build_new_file_payload(...)`.
4. For each trigger, route by case:
   * Directory-scoped + create →
     `engine.evaluate_new_file_in_dir(trigger, instruction, new_file_payload)`
     (single JSON-output call returning `(triggered, trigger_message)`).
   * Otherwise → `engine.evaluate_delta(trigger, payload)` and, on match,
     `engine.render_delta_message(instruction, payload, reason=...)`.
5. Insert one `trigger.fire` row carrying
   `{trigger_id, doc_path, sha, change_kind, reason, message,
   message_instruction, destination}`.

### Cost (watch this)
- Every doc commit × every matching trigger = **two** LLM calls on a
  match (phase 1 always; phase 2 only when matched).
- Mitigate with: tight prompts, small models (`claude-haiku-*` is fine for
  both phases), and the per-doc / total-wiki budgets in `diff.py` to keep
  the payload bounded.
- Track per-fire input/output tokens in the event payload so we can
  compute spend later.

### File-by-file (current state)

- `app/triggers/storage.py` — `compute_path` (inline-next-to-scope layout),
  `serialize`/`parse` (PyYAML), `write_trigger`/`delete_trigger`/
  `read_trigger` (via `wiki.git`), `list_all_files` (walks tracked
  paths for `.trigger_*.yaml`).
- `app/triggers/repo.py` — file-first mutation: `create`/`update`/`delete`
  write/delete the YAML, then upsert/delete the SQLite row.
  `rebuild_from_filesystem` for crash recovery / boot reconciliation.
- `app/triggers/engine.py` — `find_matching_triggers` (SQL over `triggers`
  cache, includes parent dirs), plus thin wrappers `evaluate_delta`
  (phase 1, calls `natural_language.matches`), `render_delta_message`
  (phase 2, calls `natural_language.render_message`), and
  `evaluate_new_file_in_dir` (single combined call for the new-file-in-dir
  case). `dispatch` left stubbed (deferred).
- `app/triggers/natural_language.py` — three entry points.
  Standard path: `matches(nl_description, payload)` →
  `(matched, reason)` via the `report` tool, and
  `render_message(instruction, payload, *, reason)` → delivered text via
  the `render` tool (falls back to the raw instruction on LLM error or
  missing tool call). New-file-in-dir path:
  `evaluate_new_file_in_dir(nl_description, instruction, payload)` →
  `(triggered, trigger_message)` parsed from a JSON object emitted as
  the assistant's text content (no tool call). System prompts include
  diff-first guidance and explicit JSON-only rules.
- `app/triggers/diff.py` — `build_wiki_snapshot()` concatenates every
  tracked `.md` doc (per-doc cap 16KB, total cap 200KB).
  `build_change_view(...)` produces the `=== CHANGE ===` block: unified
  diff for edits, full body for creates, both bodies side-by-side for
  high-density rewrites. `build_new_file_view(...)` /
  `build_new_file_payload(...)` produce the `=== NEW FILE ===` variant
  for the directory-scoped-on-create case (path + body, no diff).
  `build_payload(...)` glues snapshot + change view into one string.
- `app/tasks/triggers.py:fan_out_trigger_eval` — Huey task wired into the
  human-edit path (`api/documents.py:put_document_by_path` after
  `commit_file`). Reads BEFORE from `sha^`, AFTER from `sha`, writes
  `trigger.fire` events on match. Registered in `run_worker.py`.
- `app/triggers/time_based.py:due_triggers` — stub.
- `app/api/triggers.py` — `GET /` (owner-scoped list), `POST /`, `PUT /<id>`,
  `DELETE /<id>`, `GET /<id>/history` (git log on the YAML file). Threads
  the current user as the git author.

### Out of scope (do not build)
- Outbound webhooks / HTTP calls / agent messages on fire.
- Ambient UI surfacing (badges/toasts) — Events tab only.
- Editing triggers' `action_json` from the UI — v0 has no actions.

---

## Progress

### Working
- Schema in place (migrations `0001_init.sql`, `0003_triggers_file_path.sql`):
  `triggers` table with all needed columns including `file_path`; `events`
  table for fires.
- `wiki.filesystem.parent_dirs` — used for ancestor lookup.
- `wiki.git.commit_file` / `delete_path` / `history` — used for YAML
  mutations and trigger config history.
- LLM client + `LLMError` taxonomy — usable from `matches`.
- **Fire-path on human edits** (post-commit fan-out): `engine.find_matching_triggers`,
  `engine.evaluate_delta`, `natural_language.matches`, `diff.build_payload`,
  and `tasks.triggers.fan_out_trigger_eval` are all live and tested
  (`backend/tests/test_triggers_*`). Triggered from
  `api/documents.py:put_document_by_path` after `commit_file`.
- **Trigger CRUD on git-backed YAML** — `storage.write_trigger` / `read_trigger`
  / `delete_trigger` / `list_all_files`; `repo.create` / `update` / `delete`
  / `rebuild_from_filesystem`; `api/triggers.py` endpoints including
  `GET /<id>/history`.

### Stubbed, not wired
- `app/triggers/time_based.py:due_triggers`.
- `engine.dispatch` — deferred per v0 scope (no outbound action).

### Boot-time housekeeping (2026-05-08)
- `app/main.py:create_app` calls `repo.purge_invalid_triggers()` (drops
  YAML files missing a non-empty firing condition or fire message), then
  `repo.rebuild_from_filesystem()` to re-converge the SQLite cache from
  the surviving files.

---

## Work breakdown (Next up)

### D. Triggers — remaining
- **Triggers UI** — owned by [frontend](../frontend/frontend.md); inline
  on the doc and directory pages. Surfaces `GET /<id>/history` for
  per-trigger config history.

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

### Backlog
- **Shareable triggers.** Today every trigger is private to its owner.
  Future: let users opt a trigger into being visible to a group / the
  whole org, with one event row per recipient or shared event rows.
  Open design questions when this is picked up: ownership semantics on
  edit/delete (owner-only or any viewer?), how shared triggers interact
  with the per-user Events tab, and whether sharing is per-trigger or
  per-scope.
- Outbound dispatch (webhooks / HTTP / agent messages) — the
  long-deferred trigger-extensions item; see the master TBD callout.
- Per-trigger debounce / dedup window for chatty docs.
- Better UI for cross-cutting trigger scopes (e.g. "all docs under
  `projects/`").
- ~~YAML/git-backed trigger source-of-truth — a re-litigation of the v0
  decision once we want trigger history beyond what SQLite gives us.~~
  **Done 2026-05-07** (see Storage section).
