# MCP Server (inbound) — thin-client architecture

Alternative architecture for the inbound MCP surface, side-by-side with
[mcp-server.md](./mcp-server.md). Both target the same user experience
(external coding agents read/edit/subscribe to the wiki); they differ on
where state lives, who owns writes, and what we run alongside Flask.

This doc is architecture only — no phasing, no estimates, no migration
choreography. Companion to [seams.md](../seams.md) and
[architecture_diagram.md](../architecture_diagram.md).

## Goals

1. **The MCP server is a translation layer, not a state owner.** It
   speaks JSON-RPC to clients and HTTP to the Flask app. State lives in
   the database and the wiki working tree; the MCP server keeps only
   per-replica session metadata (subscriptions, seen paths) that is
   cheap to rebuild on reconnect.
2. **One writer to the wiki and to the database.** Concurrent writes to
   `git` working trees corrupt index state, partial commits, and HEAD
   pointers — `git` provides no native cross-process write coordination.
   The MCP server, the Huey worker, and the Flask handler must all
   funnel commits through a single in-process writer (Flask).
3. **Stateless, horizontally scalable MCP replicas.** Sessions are
   sticky to a replica via load-balancer hashing on session id; nothing
   about a replica is durable beyond an open connection.
4. **The MCP server is deletable.** If Anthropic deprecates MCP or we
   adopt a different agent protocol, we replace the MCP package without
   touching wiki business logic.

## Core principle — Flask is the only writer

| Process               | Reads wiki + DB?          | Writes wiki + DB?                   |
| --------------------- | ------------------------- | ----------------------------------- |
| Flask app             | yes                       | **yes**                             |
| MCP server            | no — calls Flask HTTP API | no — calls Flask HTTP API           |
| Huey worker           | reads job rows            | calls Flask HTTP API for any commit |
| Cron / future workers | as needed                 | calls Flask HTTP API                |

Worker processes never call `wiki_git.commit_file` directly. They POST to
`/api/documents/<path>/edit` (or whichever endpoint matches the change
shape). Flask serializes commits behind a process-local lock and is the
sole holder of git working-tree state.

This is the single architectural decision that eliminates the
cross-process commit race. Every other piece of the design follows from
it.

## Topology

```
┌──────────────────────────────────────────────────────────────────┐
│ Claude Code  /  Cursor  /  Codex  /  Craft   (MCP clients)       │
└─────────────┬────────────────────────────────────┬───────────────┘
              │ POST /mcp  (JSON-RPC)              │ GET /mcp  (SSE)
              ▼                                    ▼
       ┌───────────────────────────────────────────────────────────┐
       │  MCP server  (FastAPI / ASGI, N stateless replicas)       │
       │  ─────────────────────────────────────────────────────    │
       │  - bearer-token auth → user                               │
       │  - tool dispatch → httpx call to Flask                    │
       │  - per-session subscription set (in-process)              │
       │  - SSE writer fed by Postgres LISTEN                      │
       └─────────────┬──────────────────────────────────┬──────────┘
                     │ HTTP (intra-cluster, mTLS)        │ LISTEN wiki_doc_updated
                     ▼                                    ▼
           ┌──────────────────────────────────────────────────────┐
           │  Flask app  (existing — only writer)                  │
           │  /api/documents, /api/triggers, /api/jobs, /api/auth  │
           │  - acquires commit lock                               │
           │  - wiki_git.commit_file                               │
           │  - reindex + trigger fan-out                          │
           │  - pg_notify('wiki_doc_updated', payload)             │
           │  - audit_log INSERT                                   │
           └────────────┬─────────────────────────────────┬────────┘
                        │ enqueue                          │
                        ▼                                  ▼
              ┌─────────────────┐               ┌──────────────────┐
              │  Huey worker    │──HTTP──▶─────│  Postgres        │
              │  (calls Flask   │               │  documents       │
              │   API for       │               │  triggers        │
              │   commits)      │               │  events          │
              └─────────────────┘               │  mcp_tokens      │
                                                │  mcp_jobs        │
                                                │  audit_log       │
                                                │  LISTEN/NOTIFY   │
                                                └──────────────────┘
```

## Storage — Postgres

Move off SQLite. Postgres is the only primary store, in every
environment — local development, CI, staging, production. SQLite is
removed; there is no dev-mode fallback, no config flag, no compatibility
shim. Local dev runs Postgres natively (`brew install postgresql@16 &&
brew services start postgresql@16`).

Reasons to unify on Postgres rather than keep SQLite as a dev option:

- A two-store architecture splits the bug surface in two. Migration
  bugs, transaction-isolation differences, FTS-syntax drift, and
  datetime serialization all behave differently between SQLite and
  Postgres. Catching them only in CI or staging is exactly the failure
  mode unified stacks prevent.
- Subscriptions, audit log behavior, and the commit lock all rely on
  Postgres-specific features (`LISTEN/NOTIFY`, advisory locks). Any
  dev-mode SQLite path would either fake these or skip them, which
  means the SQLite path runs different code than prod and is therefore
  not testing prod.
- Operating two stores costs runbook and review attention forever. The
  one-time cost of installing Postgres locally is paid once per
  developer machine.

Why now, not later:

- **`LISTEN/NOTIFY` is in Postgres natively, free, real-time.** The
  subscription system depends on it. Polling-on-SQLite caps
  notification latency at the poll interval and pays CPU continuously.
- **Multiple replicas need a single shared writer view.** SQLite
  assumes one process; WAL extends to multi-reader but not to
  concurrent writes from N processes. Postgres is built for it.
- **Schema migrations stay simple now, get harder later.** Migrating
  from SQLite to Postgres after live tables exist is real work
  (column-type drift, datetime serialization, autoincrement vs
  sequences). Cutting over before the surface grows is a one-time
  cost.
- **Backups, point-in-time recovery, replication, observability** —
  every one of these has a paved path in Postgres and a hand-rolled
  cottage industry in SQLite.

The repo modules in `app/auth/users.py`, `app/triggers/repo.py`, etc.
are already free-function repos using `app.db.sqlite.connect()`. Swap
the `connect()` implementation to `psycopg` (or `psycopg2`); migrate the
SQL idioms (`?` → `%s`, `INSERT OR IGNORE` → `INSERT … ON CONFLICT`,
`AUTOINCREMENT` → `BIGSERIAL`). The repo pattern is preserved.

FTS5 → Postgres `tsvector` + GIN index. The bm25 wrapper in
`app/wiki/search.py` becomes a `ts_rank_cd` query. Same callers, same
return shape.

Huey switches from SQLite-backed to Redis-backed (Huey supports both
out of the box). Same Redis everywhere — local dev runs it natively
(`brew install redis && brew services start redis`).

The full stack is **Postgres + Redis**, identical in dev and prod. No
fallbacks, no flags. Two more services to install on a fresh laptop;
both are one `brew` line and run forever after.

## Pubsub — Postgres LISTEN/NOTIFY

Every wiki commit fires `pg_notify('wiki_doc_updated', json_payload)`
inside Flask after the commit + reindex + trigger fan-out succeed.

Each MCP server replica holds one Postgres connection in `LISTEN
wiki_doc_updated` mode. When NOTIFY arrives, the replica looks up which
of its in-memory sessions are subscribed to the affected path and pushes
`notifications/resources/updated` over each subscriber's open SSE
stream.

Cross-process delivery is solved by Postgres: the Huey worker can fire
NOTIFY too (as long as the worker also runs commits via the Flask API,
the NOTIFY happens inside Flask anyway, so this is implicit).

Job-status updates use a separate channel (`mcp_job_updated`) with the
same shape.

This replaces the table-backed `mcp_subscriptions` + `mcp_notifications`
tables in the [mcp-server.md](./mcp-server.md) plan. There is no
notifications table; there is no polling; there is no `delivered_at`
bookkeeping. Subscriptions live only in MCP-server-process memory and
re-establish on client reconnect.

## Transport — Streamable HTTP via FastAPI sidecar

The MCP server is its own Python process running FastAPI on Uvicorn. It
is **not** mounted on the Flask app via an ASGI bridge.

Why a sidecar instead of mounting:

- **SSE on Flask is fighting the framework.** Werkzeug's WSGI model is
  thread-per-connection; long-lived SSE connections starve the worker
  pool unless we add a second WSGI server tuned for it. Native ASGI
  with Uvicorn handles this in the loop.
- **Different scaling shape.** Wiki UI traffic is request/response,
  bursty per active user. MCP is a small number of long-lived
  connections per agent. Replica counts move independently — a busy
  agent fleet doesn't force the UI tier to scale, and UI traffic
  doesn't compete with SSE I/O.
- **Different blast radius.** A bug in the MCP tool dispatcher should
  not take down the wiki UI, and vice versa. Separate processes give
  separate restart domains.
- **Deployment hygiene.** The MCP server has a different dependency
  surface (`mcp` SDK, `httpx`) than Flask. A separate image keeps each
  smaller and faster to rebuild.

Endpoints on the MCP service:

| Endpoint   | Method | Body / behavior                                                                                                     |
| ---------- | ------ | ------------------------------------------------------------------------------------------------------------------- |
| `/mcp`     | POST   | JSON-RPC 2.0 request. Response is a single reply, OR an `text/event-stream` upgrade for streamed multi-step output. |
| `/mcp`     | GET    | Long-lived SSE stream of server-initiated messages (resource updates, job status, list-changed).                    |
| `/healthz` | GET    | Liveness — does the process answer.                                                                                 |
| `/readyz`  | GET    | Readiness — Flask reachable AND Postgres LISTEN connection healthy.                                                 |

Session id is established on `initialize` and carried in
`Mcp-Session-Id` on every subsequent request. The load balancer hashes
on this header for replica stickiness.

The `app/api/mcp.py` blueprint that exists today is renamed
`app/api/mcp_connections.py` (it was always the outbound surface;
inbound now lives in the sidecar).

## Auth

### Token format

`mcp_<32 hex chars>` = 128 bits of randomness, prefix scopes greppability
in logs.

### Storage

`mcp_tokens` table:

| column       | type                                                   | notes                                                         |
| ------------ | ------------------------------------------------------ | ------------------------------------------------------------- |
| id           | BIGSERIAL PRIMARY KEY                                  |                                                               |
| user_id      | BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE |                                                               |
| name         | TEXT NOT NULL                                          | human label e.g. "claude-code laptop"                         |
| token_hash   | TEXT NOT NULL UNIQUE                                   | sha256(token) hex; salt unnecessary for 128-bit random tokens |
| created_at   | TIMESTAMPTZ NOT NULL DEFAULT now()                     |                                                               |
| expires_at   | TIMESTAMPTZ NOT NULL                                   | default `now() + interval '1 year'`                           |
| last_used_at | TIMESTAMPTZ                                            | bumped per request, debounced                                 |
| revoked_at   | TIMESTAMPTZ                                            | non-null = revoked, kept for audit                            |

Hashing: **sha256 of the raw token**, not bcrypt. Bcrypt's slow-by-design
property protects low-entropy passwords against brute force; high-entropy
random tokens get nothing from it and pay the latency on every request.
This deliberately diverges from `app/auth/users.py:passwords` which use
bcrypt for the right reason.

Constant-time comparison on the hash is mandatory.

### Per-request flow

1. MCP server reads `Authorization: Bearer mcp_…`.
2. SHA-256 the raw token, query `mcp_tokens` by `token_hash` (indexed,
   one row).
3. Check `revoked_at IS NULL AND expires_at > now()`.
4. Resolve `user_id` → user record cached for the request.
5. Update `last_used_at` (debounced — once per minute per token to avoid
   write churn).
6. Inject `X-Internal-User-Id: <id>` and `X-Internal-Token-Id: <id>` on
   every Flask call. mTLS or a shared secret authenticates the call as
   coming from the MCP service itself.

### Token management surface

`/api/mcp/tokens` on Flask, user-scoped (no admin):

| Method | Path                   | Behavior                                             |
| ------ | ---------------------- | ---------------------------------------------------- |
| GET    | `/api/mcp/tokens`      | list current user's tokens, no hashes, no raw tokens |
| POST   | `/api/mcp/tokens`      | mint; response shows raw token **once**              |
| PATCH  | `/api/mcp/tokens/<id>` | rename, change `expires_at`                          |
| DELETE | `/api/mcp/tokens/<id>` | revoke (soft-delete via `revoked_at`)                |

Frontend page `frontend/src/app/settings/mcp-tokens/page.tsx` reuses
`apiFetch` and `useRequireAuth`.

### Audit log

Every Flask write endpoint inserts into `audit_log`:

| column     | type                                 | notes                                                                     |
| ---------- | ------------------------------------ | ------------------------------------------------------------------------- |
| id         | BIGSERIAL PRIMARY KEY                |                                                                           |
| user_id    | BIGINT NOT NULL REFERENCES users(id) |                                                                           |
| token_id   | BIGINT REFERENCES mcp_tokens(id)     | NULL when the action came from a session cookie                           |
| action     | TEXT NOT NULL                        | `doc.edit`, `doc.write`, `doc.move`, `doc.create`, `trigger.create`, etc. |
| target     | TEXT NOT NULL                        | wiki path, trigger id, etc.                                               |
| sha_before | TEXT                                 | git SHA before the change, NULL on create                                 |
| sha_after  | TEXT                                 | git SHA after the change                                                  |
| metadata   | JSONB                                | tool name, arguments shape (NOT raw arguments — PII risk), result summary |
| created_at | TIMESTAMPTZ NOT NULL DEFAULT now()   |                                                                           |

The audit log is append-only. Indexed on `(user_id, created_at)` and
`(target, created_at)`. Retention policy lives in ops, not the schema.

This is a deliberate addition not in [mcp-server.md](./mcp-server.md).
For any system that lets external agents mutate shared state, an audit
trail is foundational, not a v2 nice-to-have.

## Sessions

A Session object lives in MCP-server-process memory:

```
Session:
  id: str                           # Mcp-Session-Id value
  user_id: int                      # resolved from token
  token_id: int
  seen_paths: set[str]              # paths the agent has read at HEAD
  subscriptions: set[ResourceURI]   # wiki:///… and job://…
  notification_queue: asyncio.Queue # outbound SSE buffer
  created_at: datetime
  last_active_at: datetime
```

Sessions die on client disconnect or after 24h of inactivity (janitor
task). Subscriptions die with the session — clients re-subscribe on
reconnect. There is no persistent subscription table.

Scaling: load balancer hashes `Mcp-Session-Id` to a replica. Sticky
sessions make in-memory state correct without distributed state. If a
replica dies, all its sessions die; clients reconnect and re-establish
on a new replica. This is the standard stateful-stickiness pattern.

## Concurrency

| Layer                                  | Mechanism                                                                                                                      | Guarantee                                                             |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------- |
| 1. Single writer                       | Only Flask calls `wiki_git.commit_file`; everyone else POSTs to Flask.                                                         | Eliminates cross-process race on the working tree entirely.           |
| 2. base_sha optimistic concurrency     | Every write tool accepts `base_sha`; Flask returns 409 `stale_base` if HEAD-for-path differs.                                  | Hard guarantee against blind overwrites.                              |
| 3. In-process commit lock              | Flask wraps `wiki_git.commit_file` in a `threading.Lock` (and a Postgres advisory lock keyed on path when N>1 Flask replicas). | Serializes concurrent writes to the same path within Flask.           |
| 4. Push notifications                  | `pg_notify` → MCP `LISTEN` → SSE within ~1ms.                                                                                  | Low-latency feedback so well-behaved agents re-read before next edit. |
| 5. `stale_paths` field on tool results | Every tool result includes a list of subscribed paths that drifted since the last call.                                        | Belt-and-suspenders for agents that ignore notifications.             |
| 6. Edit fuzziness                      | Existing `wiki_edit.replace` chain in `_doc_helpers`.                                                                          | Final safety net for context drift in `old_string`.                   |

`base_sha` semantics per tool:

| Tool            | base_sha behavior                                                                                                                  |
| --------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `edit_doc`      | Optional. If set, must match HEAD-for-path.                                                                                        |
| `apply_patch`   | Optional. Same as edit_doc.                                                                                                        |
| `write_doc`     | **Required when overwriting an existing file.** New-file creates skip.                                                             |
| `update_doc_nl` | Optional. Recorded on the job row; checked at task-execution time inside the worker (HEAD may have moved between enqueue and run). |
| `move_doc`      | N/A — content unchanged.                                                                                                           |

`read_doc` returns `sha`, so the canonical agent flow is `read_doc →
edit_doc(base_sha=<that sha>)`.

## Subscriptions

URI scheme on the MCP surface:

| URI                  | Body               | Notes             |
| -------------------- | ------------------ | ----------------- |
| `wiki:///<rel-path>` | `text/markdown`    | Doc body at HEAD. |
| `wiki:///`           | `application/json` | Tree walk.        |
| `job://<job_id>`     | `application/json` | Async job status. |

`resources/subscribe`:

- `wiki:///<path>`: add `(session_id, path)` to the session's
  `subscriptions` set in process memory.
- `job://<id>`: add `(session_id, job_id)`.

`resources/unsubscribe` removes the entry. Subscriptions die with the
session.

`read_doc(subscribe=true, is_head=true)` auto-subscribes. Historical
reads (with `sha`) do not, because subscribing to a frozen sha is
meaningless.

Server-side delivery: when `pg_notify` arrives on the MCP replica, the
LISTEN handler walks the local sessions, matches subscriptions against
the notified path, and pushes `notifications/resources/updated` into
each affected session's outbound queue. The SSE writer drains the queue.

If a session's outbound queue grows past a high-water mark (e.g. the
client stopped reading), the writer drops the connection rather than
buffer indefinitely. The client reconnects and re-subscribes.

## Async jobs

For tools that take longer than ~1s — primarily `update_doc_nl` (LLM
call) — the MCP tool returns a `job_id` immediately and the work runs
in the Huey worker.

`mcp_jobs` table:

| column          | type                                 | notes                                                              |
| --------------- | ------------------------------------ | ------------------------------------------------------------------ |
| id              | TEXT PRIMARY KEY                     | ULID                                                               |
| user_id         | BIGINT NOT NULL REFERENCES users(id) |                                                                    |
| token_id        | BIGINT REFERENCES mcp_tokens(id)     | for audit                                                          |
| kind            | TEXT NOT NULL                        | `update_doc_nl` for now                                            |
| status          | TEXT NOT NULL                        | `pending`/`running`/`succeeded`/`failed`                           |
| idempotency_key | TEXT                                 | `sha256(user_id‖kind‖canonical_payload)` if not provided by client |
| payload         | JSONB NOT NULL                       | `{path, instruction, base_sha}`                                    |
| result          | JSONB                                | `{committed, sha, reason}`                                         |
| error           | TEXT                                 | error code on `failed`                                             |
| created_at      | TIMESTAMPTZ NOT NULL DEFAULT now()   |                                                                    |
| started_at      | TIMESTAMPTZ                          |                                                                    |
| finished_at     | TIMESTAMPTZ                          |                                                                    |

Unique partial index on `(user_id, idempotency_key) WHERE
idempotency_key IS NOT NULL` collapses retries.

Flow:

1. MCP server `update_doc_nl` tool → POST `/api/jobs/doc-update` on
   Flask.
2. Flask validates, looks up idempotency_key (returns existing
   pending/succeeded job if a match), inserts `mcp_jobs` row, enqueues
   Huey task, returns `{job_id}`.
3. MCP tool returns `{job_id, status_uri: "job://<job_id>"}` to the
   client.
4. Huey worker: load job, validate `base_sha` against current HEAD,
   call `app.llm.agents.document_updater.run(...)`. On result, POST to
   Flask write endpoint (NOT direct git call) → Flask commits → Flask
   updates `mcp_jobs.status` → Flask `pg_notify('mcp_job_updated', …)`.
5. MCP replicas with subscribers to `job://<job_id>` push the update
   over SSE.

A debounce window (`MCP_NL_DEBOUNCE_SECONDS`, default 30s) inside the
worker checks for a recent succeeded job on the same `(user_id, path)`
and skips the LLM call if found, marking the new job
`succeeded committed=false reason=debounced`.

## Tool surface

The MCP server keeps its own tool registry in `mcp_server/tools/`. Each
tool is a small file that translates MCP arguments → Flask HTTP call →
result shape. The chat-agent tool registry in
`app/llm/agents/tools/` is independent — same domain, different
caller, different needs.

Why a separate registry instead of re-exposing the chat-agent registry:

- The chat-agent tools call DB and git directly via `_doc_helpers`. The
  MCP tools must call Flask HTTP. Different code path — sharing the
  registry forces conditional dispatch logic that grows over time.
- MCP-only tools (`apply_patch`, `update_doc_nl`, `ask_nl_question`,
  `read_doc(sha)`, `list_history`) have no chat-agent analog and don't
  belong in the chat registry.
- Tool descriptions and input schemas can be tuned for the MCP audience
  (external coding agents) without affecting the chat agent's prompt
  budget.
- The two surfaces evolve at different rates.

This is a deliberate divergence from [mcp-server.md](./mcp-server.md),
which proposes sharing the registry. The shared-registry approach
minimizes initial duplication but accretes shape-divergence
conditionals as the surfaces grow.

Inventory:

| Tool                                                            | Calls                                            | Notes                                                                                                                                                         |
| --------------------------------------------------------------- | ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `search_wiki`                                                   | `GET /api/documents/search`                      | bm25 / tsvector hits with snippets.                                                                                                                           |
| `read_doc(path, sha?, subscribe=true)`                          | `GET /api/documents/<path>?sha=<sha>`            | `sha` defaults to HEAD. Returns `{path, body, sha, is_head}`. Auto-subscribes when `is_head` and `subscribe=true`. Populates `seen_paths` only on HEAD reads. |
| `list_history(path, limit=20)`                                  | `GET /api/documents/<path>/history`              | `[{sha, author, ts, message}, …]`                                                                                                                             |
| `edit_doc(path, edits[], message, base_sha?)`                   | `POST /api/documents/<path>/edit`                | Atomic batch of `{old_string, new_string, replace_all?}`.                                                                                                     |
| `apply_patch(path, patch, message, base_sha?)`                  | `POST /api/documents/<path>/patch`               | Unified diff with line-anchored hunks; fuzzy fallback if line offsets drift. Atomic across hunks.                                                             |
| `write_doc(path, body, message, base_sha?)`                     | `POST /api/documents/<path>`                     | Full overwrite or new file. `base_sha` required for overwrite.                                                                                                |
| `move_doc(old_path, new_path, message)`                         | `POST /api/documents/<old_path>/move`            | Rename.                                                                                                                                                       |
| `create_directory(path, message)`                               | `POST /api/documents/directories`                | `.gitkeep`.                                                                                                                                                   |
| `update_doc_nl(path, instruction, idempotency_key?, base_sha?)` | `POST /api/jobs/doc-update`                      | Async LLM-driven update; returns `{job_id}`.                                                                                                                  |
| `ask_nl_question(query, max_sources=8)`                         | `POST /api/wiki/ask`                             | Sync RAG; `{answer, sources: [{path, sha}]}`. Wraps `app/llm/agents/wiki_qa.py`.                                                                              |
| `create_trigger`, `update_trigger`                              | `POST /api/triggers`, `PATCH /api/triggers/<id>` | Same shape as the in-app trigger tools.                                                                                                                       |

Every successful tool result includes a `stale_paths` field: paths the
agent had subscribed to that have drifted since the agent's last tool
call. Computed from the session's pending notifications,
non-destructively.

## Module layout

```
backend/
├── app/                            (existing Flask — unchanged shape)
│   ├── api/
│   │   ├── documents.py            +write/edit/patch/move/edit-fuzzy/history endpoints
│   │   ├── jobs.py                 NEW — async job CRUD
│   │   ├── mcp_connections.py      RENAMED from mcp.py (outbound stays here)
│   │   ├── mcp_tokens.py           NEW — user-scoped token CRUD
│   │   └── wiki_ask.py             NEW — POST /api/wiki/ask (sync RAG)
│   ├── auth/
│   │   └── mcp_tokens.py           NEW — sha256 verify, constant-time compare
│   ├── audit/
│   │   └── log.py                  NEW — write helper called from each write endpoint
│   ├── db/
│   │   └── postgres.py             NEW — replaces sqlite.py; psycopg connect + repo idioms
│   ├── tasks/
│   │   └── document_update.py      worker — calls Flask HTTP, never git directly
│   ├── llm/agents/
│   │   └── wiki_qa.py              NEW — one-shot RAG harness
│   └── wiki/
│       ├── git.py                  +read_file_at_ref(rel, sha), +head_sha_for_path(rel)
│       └── patch.py                NEW — parse + apply unified-diff hunks
│
├── mcp_server/                     NEW package, sibling of app/
│   ├── __init__.py
│   ├── main.py                     FastAPI ASGI entry (uvicorn)
│   ├── config.py                   env-loaded; Flask base URL, internal secret, PG DSN
│   ├── auth.py                     bearer middleware → user
│   ├── session.py                  Session class, in-memory registry, janitor
│   ├── flask_client.py             httpx.AsyncClient wrapper, internal-header injection
│   ├── pubsub.py                   asyncio Postgres LISTEN; routes notifies to sessions
│   ├── transport.py                SSE writer per session (drains the outbound queue)
│   ├── resources.py                wiki:///, job://, list/read/subscribe handlers
│   └── tools/
│       ├── __init__.py             registry
│       ├── search_wiki.py
│       ├── read_doc.py
│       ├── edit_doc.py
│       ├── apply_patch.py
│       ├── write_doc.py
│       ├── multi_edit.py
│       ├── move_doc.py
│       ├── create_directory.py
│       ├── create_trigger.py
│       ├── update_trigger.py
│       ├── update_doc_nl.py
│       ├── ask_nl_question.py
│       └── list_history.py
│
├── frontend/src/app/
│   └── settings/mcp-tokens/page.tsx  NEW
│
└── deploy/
    ├── flask.Dockerfile
    ├── mcp.Dockerfile               NEW — slim ASGI image
    ├── worker.Dockerfile            existing
    └── docker-compose.yml           +mcp service, +postgres, +redis (queue)
```

## Deployment

Four long-running services in production:

| Service     | Image            | Replicas                     | Notes                                                                                                                                            |
| ----------- | ---------------- | ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| Postgres    | upstream         | 1 primary (+ replicas later) | Owns all durable state.                                                                                                                          |
| Redis       | upstream         | 1                            | Huey backing store.                                                                                                                              |
| Flask app   | flask.Dockerfile | 1+                           | The only writer; behind a load balancer. Optionally pinned to one replica for the simplest commit lock; scaled out with Postgres advisory locks. |
| MCP server  | mcp.Dockerfile   | 1+                           | Stateless; sticky-by-session at the LB; `/healthz` + `/readyz` for orchestrator probes.                                                          |
| Huey worker | (Flask image)    | 1+                           | Background work; talks to Flask over HTTP for any commit.                                                                                        |

Single-replica Flask is the simplest correct configuration. Multi-replica
Flask requires Postgres advisory locks around `commit_file` keyed on the
target path; advisory locks are sub-millisecond and don't touch user
data, so the cost is trivial.

## Divergences from current state

| Area                                     | Today                                             | This proposal                                                                | Why                                                                                               |
| ---------------------------------------- | ------------------------------------------------- | ---------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| Primary store                            | SQLite (`app.sqlite`)                             | Postgres (everywhere — dev, CI, prod)                                        | LISTEN/NOTIFY, multi-replica, audit-friendly, advisory locks. SQLite is removed; no dev fallback. |
| Queue store                              | SQLite (`queue.sqlite`, Huey)                     | Redis (everywhere)                                                           | Idiomatic Huey; one queue store across all environments.                                          |
| Search index                             | SQLite FTS5                                       | Postgres `tsvector` + GIN                                                    | Co-located with primary store; same ACID transaction as the commit.                               |
| Inbound MCP                              | none (only an outbound stub at `app/api/mcp.py`)  | Streamable HTTP, FastAPI sidecar                                             | Real protocol surface; ASGI-native for SSE; separate scaling shape.                               |
| Outbound MCP                             | `app/api/mcp.py` blueprint (stubs)                | Renamed `app/api/mcp_connections.py`                                         | Clarifies direction in the namespace.                                                             |
| Wiki commit ownership                    | Flask AND worker both call `wiki_git.commit_file` | Only Flask calls `commit_file`; worker POSTs to Flask                        | Eliminates cross-process race on the git working tree.                                            |
| Auth for tools                           | none                                              | per-user PAT (`mcp_<32hex>`), sha256-hashed, expiring, revocable             | Real auth + audit per agent.                                                                      |
| Token hashing                            | n/a                                               | sha256 of high-entropy random                                                | bcrypt is for low-entropy passwords; sha256 is correct here.                                      |
| Audit                                    | events table fires on triggers only               | dedicated `audit_log` written by every Flask write endpoint                  | Foundational requirement once external agents can mutate state.                                   |
| Subscriptions                            | none                                              | MCP `resources/subscribe`; PG `LISTEN/NOTIFY` fan-out; in-memory per-replica | Real-time multi-agent collab.                                                                     |
| Pubsub mechanism                         | n/a                                               | Postgres LISTEN/NOTIFY                                                       | Native, real-time, no polling.                                                                    |
| Web framework for long-lived connections | Flask (sync, WSGI)                                | FastAPI/Uvicorn (async, ASGI) for the MCP service                            | SSE without fighting the framework.                                                               |
| Deployment shape                         | one Flask container, one worker container         | Flask + worker + MCP sidecar + Postgres + Redis                              | Each service scales independently.                                                                |

Each of these changes has a defensible "do it later" framing. The
recommendation is to do them now, before the surface area grows. The
SQLite→Postgres migration is the largest of these — it has been called
out in the spec doc as inevitable. Doing it before live tables exist
under MCP load saves a coordinated cutover later.

## Out of scope

| Concern                                         | Out of scope here, where it lands                                              |
| ----------------------------------------------- | ------------------------------------------------------------------------------ |
| Outbound MCP dispatch from triggers             | Trigger destination work, separate doc.                                        |
| Persistent subscriptions across reconnects      | Clients re-subscribe; if proven flaky in practice, revisit.                    |
| Per-org / multi-tenant isolation                | Org concept doesn't exist yet; tokens are user-scoped.                         |
| Streaming partial results from `update_doc_nl`  | Job is `pending`/`succeeded`/`failed`; agents don't need token-level progress. |
| Resource templates (`resources/templates/list`) | Flat `resources/list` is fine until the wiki has thousands of docs.            |
| Token scopes beyond all-or-nothing              | Add `scopes` column when first scoped use case appears.                        |
| HTTP+SSE legacy MCP transport                   | Streamable HTTP only. Bridge if a real client breaks.                          |
| Rate limiting                                   | Add a Redis token bucket once abuse is observed.                               |

## Open questions

- **Streamable HTTP vs HTTP+SSE.** Some agent runtimes only speak the
  older transport. Streamable HTTP first; bridge if needed.
- **Postgres advisory lock granularity.** `pg_advisory_xact_lock(hash)`
  keyed on path is the natural choice. Single-replica Flask doesn't
  need it; once we go multi-replica, every commit acquires the lock for
  the duration of the commit. Latency impact has not been measured —
  worth checking before declaring it fine.
- **Worker → Flask HTTP cost.** Every async commit becomes a
  loopback HTTP call. Latency impact is in the single-digit ms range
  on localhost; needs verification under load.
- **MCP service co-location.** Is the MCP service deployed in the same
  cluster as Flask, or run behind a public-facing edge with its own
  TLS? Affects the auth model between MCP and Flask (mTLS in-cluster
  is cheap; over the public internet needs more thought).
- **Internal-header trust.** The MCP service injects
  `X-Internal-User-Id` on Flask calls. This requires either mTLS or a
  shared secret enforced at the Flask boundary. Either is fine; pick
  one and document.
- **Tool description sharing.** If the chat-agent and MCP surfaces both
  expose `search_wiki`, do their descriptions diverge or stay aligned?
  Default: each owns its description; cross-pollinate through review.
- **`stale_paths` payload size.** A heavily subscribed agent could
  accumulate many notifications between tool calls. Cap and surface
  truncation explicitly on the result.

## Relationship to other docs

- [mcp-server.md](./mcp-server.md) — the alternative architecture this
  doc is paired with. Same product surface; different state ownership,
  storage, transport, and deployment.
- [seams.md](../seams.md) — needs an updated row pointing inbound MCP
  at this sidecar, outbound at `app/api/mcp_connections.py`.
- [architecture_diagram.md](../architecture_diagram.md) — the "as
  built" snapshot; this doc describes the next shape.
- [agents/document-updater.md](../agents/document-updater.md) — the
  agent invoked by `update_doc_nl`.
- [tool-design](../tool-design/tool-design.md) — the in-process
  chat-agent tool primitives this proposal stops sharing the registry
  with.
