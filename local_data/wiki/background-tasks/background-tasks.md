# Background tasks (Huey)

> **Part of agent-wiki v0.** See the master doc
> [`../architecture_and_progress.md`](../architecture_and_progress.md) for
> the cross-area map. This doc owns the worker container, the Huey queue,
> and every async unit of work that runs off the request path. Specific
> agent logic lives in [agents/](../agents/document-updater.md); trigger
> eval lives in
> [natural-language-triggers](../natural-language-triggers/natural-language-triggers.md).

_Last updated: 2026-05-06_

---

## Design

### Why Huey on SQLite (per V0 brief)
- Works with our existing SQLite stack — no Redis/Celery dependency.
- Separate file (`queue.sqlite`) so workers don't contend with the web
  tier on FTS writes against `app.sqlite`.
- One worker container today (`python -m app.tasks.run_worker`).

### Architectural rules (also in CLAUDE.md)
- **Anything that might take >100ms goes in a task.** Web requests should
  not block on LLM calls or git operations beyond what's strictly
  necessary.
- **New task modules must be imported by `app/tasks/run_worker.py`** (or
  transitively) so they register on boot.
- **Tasks are idempotent.** Huey can retry. If you can't make it
  idempotent, gate with a "did we already do this" check (e.g. a row in
  `events` keyed on a deterministic id).
- **Catch and surface errors.** A task that swallows an `LLMError` is
  worse than one that records it as an event of kind `<thing>.failed`.

### Tasks today

| Task | Status | Trigger |
|---|---|---|
| `tasks.reindex.reindex_document(doc_id, path, title)` | working | (legacy form; called by document_update once it's wired) |
| `tasks.reindex.reindex_path(path)`                    | working | preferred form — derives title from `# heading`; used by the new wiki write path |
| `tasks.document_update.update_document_from_payload`  | stub    | inbound ingest from Onyx/webhooks |
| `tasks.document_update.update_document_direct`        | stub    | agent PUTs a doc directly through the API |
| `tasks.periodic.evaluate_scheduled_triggers`          | stub    | every 5 min |
| `tasks.periodic.stale_doc_review`                     | stub    | every 6 hours |

### Tasks to add

| Task | Notes |
|---|---|
| `tasks.triggers.fan_out_trigger_eval(doc_path, before, after, change_kind)` | Post-commit; evaluates matching delta triggers + records `trigger.fire` events |
| `tasks.triggers.fan_out_directory_new_file(dir_path, doc_path)`             | Variant for "new file" change kind on directory triggers |
| `tasks.events.record(...)`                                                  | Convenience shim if we want to enqueue audit-log writes (probably not needed; sync write is fine) |

### Concurrency on the wiki repo
- Git operations are serialized inside one worker process today.
- If we move to multi-worker, we need a per-repo lock. Options:
  - SQLite advisory lock row (cheap; transactional).
  - `flock` on a sentinel file in the wiki dir.
- Don't address until we hit the problem; flag in `architecture_and_progress.md`.

### Cron / time-based checks
Huey's `periodic_task(crontab(...))` is already used for the scheduled
trigger evaluator. Per V0 brief — these are a v0 requirement, but the
implementation hooks (`due_triggers`, `evaluate_scheduled_triggers`)
remain stubs.

### Failure handling
- LLM-call tasks: catch `LLMError`, write `<thing>.failed` event with
  the `code`, do **not** raise — Huey will retry forever on raise unless
  configured otherwise, and we don't want repeated provider 401s.
- Git failures (e.g. concurrent write race): raise, let Huey retry; a
  short backoff is fine for v0.

---

## Progress

### Working
- Huey configured (`SqliteHuey`, queue path from `CONFIG.queue_db_path`).
- Worker entry point (`run_worker.py`, 2 thread workers).
- `reindex_document` and `reindex_path` are real and produce FTS rows.

### Stubbed
- All `document_update` and `periodic` tasks.
- No `triggers` task module yet.

### Next up
1. **Wire the wiki write path** to enqueue `reindex_path` after every
   `commit_file`. (Web tier change; trivial once `PUT /documents/file`
   lands.)
2. **`tasks.document_update.update_document_from_payload`** — see
   `agents/document-updater.md` next-up list.
3. **`tasks.triggers.fan_out_trigger_eval`** — see
   `natural-language-triggers/`.
4. **Periodic stubs** — implement `evaluate_scheduled_triggers` once
   `triggers/time_based.py:due_triggers` is real.
5. **Test pattern** — set `huey.immediate = True` in fixtures and
   call tasks synchronously to assert side effects (FTS row, events row).

### Open questions
- Per-doc lock to prevent two ingest tasks racing on the same path?
  Cheap: `(SELECT … FOR UPDATE)` style guard via a `doc_locks` table, or
  a fixed-key `flock`. Defer until we see contention.
- Multi-worker scaling — when we add more workers, see "Concurrency on
  the wiki repo" above.
