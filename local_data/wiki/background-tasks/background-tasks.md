# Background tasks (pgmq)

> **Part of agent-wiki v0.** See the master doc
> [`../architecture_and_progress.md`](../architecture_and_progress.md) for
> the cross-area map. This doc owns the worker container, the pgmq queues,
> and every async unit of work that runs off the request path. Specific
> agent logic lives in [agents/](../agents/document-updater.md); trigger
> eval lives in
> [natural-language-triggers](../natural-language-triggers/natural-language-triggers.md).

_Last updated: 2026-05-09_

---

## Design

### Why pgmq on the app's own Postgres
- App state already lives in Postgres — adding a queue extension is one
  more `CREATE EXTENSION` and zero new infrastructure (no Redis, no
  separate broker).
- pgmq's visibility-timeout model gives us at-least-once delivery with
  trivial retry semantics. The decorator-shaped surface
  (`@task()`, `.schedule(args=, eta=)`, `immediate=True`) lives in
  `app/tasks/queue.py`.
- **Three queues, three worker containers.** See "Three queues" below.

### Three queues — one pgmq queue per logical lane

We split background work into three independent `TaskQueue` instances
(see `app/tasks/queues.py`), backed by `pgmq.q_documents`,
`pgmq.q_triggers`, and `pgmq.q_wiki_bm25`. Each queue has its own
consumer process so a backlog on one queue doesn't backpressure the
others.

| Queue | TaskQueue | What it runs | Why it's its own queue |
|---|---|---|---|
| `documents`      | `documents_queue`      | LLM-bound work: `update_document_from_payload`, `agent_update_document_nl`, `generate_chat_title` | Slow + LLM-bound. Keeps provider latency off the indexer / triggers paths. |
| `triggers`       | `triggers_queue`       | Trigger evaluation, both event-driven (`fan_out_trigger_eval`) and time-based (`evaluate_scheduled_triggers`, cron) | Read-only (no commits). Trigger backlog can't delay event-log entries. |
| `wiki_bm25` | `wiki_bm25_queue` | BM25 indexer: `reindex_path`, `reindex_document` | Cheap, no LLM. Search staleness bounded by indexer throughput alone. |

Cron tasks live on the queue that owns the work they generate:
`evaluate_scheduled_triggers` is on `triggers_queue` (same evaluator as
delta triggers, just clock-ignited).

### Worker processes — how to run them

Three queues = **three worker processes**. They're all the same image /
venv / entry point; the queue name is a positional arg.

`run_worker.py` imports every task module so the per-queue handler
registries are fully populated, then calls `run_consumer` against the
named queue. Tasks bound to the other two queues are inert in this
process. Per-queue **handler concurrency** (= number of worker threads
in the process, each running its own `pgmq.read(qty=1)` poll loop)
lives in `_CONCURRENCY` in `app/tasks/run_worker.py` (defaults today:
`documents=1`, `triggers=4`, `wiki_bm25=4`). To scale beyond a single
host, run more replicas of the worker — pgmq's visibility timeout
serializes per-message redelivery, so multiple replicas pulling from
the same queue is safe. **Periodic tasks** are scheduled by an
in-process thread inside the worker, but the scheduler holds a
per-queue advisory lock (`pg_try_advisory_lock` on
`hashtext('queue-cron:<name>')`) so only one replica fires crons; the
others idle until the leader's connection drops, then race for the
lock. So scaling `worker-triggers` to N replicas is fine — only one of
them runs the scheduler at a time.

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
./.venv/bin/python -m app.tasks.run_worker wiki_bm25  # BM25 reindex
```

Same venv as the backend. All three read/write their own pgmq queue in
the app's Postgres (`pgmq.q_<name>`); the `pgmq.create()` calls in `init_db` creates
the queues on first `init_db()`, so there's no per-queue setup — just
launch the right process.

#### What breaks if you skip a worker

Useful for narrow iteration; not safe for a real run.

| Skipped worker | What stops working | What still works |
|---|---|---|
| `wiki_bm25` | `documents_fts` falls behind; search results stale until you restart the worker (queued `reindex_path` calls drain on resume). | All wiki reads/writes; trigger eval. |
| `triggers`       | Trigger fires don't get evaluated; `trigger.fire` rows stop appearing in the events log; the 5-min `evaluate_scheduled_triggers` cron stops noisy stub-error logs. | All wiki reads/writes; FTS reindex. |
| `documents`      | `POST /api/documents/ingest` enqueues but nothing reconciles. | Human edits via the UI (they don't go through this queue); FTS reindex; trigger eval. |

#### VS Code / Cursor (`.vscode/launch.json`)

Five debug configs are checked in: backend, the three workers, frontend
— plus the compound **App: backend + 3 workers + frontend** that boots
all five with one click. Worker configs:

| Config | Module + args | Drains |
|---|---|---|
| `Worker — documents (LLM doc-updater)`   | `app.tasks.run_worker documents`      | `documents_queue` — connector ingest, direct agent edits |
| `Worker — triggers (NL trigger eval)`    | `app.tasks.run_worker triggers`       | `triggers_queue` — `fan_out_trigger_eval` + 5-min `evaluate_scheduled_triggers` cron |
| `Worker — wiki_bm25 (BM25)`  | `app.tasks.run_worker wiki_bm25` | `wiki_bm25_queue` — `reindex_path`, `reindex_document` |

Pick the compound from the Run & Debug panel and hit ▶; five
integrated-terminal panes open. To debug just one worker, run its
config alone — the other queues will sit idle (so trigger fires won't
evaluate, the indexer won't catch up, etc.). Editor-wide caveats
(envFile loading, killing stuck dev servers) are covered in
[running-locally.md](../running-locally.md#running-from-vs-code--cursor).

### End-to-end fan-out on a wiki write

Every successful wiki `.md` mutation flows through the **post-write
seam** at `app/wiki/notify.py` — the single place that owns reindex +
trigger fan-out. Three entry points:

| Function | Used by | Effect |
|---|---|---|
| `after_doc_write(rel, sha, change_kind, actor)` | UI save (`PUT /api/documents/file`), agent `edit_doc` / `multi_edit` / `write_doc` | enqueue `reindex_path(rel)` on `wiki_bm25_queue` + `fan_out_trigger_eval(rel, sha, change_kind, actor)` on `triggers_queue` |
| `after_doc_delete(rel, sha, actor)` | UI delete (`DELETE /api/documents/file`) | inline `fts.delete_document(rel)` + fan out with `change_kind="delete"` |
| `after_path_move(moves, sha, actor)` | UI move (`POST /api/documents/move`), agent `move_path` | for each `(old, new)` pair: drop old FTS row + reindex new + fan out a `delete` on old and a `create` on new |

`fan_out_trigger_eval` runs `find_matching_triggers(doc_path)`, which
returns triggers attached to the doc itself **and every ancestor
directory** (incl. the wiki root `""`) via
`app/wiki/filesystem.py:parent_dirs`. Each match runs phase-1
`evaluate_delta` (or `evaluate_new_file_in_dir` for `change_kind=="create"`
on dir-scoped triggers) → on match, phase-2 `render_delta_message` →
write a `trigger.fire` event row.

Connector ingest is the exception: it goes through
`update_document_from_payload` on `documents_queue`. When that task
eventually commits a new body, it should call `after_doc_write` so the
side effects fan out exactly like a human edit. (Stub today — see
[Status](#status).)

Trigger YAMLs (`.trigger_*.yaml`) deliberately bypass the seam — they're
trigger config, not docs. `app/triggers/storage.py` calls `commit_file`
directly and never `wiki/notify.py`.

### Architectural rules (also in CLAUDE.md)
- **Anything that might take >100ms goes in a task.** Web requests should
  not block on LLM calls or git operations beyond what's strictly
  necessary.
- **New task modules must be imported by `app/tasks/run_worker.py`** (or
  transitively) so they register on boot.
- **Tasks are idempotent.** pgmq can retry. If you can't make it
  idempotent, gate with a "did we already do this" check (e.g. a row in
  `events` keyed on a deterministic id).
- **Catch and surface errors.** A task that swallows an `LLMError` is
  worse than one that records it as an event of kind `<thing>.failed`.

### Bounded backlog — `MAX_QUEUE_SIZE`

Each `TaskQueue` (in `app/tasks/queue.py`) holds a per-queue
`pg_advisory_xact_lock` and counts pending + future-scheduled rows
(`read_ct = 0 OR vt <= now()`) before every `pgmq.send`. If the count
is at the cap, it raises `QueueFullError`. The cap is shared across
all three queues and configured via `MAX_QUEUE_SIZE` (default
**1000**, positive integer).

In-flight messages (a worker has read them and the VT hasn't expired)
are intentionally excluded from the count — otherwise a slow consumer
locks out producers entirely. The advisory lock makes the count + send
atomic *per queue*, so concurrent producers can't all read `999` and
then all insert. The lock is per-queue so the three queues stay
independent; contention is bounded by the time of one INSERT.

The Flask app registers a global error handler for `QueueFullError`
that returns a 503 with `{error, queue, size, limit}`, so any route
that enqueues (e.g. `POST /api/documents/ingest`,
`POST /api/documents/reindex`, the wiki write paths via
`wiki/notify.py`) gets a clear failure message without per-route
try/except.

### Retries — exponential backoff via `pgmq.set_vt`

When a handler raises, the consumer pushes the message's visibility
timeout out by `min(600, 30 * 2^(read_ct-1))` seconds via
`pgmq.set_vt`, so the first retry waits 30s, the next 60s, then 120s,
240s, 480s, capped at 600s. After `MAX_RETRIES` (default 3)
redeliveries the message is `pgmq.archive`d. The exponential backoff
matters because transient errors get retried fast and persistently
broken handlers are archived instead of looping forever.

### Graceful shutdown

`run_consumer` installs a SIGTERM / SIGINT handler that sets a
`threading.Event`. Each worker thread checks the event between polls
and exits cleanly when it's set; in-flight handlers run to completion.
The `ThreadPoolExecutor` joins all workers before `run_consumer`
returns. Messages that are mid-flight at SIGKILL still hold their VT
until it expires — that's pgmq's at-least-once contract, not a bug.

### Periodic scheduler — leader election + persisted last-fired

Each worker process spawns a scheduler thread *only if* its queue has
periodic tasks registered. The thread opens a dedicated SQLAlchemy
connection and tries `pg_try_advisory_lock(hashtext('queue-cron:<name>'))`.
If acquired, the connection (and lock) is held for the lifetime of
leadership; if not, the thread sleeps 30s and retries. The lock
auto-releases when the process dies or the connection drops, so
another replica picks up leadership without manual intervention.

Each fire is recorded in the `cron_state` table
(`(queue_name, task_name, last_fired_at)`); on startup the leader
seeds `next_fire = croniter(spec, last_fired or now).get_next()`. If
the scheduler was down across multiple cron windows, `next_fire` will
be in the past — the loop fires *once* and then advances from `now`,
so a long downtime collapses to a single catch-up fire instead of a
stampede.

### Healthcheck — `GET /api/health`

Reports overall liveness plus per-queue `{name, size, limit, ok}` so
the frontend `/health` page (and any external probe) can read backlog
straight from Postgres. `size` matches the cap-check definition (pending
+ future-scheduled, not in-flight), so a deep "size" really does mean
producers are outpacing consumers.

### Tasks today

| Task | Queue | Status | Trigger |
|---|---|---|---|
| `tasks.reindex.reindex_path(path)`                    | `wiki_bm25` | ✅ working | enqueued by `wiki/notify.py:after_doc_write` and `after_path_move`; also by `POST /api/documents/reindex` |
| `tasks.reindex.reindex_document(doc_id, path, title)` | `wiki_bm25` | ✅ working | legacy form; kept for the doc-updater path once it lands |
| `tasks.triggers.fan_out_trigger_eval`                 | `triggers`  | ✅ working | enqueued by `wiki/notify.py` for write / delete / move; runs delta + new-file-in-dir flows |
| `tasks.document_update.update_document_from_payload`  | `documents` | 🛑 stub   | inbound ingest from Onyx / webhooks |
| `tasks.document_update.agent_update_document_nl`        | `documents` | 🛑 stub   | agent PUTs a doc directly through the API (not via the chat-tool path) |
| `tasks.periodic.evaluate_scheduled_triggers`          | `triggers`  | 🛑 stub   | every 5 min — depends on `triggers/time_based.py:due_triggers` |

### Concurrency on the wiki repo
- Git writes (commits, moves) happen on the `documents` worker (LLM
  doc-updater commits) and from web-tier request handlers (human edits,
  agent tool edits). The `triggers` and `wiki_bm25` workers are
  read-only against git, so they don't contend on writes — but they
  *do* read while another process may be writing.
- If we add a second `documents` worker (or commit from elsewhere), we
  need a per-repo write lock. Options:
  - Postgres `pg_advisory_lock` (cheap; transactional; we already use
    advisory locks in `app/tasks/queue.py` for queue caps and scheduler
    leadership, so the pattern is in-house).
  - `flock` on a sentinel file in the wiki dir.
- Don't address until we hit the problem; flag in `architecture_and_progress.md`.

### Cron / time-based checks
Our `periodic_task(crontab(...))` decorator (mirrors pgmq's API,
implemented on top of `croniter` in `app/tasks/queue.py`) is already
used for the scheduled trigger evaluator. A scheduler thread inside
the worker process that owns each periodic enqueues the task at the
cron-computed time. Per V0 brief — these are a v0 requirement, but
the implementation hooks (`due_triggers`,
`evaluate_scheduled_triggers`) remain stubs.

### Failure handling
- LLM-call tasks: catch `LLMError`, write `<thing>.failed` event with
  the `code`, do **not** raise — pgmq will redeliver up to
  `MAX_RETRIES` (3) times before archiving, and we don't want repeated
  provider 401s burning retries.
- Git failures (e.g. concurrent write race): raise. The visibility
  timeout will expire and pgmq will redeliver; after `MAX_RETRIES`
  the message is moved to `pgmq.a_<name>` and a `ERROR` log entry
  identifies it.

---

## Status

### ✅ Done

- **Three queues + three workers wired up.**
  - `documents_queue`, `triggers_queue`, `wiki_bm25_queue` all configured
    in `app/tasks/queues.py` as `TaskQueue` instances backed by
    `pgmq.q_documents`, `pgmq.q_triggers`, `pgmq.q_wiki_bm25` in the
    app's Postgres.
  - `app/tasks/run_worker.py <queue>` is the entry point; per-queue
    batch sizes in `_WORKERS` (`documents=1`, `triggers=4`, `wiki_bm25=4`).
  - `docker-compose.yml` runs `worker-documents`, `worker-triggers`,
    `worker-wiki-bm25` from the same image with different commands.
- **BM25 indexer end-to-end.** `reindex_path` / `reindex_document`
  populate `documents_fts`; `search_wiki` returns hits.
  Lock-in tests in `tests/test_bm25_indexer_e2e.py`.
- **Trigger fan-out end-to-end.** `fan_out_trigger_eval` runs the SQL
  match (incl. parent dirs + root scope), routes to delta or new-file
  flow, writes `trigger.fire` rows to `events`.
  Tests in `tests/test_triggers_fanout.py` + `tests/test_save_to_fire_e2e.py`.
- **Single post-write seam.** `app/wiki/notify.py` owns reindex +
  fan-out; both API handlers and chat-agent tools route through it. See
  [seams.md](../seams.md) (`Post-write notify`).
- **Save-button → event row** flow is live and covered end-to-end:
  - UI save (`PUT /api/documents/file`) → `wiki/notify.py:after_doc_write`
  - UI delete (`DELETE /api/documents/file`) → `after_doc_delete`
  - UI move / drag-and-drop (`POST /api/documents/move`) → `after_path_move`
  - Chat-agent edits (`edit_doc`, `multi_edit`, `write_doc`, `move_path`) → same seam
- **Manual reindex.** `POST /api/documents/reindex` enqueues
  `reindex_path` for ops use.

### 🛑 TODO — stubs to implement

1. **`tasks.document_update.update_document_from_payload`** (queue:
   `documents`). Connector ingest from Onyx / webhooks. After the
   doc-updater agent produces a new body, call `wiki/notify.py:after_doc_write`
   so reindex + fan-out happen automatically. Design lives in
   [agents/document-updater.md](../agents/document-updater.md).
2. **`tasks.document_update.agent_update_document_nl`** (queue:
   `documents`). The "agent PUTs a doc directly via API" path. Same
   side-effect pattern as above.
3. **`tasks.periodic.evaluate_scheduled_triggers`** (queue: `triggers`,
   cron: every 5 min). Depends on `app/triggers/time_based.py:due_triggers`
   being real (today: also stub). Implement that first.

### 🟡 Open questions / deferred

- **Per-doc lock to prevent two ingest tasks racing on the same path.**
  Cheap options: `(SELECT … FOR UPDATE)`-style guard via a `doc_locks`
  table, or fixed-key `flock` on a sentinel file. Defer until contention
  shows up — today the only writer queue is `documents`, capped at 2
  threads, and the post-write seam doesn't race itself.
- **Multi-worker scaling.** When we add a second `documents` worker (or
  commit from elsewhere), need a per-repo write lock; see "Concurrency
  on the wiki repo" above.
- **Test pattern (in use, worth documenting).** Set `<queue>_queue.immediate
  = True` in fixtures and call tasks synchronously to assert side
  effects (FTS row, events row). See `tests/test_save_to_fire_e2e.py`
  and `tests/test_bm25_indexer_e2e.py` for the canonical pattern.
