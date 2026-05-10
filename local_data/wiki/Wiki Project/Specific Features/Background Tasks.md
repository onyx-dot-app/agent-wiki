# Background Tasks

Anything that might take more than ~100ms — LLM calls, BM25 reindex,
trigger fan-out, scheduled cleanups — runs out-of-band on a task
queue. Three independent queues, all backed by **pgmq** in the same
Postgres instance that holds app state. Each queue has its own
consumer process and its own concurrency story.

This doc covers the queue abstraction, the consumer/scheduler loop,
and the four kinds of tasks the system actually runs today.

## Why three queues

```
                        documents_queue          triggers_queue          lightweight_maintenance_queue
                        (LLM-bound, slow)        (NL eval, read-only)    (sub-second upkeep, no LLM)
                        concurrency = 1          concurrency = 4         concurrency = 4
```

A slow Anthropic call can't backpressure BM25 reindex; a flood of
trigger fires can't delay an event-log entry. Each queue has its own
worker container so the backlog of one is observable on its own.

The split is **deliberately small** — three queues are the right
amount of parallelism for the work we have. Don't add a fourth
without a real reason. (See `app/tasks/queues.py` for the rationale
verbatim.)

| Queue | Owns | Worker concurrency | Typical task |
|---|---:|---:|---|
| `documents_queue` | LLM doc-reconciliation | 1 | `agent_update_document_nl`, `generate_chat_title` |
| `triggers_queue` | NL trigger eval (delta + scheduled) | 4 | `fan_out_trigger_eval`, `evaluate_scheduled_triggers` |
| `lightweight_maintenance_queue` | Sub-second upkeep — BM25 reindex, agent-activity expiration cleanup | 4 | `reindex_path`, `reindex_document`, `cleanup_expired_activity` |

**Placement rule for `lightweight_maintenance_queue`:** handlers must
be sub-second, no LLM, no external HTTP, no wiki commits. The wider
concurrency only works because handlers return fast — drop a slow
task in here and it starves the others. Anything slower belongs on
its own queue.

`documents` is held at concurrency 1 deliberately — we don't want
multiple concurrent provider calls from a single host. The cheap
queues run wider.

## The pieces

```
backend/app/tasks/
├── queue.py            TaskQueue + Task + run_consumer + scheduler
├── queues.py           the three TaskQueue instances + QUEUES map
├── run_worker.py       entry point: python -m app.tasks.run_worker <name>
├── periodic.py         @triggers_queue.periodic_task() — cron tasks
├── document_update.py  @documents_queue.task() — LLM-bound
├── triggers.py         @triggers_queue.task() — fan-out
├── reindex.py          @lightweight_maintenance_queue.task() — BM25
├── chat_title.py       @documents_queue.task() — short LLM call
└── agent_activity.py   @lightweight_maintenance_queue.task() — delayed cleanups
```

Each task module is **imported up-front** by `run_worker.py`
regardless of which queue is being served, so all
`@<queue>.task()` decorators run and the per-queue handler registry
is populated. The consumer then only pulls from the queue it was
launched with; tasks bound to other queues are inert in that process.

## The producer-side API

The public surface is small. Defined in `app/tasks/queue.py`.

```python
from app.tasks.queue import crontab
from app.tasks.queues import documents_queue, lightweight_maintenance_queue, triggers_queue

# Register a handler.
@documents_queue.task()
def update_document_from_payload(doc_id: str, source: str, payload: dict) -> None:
    ...

# Direct call → enqueue. Returns the pgmq msg_id, or None in immediate mode.
update_document_from_payload(doc_id, source, payload)

# Enqueue with a future ETA (or a relative delay in seconds).
cleanup_expired_activity.schedule(args=(...), eta=expires_at)

# Cron-shaped periodic task.
@triggers_queue.periodic_task(crontab(minute="*/5"))
def evaluate_scheduled_triggers() -> None:
    ...
```

A few details worth knowing:

- **Calling the registered function doesn't run it** — it enqueues.
  `task()` returns a `Task` wrapper whose `__call__` puts a row in
  `pgmq.q_<queue>`. The handler runs later in a worker process.
- `task.schedule(args=..., kwargs=..., eta=..., delay=...)` is the
  delayed-fire form. `eta` is a timezone-aware `datetime`; `delay`
  is seconds-from-now. Pass one or the other, not both. Returns the
  pgmq `msg_id` so callers that want to *cancel* a scheduled fire
  later can `pgmq.delete(queue, msg_id)`.
- **`immediate_mode()`** — a context manager on `TaskQueue` that
  flips a boolean so `__call__` runs the handler synchronously.
  *Tests only.* Never call from app code. The context manager
  saves/restores the prior value, which is why we don't expose
  `queue.immediate = True` as the supported toggle — that would leak
  across tests if the body raised.

### Capacity + backpressure

Each `TaskQueue` is constructed with `max_size = CONFIG.max_queue_size`
(`MAX_QUEUE_SIZE`, default 1000). The producer side cap-checks before
sending:

1. Take a per-queue advisory `pg_advisory_xact_lock` (key
   `"queue-cap:<name>"`) — this is what makes the count + send
   atomic against concurrent producers on the same queue.
2. `count(*) FROM pgmq."q_<name>" WHERE read_ct = 0 OR vt <= now()`
   — pending = ready + delayed (in-flight messages don't count).
3. If `size >= max_size`: raise `QueueFullError`. Otherwise
   `pgmq.send(queue, body, delay)`.

The lock is per-queue, so the three queues stay independent. It
auto-releases on transaction commit, so contention is bounded by the
duration of one INSERT.

A Flask error handler in `app/main.py` translates `QueueFullError`
into HTTP **503** with a structured body (`queue`, `size`, `limit`).
Producer sites that want graceful degradation should catch
`QueueFullError` themselves; the periodic scheduler does this and
just logs `"skipped — queue full; will retry next tick"`.

## The consumer side

`run_worker.py` parses the queue name and calls
`queue.run_consumer(queue, concurrency=N)`. Inside:

1. Install `SIGTERM` / `SIGINT` handlers that set a module-global
   `_stop_event` (idempotent; only the main thread can register
   signal handlers in Python).
2. If the queue has periodic tasks, spawn a daemon **scheduler
   thread** (see below).
3. Spin a `ThreadPoolExecutor` with `concurrency` threads, each
   running `_worker_loop`.
4. Block until the executor's `__exit__` joins all threads (which
   happens after `_stop_event` fires).

### `_worker_loop`

```
while not _stop_event.is_set():
    msgs = pgmq.read(queue, vt=300, qty=1)   # long-poll, 1 message
    if not msgs:
        _stop_event.wait(1.0)                # idle backoff
        continue
    for msg in msgs:
        _process_one(queue, msg, max_retries=3)
```

Each thread polls independently. `qty=1` per read keeps the work
even across threads (no thread grabbing a chunk and starving the
others). The handler runs in-thread, so a slow LLM task only blocks
its own thread — not its peers, not the scheduler.

Even if the stop event fires between read and process, we finish
what we already read. Otherwise the message would hold its
visibility timeout for the full `vt_seconds` before redelivery, and
that's a worse outcome than running it during shutdown.

### Visibility timeout, retries, archiving

Constants in `app/tasks/queue.py`:

| Constant | Value | What |
|---|---:|---|
| `_DEFAULT_VT_SECONDS` | `300` | How long a worker holds a message before it's eligible for redelivery |
| `_POLL_IDLE_SLEEP` | `1.0` s | Wait between empty `pgmq.read` calls |
| `_MAX_RETRIES` | `3` | Archive after this many redeliveries |
| `_RETRY_BASE_SECONDS` | `30` | First backoff |
| `_RETRY_MAX_SECONDS` | `600` | Backoff cap |

On handler exception:

- If `read_ct >= max_retries` → `pgmq.archive(queue, msg_id)` (moves
  it to `pgmq.a_<queue>`).
- Else compute `_retry_backoff_seconds(read_ct)` (30s, 60s, 120s, …
  capped at 600s) and `pgmq.set_vt(queue, msg_id, backoff)` to push
  the visibility-timeout out. pgmq redelivers after that point.

On success → `pgmq.delete(queue, msg_id)`.

If a worker is **`SIGKILL`d** mid-handler, the message holds its VT
until it expires (5 min default) before pgmq makes it visible
again. That window is unavoidable with pgmq.

## The periodic scheduler

Cron-shaped tasks are registered with
`@<queue>.periodic_task(crontab(minute="*/5"))`. Today there's just
one: `evaluate_scheduled_triggers` on `triggers_queue`. The scheduler
is **leader-elected per queue** so scaling worker replicas doesn't
double-fire schedules.

```
queue %s: periodic scheduler is leader — N task(s)

  ┌─ try pg_try_advisory_lock(hashtext("queue-cron:<name>"))   ─ on a fresh connection
  │
  ├─ if not got: idle 30s, retry
  │
  └─ if got: hold the connection, loop:
        soonest = min(next_fires)
        sleep until soonest
        for each due entry:
            queue.enqueue(...)
            UPDATE cron_state SET last_fired_at = now()
            advance next_fire from now (NOT from missed time)
```

Two design choices worth understanding:

1. **Lock on a long-lived connection.** `pg_try_advisory_lock` (not
   `pg_advisory_xact_lock`) gives us session-scope. We hold the
   connection open for the lifetime of leadership; on process death
   the lock auto-releases when the connection closes. A non-leader
   replica idles in a 30-second retry loop and takes over if the
   leader disappears.
2. **`cron_state` persists last-fired.** The `cron_state(queue_name,
   task_name, last_fired_at)` table records every fire. On
   restart, `_initial_next_fire` computes the next fire from the
   recorded `last_fired_at`. If a fire came due during downtime,
   `croniter` returns a time in the past — the loop fires once, then
   advances `next_fires[i]` from `now` (not from the missed
   timestamp). This gives **single catch-up** semantics: you don't
   silently skip a missed fire, but you also don't queue up a
   stampede of catch-ups for a long downtime.

If `enqueue` raises `QueueFullError`, the scheduler logs and skips
the tick; the next cron evaluation will try again.

## Delayed tasks: the agent-activity cleanup pattern

`task.schedule(eta=...)` is the delayed-fire form. The most
interesting use of it is the **agent-activity** registry — every row
in `agent_activity` carries an `expires_at`, and the row needs to be
deleted when that moment passes. We don't poll. Instead:

- Every upsert of an activity row schedules a cleanup at exactly
  `expires_at` and stores the new pgmq `msg_id` on the row in
  `cleanup_msg_id`.
- If a re-read slides `expires_at` forward, we **enqueue the new
  fire first, then `pgmq.delete` the old `cleanup_msg_id`**, both in
  one transaction. Cancel-on-rewrite invariant.
- The handler (`cleanup_expired_activity`) compares its
  `expected_expires_at` argument against the row's current value and
  no-ops if they don't match (stale fire, already re-registered).
- On server boot, `schedule_all_pending_cleanups()` (called from
  `create_app`) walks every active row and re-registers a cleanup
  for each — past-due rows fire immediately, future rows fire at
  their `expires_at`. That way a restart never leaves rows orphaned.

Source: `app/tasks/agent_activity.py`. The two-step swap (enqueue
new, then delete old) is intentional — the head comment explains
why it's not a single transaction.

## Shutdown

```
SIGTERM / SIGINT
   ↓
_stop_event.set()
   ↓
Worker threads stop accepting new messages.
In-flight handlers run to completion.
   ↓
Scheduler thread observes _stop_event in its sleep,
closes its lock connection (releases the advisory lock),
returns.
   ↓
ThreadPoolExecutor.__exit__ joins all threads.
run_consumer returns. Process exits.
```

`SIGKILL` skips all of that. In-flight messages hold their VT until
expiry; the next worker that polls picks them up after that.

## Where producers enqueue from

The interesting fan-out points:

- **`app/wiki/notify.py`** — every wiki commit goes through
  `after_doc_write`, `after_doc_delete`, or `after_path_moved`.
  Those helpers enqueue `reindex_path`
  (`lightweight_maintenance_queue`) + `fan_out_trigger_eval`
  (`triggers_queue`) so a human edit, agent
  edit, move, and worker-side commit all fan out identically.
- **`app/api/documents.py`** — `POST /documents/ingest` enqueues
  `process_pushed_document`; `POST /documents/reindex` enqueues
  `reindex_path`.
- **`app/api/chat.py`** — when a chat session crosses its first
  user/assistant pair, enqueues `generate_chat_title` on
  `documents_queue` (reuses the LLM-bound worker rather than
  spinning up a new one for one short call).
- **`app/mcp_server/tools.py`** — the inbound MCP `update_doc_nl`
  tool inserts a job row, then enqueues
  `agent_update_document_nl(job_id)` on `documents_queue`. The
  worker reconstitutes `g.user` from the job row before any wiki
  write so ACL applies inside the worker too. See
  [MCP Server Inbound](MCP%20Server%20Inbound.md).
- **`app/llm/agents/tools/_doc_helpers.py`** — agent tools call
  `schedule_cleanup_for_natural_key` whenever they upsert an
  agent-activity row.

## Storage layout in Postgres

Every queue + scheduler artifact lives in the same Postgres database
as app state.

| Object | What |
|---|---|
| `pgmq.q_documents`, `pgmq.q_triggers`, `pgmq.q_lightweight_maintenance` | The live queues (created by migration `0001_initial`; `0007` renames the BM25 queue) |
| `pgmq.a_<name>` | Archive tables — messages exceeding `MAX_RETRIES` end up here |
| `cron_state` | `(queue_name, task_name, last_fired_at)` — one row per periodic task |
| `agent_activity.cleanup_msg_id` | The pgmq msg_id of the row's currently-scheduled cleanup; nullable |

The pgmq SQL functions (`pgmq.send`, `pgmq.read`, `pgmq.delete`,
`pgmq.archive`, `pgmq.set_vt`, `pgmq.create`) have no SQLAlchemy ORM
equivalents, so they go through `session.execute(text(...))` inside
`app/tasks/queue.py`. **That's the only place raw queue SQL is
allowed**, alongside the pg_textsearch operators in
`app/db/fts.py`. New raw-SQL sites elsewhere should be model
expressions instead.

## Healthcheck

`GET /api/health` reads `queue.depth()` for each of the three
queues. The split into `ready` / `delayed` / `in_flight` is the
point: a queue sitting at "size 9" because of nine
`schedule(..., eta=tomorrow)` fires is healthy; the same number from
ready messages no consumer is draining is not. Single
filtered-`count(*)` per queue, cheap on every poll. Source:
`app/api/health.py`.

## Adding a new task

1. Pick the right queue. If you're not sure: LLM call → `documents`,
   trigger / read-only side effect → `triggers`, sub-second upkeep
   (search keep-up, expirations, eviction) → `lightweight_maintenance`.
   Anything that doesn't fit the third queue's placement rule should
   go on its own queue.
2. Add the handler under `app/tasks/<area>.py` and decorate with
   `@<queue>.task()` (or `@<queue>.periodic_task(crontab(...))`).
3. Make sure `app/tasks/run_worker.py` imports your module — it must,
   or the decorator never runs and the handler is unregistered.
4. Producers call the registered function directly to enqueue. For
   delayed fires, call `task.schedule(args=..., eta=...)`.
5. In tests, wrap relevant queues with `queue.immediate_mode()` and
   call the producer; the handler runs synchronously and you assert
   on its side effects (DB rows, commits, fts entries). Don't set
   `queue.immediate = True` directly — the context manager exists
   precisely to prevent that leak.

## Adding a new queue (rare)

You almost certainly don't want to. The whole point of the
three-queue design is that the rest of the system can reason about
"the queues" as a small fixed set. If you do:

1. Add a new `TaskQueue("<name>")` in `app/tasks/queues.py` and
   register it in `QUEUES`.
2. Add an entry to `_CONCURRENCY` in `app/tasks/run_worker.py`.
3. Add a new migration that calls `pgmq.create('<name>')`.
4. Add a `worker-<name>` service to `docker-compose.yml`,
   `.vscode/launch.json`, and the helm chart in
   `deploy/helm/agent-workspace/`.

## Pointers

- Code: `backend/app/tasks/`
- Compose: `docker-compose.yml` (`worker-documents`,
  `worker-triggers`, `worker-lightweight-maintenance`)
- Local dev: [Running Locally](../Running%20Locally.md) §"Workers"
  — five-process loop and VS Code launch configs.
- Big picture: [Architecture Overview](../Architecture%20Overview.md)
  — where the queues fit in the data flow.
