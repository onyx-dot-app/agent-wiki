# Background tasks (Huey)

> **Part of agent-wiki v0.** See the master doc
> [`../architecture_and_progress.md`](../architecture_and_progress.md) for
> the cross-area map. This doc owns the worker container, the Huey queue,
> and every async unit of work that runs off the request path. Specific
> agent logic lives in [agents/](../agents/document-updater.md); trigger
> eval lives in
> [natural-language-triggers](../natural-language-triggers/natural-language-triggers.md).

_Last updated: 2026-05-08_

---

## Design

### Why Huey on SQLite (per V0 brief)
- Works with our existing SQLite stack — no Redis/Celery dependency.
- Separate file (`queue.sqlite`) so workers don't contend with the web
  tier on FTS writes against `app.sqlite`.
- **Three queues, one DB file, three worker containers.** See "Three
  queues" below.

### Three queues — one Huey instance each, sharing `queue.sqlite`

We split background work into three independent `SqliteHuey` instances
(see `app/tasks/huey_app.py`), namespaced inside the same `queue.sqlite`
file by `name=`. Each queue has its own consumer process so a backlog on
one queue doesn't backpressure the others.

| Queue (`name=`) | Instance | What it runs | Why it's its own queue |
|---|---|---|---|
| `documents`      | `documents_huey`      | LLM doc-reconciliation: `update_document_from_payload`, `update_document_direct`, `stale_doc_review` (cron) | Slow + LLM-bound. Keeps provider latency off the indexer / triggers paths. |
| `triggers`       | `triggers_huey`       | Trigger evaluation, both event-driven (`fan_out_trigger_eval`) and time-based (`evaluate_scheduled_triggers`, cron) | Read-only (no commits). Trigger backlog can't delay event-log entries. |
| `wiki_bm25` | `wiki_bm25_huey` | FTS5 / BM25 indexer: `reindex_path`, `reindex_document` | Cheap, no LLM. Search staleness bounded by indexer throughput alone. |

Cron tasks live on the queue that owns the work they generate:
`evaluate_scheduled_triggers` is on `triggers_huey` (same evaluator as
delta triggers, just clock-ignited); `stale_doc_review` is on
`documents_huey` (it's a doc-updater pass, same cost profile as ingest).

### Worker processes — how to run them

Three queues = **three worker processes**. They're all the same image /
venv / entry point; the queue name is a positional arg.

`run_worker.py` imports every task module so Huey's task registry is
fully populated, then launches a `Consumer` against the named queue.
Tasks bound to the other two queues are inert in this process. Per-queue
thread counts live in `_WORKERS` in `app/tasks/run_worker.py` (defaults
today: `documents=2`, `triggers=4`, `wiki_bm25=4`).

#### Docker (canonical)

`docker-compose.yml` runs three worker services from the same image:

| Service | Command |
|---|---|
| `worker-documents`      | `python -m app.tasks.run_worker documents` |
| `worker-triggers`       | `python -m app.tasks.run_worker triggers` |
| `worker-wiki-bm25` | `python -m app.tasks.run_worker wiki_bm25` |

`docker compose up --build` brings up backend + nginx + frontend + all
three workers.

#### Host run (no Docker — fast iteration)

The full stack is **backend + three workers + frontend** (five
processes). All five are long-lived, so an agent should background them.
For the backend / frontend commands and the rest of the dev story see
[running-locally.md](../running-locally.md). The worker commands:

```
cd backend
./.venv/bin/python -m app.tasks.run_worker documents       # LLM doc-updater
./.venv/bin/python -m app.tasks.run_worker triggers        # NL trigger eval (delta + scheduled)
./.venv/bin/python -m app.tasks.run_worker wiki_bm25  # FTS5 / BM25 reindex
```

Same venv as the backend. All three share `local_data/queue.sqlite`
(Huey namespaces tables by queue `name=`), so there's no per-queue
setup — just launch the right process.

#### What breaks if you skip a worker

Useful for narrow iteration; not safe for a real run.

| Skipped worker | What stops working | What still works |
|---|---|---|
| `wiki_bm25` | `documents_fts` falls behind; search results stale until you restart the worker (queued `reindex_path` calls drain on resume). | All wiki reads/writes; trigger eval. |
| `triggers`       | Trigger fires don't get evaluated; `trigger.fire` rows stop appearing in the events log; the 5-min `evaluate_scheduled_triggers` cron stops noisy stub-error logs. | All wiki reads/writes; FTS reindex. |
| `documents`      | `POST /api/documents/ingest` enqueues but nothing reconciles; the 6-hour `stale_doc_review` cron stops noisy stub-error logs. | Human edits via the UI (they don't go through this queue); FTS reindex; trigger eval. |

#### VS Code / Cursor (`.vscode/launch.json`)

Five debug configs are checked in: backend, the three workers, frontend
— plus the compound **App: backend + 3 workers + frontend** that boots
all five with one click. Worker configs:

| Config | Module + args | Drains |
|---|---|---|
| `Worker — documents (LLM doc-updater)`   | `app.tasks.run_worker documents`      | `documents_huey` — connector ingest, direct agent edits, `stale_doc_review` cron |
| `Worker — triggers (NL trigger eval)`    | `app.tasks.run_worker triggers`       | `triggers_huey` — `fan_out_trigger_eval` + 5-min `evaluate_scheduled_triggers` cron |
| `Worker — wiki_bm25 (FTS5 / BM25)`  | `app.tasks.run_worker wiki_bm25` | `wiki_bm25_huey` — `reindex_path`, `reindex_document` |

Pick the compound from the Run & Debug panel and hit ▶; five
integrated-terminal panes open. To debug just one worker, run its
config alone — the other queues will sit idle (so trigger fires won't
evaluate, the indexer won't catch up, etc.). Editor-wide caveats
(envFile loading, killing stuck dev servers) are covered in
[running-locally.md](../running-locally.md#running-from-vs-code--cursor).

### End-to-end fan-out on a wiki write

When a doc is saved (human edit, agent edit, doc-updater commit, etc.):

1. The caller commits via `app/wiki/git.py:commit_file`.
2. It enqueues `reindex_path(rel)` on `wiki_bm25_huey`.
3. It enqueues `fan_out_trigger_eval(rel, sha, change_kind, actor)` on
   `triggers_huey`. That task runs `find_matching_triggers(doc_path)`,
   which already returns triggers attached to the doc itself **and to
   every ancestor directory** (incl. the wiki root, `""`) via
   `app/wiki/filesystem.py:parent_dirs`. Each match runs phase-1
   `evaluate_delta` (or `evaluate_new_file_in_dir` for `change_kind ==
   "create"` on dir-scoped triggers) → on match, phase-2
   `render_delta_message` → write a `trigger.fire` event row.
4. Connector ingest goes through `update_document_from_payload` on
   `documents_huey` instead. When that task commits a new body, it
   re-enqueues `reindex_path` (wiki_bm25) and
   `fan_out_trigger_eval` (triggers), so the side effects fan out
   exactly like a human edit.

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

| Task | Queue | Status | Trigger |
|---|---|---|---|
| `tasks.reindex.reindex_document(doc_id, path, title)` | `wiki_bm25` | working | (legacy form; called by document_update once it's wired) |
| `tasks.reindex.reindex_path(path)`                    | `wiki_bm25` | working | preferred form — derives title from `# heading`; used by the wiki write path |
| `tasks.triggers.fan_out_trigger_eval`                 | `triggers`       | working | post-commit fan-out from the wiki write path / agent edit tools |
| `tasks.document_update.update_document_from_payload`  | `documents`      | stub    | inbound ingest from Onyx/webhooks |
| `tasks.document_update.update_document_direct`        | `documents`      | stub    | agent PUTs a doc directly through the API |
| `tasks.periodic.evaluate_scheduled_triggers`          | `triggers`       | stub    | every 5 min |
| `tasks.periodic.stale_doc_review`                     | `documents`      | stub    | every 6 hours |

### Tasks to add

| Task | Queue | Notes |
|---|---|---|
| `tasks.events.record(...)` | (TBD) | Convenience shim if we want to enqueue audit-log writes (probably not needed; sync write is fine) |

### Concurrency on the wiki repo
- Git writes (commits, moves) happen on the `documents` worker (LLM
  doc-updater commits) and from web-tier request handlers (human edits,
  agent tool edits). The `triggers` and `wiki_bm25` workers are
  read-only against git, so they don't contend on writes — but they
  *do* read while another process may be writing.
- If we add a second `documents` worker (or commit from elsewhere), we
  need a per-repo write lock. Options:
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
- Three Huey queues configured (`documents_huey`, `triggers_huey`,
  `wiki_bm25_huey`), all `SqliteHuey` instances sharing
  `queue.sqlite` via `name=` namespacing.
- Worker entry point (`run_worker.py <queue>`); per-queue worker counts
  in `_WORKERS`.
- `docker-compose.yml` runs three worker services
  (`worker-documents`, `worker-triggers`, `worker-wiki-bm25`).
- `reindex_document` and `reindex_path` are real and produce FTS rows.
- `fan_out_trigger_eval` is real (see `natural-language-triggers/`).

### Stubbed
- All `document_update` tasks (`update_document_from_payload`,
  `update_document_direct`).
- All `periodic` tasks (`evaluate_scheduled_triggers`, `stale_doc_review`).

### Next up
1. **`tasks.document_update.update_document_from_payload`** — see
   `agents/document-updater.md` next-up list. After commit, re-enqueue
   `reindex_path` (wiki_bm25) and `fan_out_trigger_eval` (triggers).
2. **Periodic stubs** — implement `evaluate_scheduled_triggers` once
   `triggers/time_based.py:due_triggers` is real.
3. **Test pattern** — set `<queue>_huey.immediate = True` in fixtures
   (e.g. `triggers_huey.immediate = True` for fan-out tests) and call
   tasks synchronously to assert side effects (FTS row, events row).

### Open questions
- Per-doc lock to prevent two ingest tasks racing on the same path?
  Cheap: `(SELECT … FOR UPDATE)` style guard via a `doc_locks` table, or
  a fixed-key `flock`. Defer until we see contention.
- Multi-worker scaling — when we add more workers, see "Concurrency on
  the wiki repo" above.
