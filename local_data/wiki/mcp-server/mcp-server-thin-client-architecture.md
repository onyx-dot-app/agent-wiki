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
       │  MCP server  (FastAPI / ASGI, single replica for v0)      │
       │  ─────────────────────────────────────────────────────    │
       │  - bearer-token auth → user                               │
       │  - tool dispatch → httpx call to Flask                    │
       │  - per-session subscription set (in-process)              │
       │  - SSE writer fed by mcp_notifications poll (100 ms)      │
       └─────────────┬──────────────────────────────────┬──────────┘
                     │ HTTP (intra-host)                 │ poll mcp_notifications
                     ▼                                    ▼
           ┌──────────────────────────────────────────────────────┐
           │  Flask app  (existing — only writer)                  │
           │  /api/documents, /api/triggers, /api/jobs, /api/auth  │
           │  - acquires threading.Lock                            │
           │  - wiki_git.commit_file                               │
           │  - reindex + trigger fan-out                          │
           │  - INSERT mcp_notifications row (cross-process)       │
           │  - in-process asyncio.Queue push (same-process)       │
           │  - audit_log INSERT                                   │
           └────────────┬─────────────────────────────────┬────────┘
                        │ enqueue                          │
                        ▼                                  ▼
              ┌─────────────────┐               ┌──────────────────┐
              │  Huey worker    │──HTTP──▶─────│  SQLite          │
              │  (calls Flask   │               │  app.sqlite:     │
              │   API for       │               │   documents      │
              │   commits)      │               │   triggers       │
              └─────────────────┘               │   events         │
                                                │   mcp_tokens     │
                                                │   mcp_jobs       │
                                                │   mcp_notifs     │
                                                │   audit_log      │
                                                │  queue.sqlite:   │
                                                │   Huey           │
                                                └──────────────────┘
```

Single-host deployment for v0. Postgres + Redis become a future
migration when scaling needs cross that line — see [Future migration to
Postgres + Redis](#future-migration-to-postgres--redis).

## Storage — SQLite (v0)

The current store stays. `app.sqlite` continues to hold all primary
state (`users`, `documents`, `triggers`, `events`, `llm_settings`,
`mcp_connections`, plus the new tables this proposal adds). FTS5
remains the search index. Huey continues to run on `queue.sqlite`. The
existing `app/db/sqlite.py` connect helper and the free-function repo
modules in `app/auth/`, `app/triggers/`, etc. are unchanged.

This proposal does not introduce Postgres or Redis as v0 dependencies.
Adding them was the single biggest weight in earlier drafts, and the
production-quality wins this doc lands do not depend on the storage
swap. See [Future migration to Postgres + Redis](#future-migration-to-postgres--redis)
for when and why we'd cut over.

New tables (all SQLite, applied through the existing numbered-migration
mechanism in `app/db/migrations/`):

- `mcp_tokens` — per-user PATs.
- `mcp_jobs` — async LLM jobs (state, idempotency, payload, result).
- `mcp_notifications` — cross-process pubsub queue (see [Pubsub](#pubsub--polled-mcp_notifications-table)).
- `audit_log` — append-only write trail.

Schemas use SQLite idioms (`INTEGER PRIMARY KEY AUTOINCREMENT`, `TEXT`
for ISO-8601 datetimes, `TEXT` for JSON blobs). Stated explicitly per
table below.

## Pubsub — polled `mcp_notifications` table

Wiki commits and job updates fan out through two parallel paths:

1. **In-process (same-replica) — direct.** When Flask commits a doc,
   it pushes a `Notification` object into an in-process registry
   keyed by path. The MCP-server-side queue drains the registry and
   ships SSE messages without touching SQLite. Latency: sub-ms.
   Applies only to commits originating in the Flask process the MCP
   replica is co-located with.
2. **Cross-process — `mcp_notifications` table.** When the Huey
   worker (separate process) commits via the Flask API, Flask still
   does the in-process push (above) and ALSO inserts a row into
   `mcp_notifications`. Each MCP replica polls
   `mcp_notifications WHERE delivered=0 ORDER BY id` every 100 ms,
   matches against subscribed sessions, ships the SSE messages, and
   stamps `delivered=1`. A small janitor task drops delivered rows
   older than an hour.

The two paths look the same to subscribers — the in-process path is a
latency optimization for the common case (worker-not-involved commits),
the table is the correctness floor for cross-process events.

`mcp_notifications`:

| column     | type                                    | notes                                                     |
| ---------- | --------------------------------------- | --------------------------------------------------------- |
| id         | INTEGER PRIMARY KEY AUTOINCREMENT       |                                                           |
| uri        | TEXT NOT NULL                           | `wiki:///<path>` or `job://<id>`                          |
| payload    | TEXT NOT NULL                           | JSON blob: `{sha, kind}` for docs, `{status, …}` for jobs |
| delivered  | INTEGER NOT NULL DEFAULT 0              | 0 / 1 boolean                                             |
| created_at | TEXT NOT NULL DEFAULT (datetime('now')) |                                                           |

Indexed on `(delivered, id)` for the polling query.

Subscriptions themselves live only in MCP-server-process memory — no
table. Clients re-subscribe on reconnect. With single-replica MCP for
v0, that is sufficient; multi-replica futures get a subscription table
or a real pubsub broker (see Future migration).

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
| `/readyz`  | GET    | Readiness — Flask reachable AND `mcp_notifications` poll loop healthy.                                              |

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

| column       | type                                                    | notes                                                         |
| ------------ | ------------------------------------------------------- | ------------------------------------------------------------- |
| id           | INTEGER PRIMARY KEY AUTOINCREMENT                       |                                                               |
| user_id      | INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE |                                                               |
| name         | TEXT NOT NULL                                           | human label e.g. "claude-code laptop"                         |
| token_hash   | TEXT NOT NULL UNIQUE                                    | sha256(token) hex; salt unnecessary for 128-bit random tokens |
| created_at   | TEXT NOT NULL DEFAULT (datetime('now'))                 | ISO-8601                                                      |
| expires_at   | TEXT NOT NULL                                           | ISO-8601; default 1 year from `created_at`                    |
| last_used_at | TEXT                                                    | bumped per request, debounced                                 |
| revoked_at   | TEXT                                                    | non-null = revoked, kept for audit                            |

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
3. Check `revoked_at IS NULL AND expires_at > datetime('now')`.
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

| column     | type                                    | notes                                                                           |
| ---------- | --------------------------------------- | ------------------------------------------------------------------------------- |
| id         | INTEGER PRIMARY KEY AUTOINCREMENT       |                                                                                 |
| user_id    | INTEGER NOT NULL REFERENCES users(id)   |                                                                                 |
| token_id   | INTEGER REFERENCES mcp_tokens(id)       | NULL when the action came from a session cookie                                 |
| action     | TEXT NOT NULL                           | `doc.edit`, `doc.write`, `doc.move`, `doc.create`, `trigger.create`, etc.       |
| target     | TEXT NOT NULL                           | wiki path, trigger id, etc.                                                     |
| sha_before | TEXT                                    | git SHA before the change, NULL on create                                       |
| sha_after  | TEXT                                    | git SHA after the change                                                        |
| metadata   | TEXT                                    | JSON blob: tool name, arguments shape (NOT raw arguments — PII), result summary |
| created_at | TEXT NOT NULL DEFAULT (datetime('now')) | ISO-8601                                                                        |

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

For v0, MCP runs as a single replica. Session state is in-memory; the
process is the session boundary. Multi-replica scaling becomes a
concern alongside the Postgres migration — both fall in the same
future-work bucket because both want a real pubsub broker.

## Concurrency

| Layer                                  | Mechanism                                                                                                    | Guarantee                                                                      |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------ |
| 1. Single writer                       | Only Flask calls `wiki_git.commit_file`; everyone else POSTs to Flask.                                       | Eliminates cross-process race on the working tree entirely.                    |
| 2. base_sha optimistic concurrency     | Every write tool accepts `base_sha`; Flask returns 409 `stale_base` if HEAD-for-path differs.                | Hard guarantee against blind overwrites.                                       |
| 3. In-process commit lock              | Flask wraps `wiki_git.commit_file` in a `threading.Lock`. Single-replica Flask makes this sufficient for v0. | Serializes concurrent writes to the same path within Flask.                    |
| 4. Push notifications                  | In-process push for same-replica commits; `mcp_notifications` poll for cross-process commits (Huey worker).  | Sub-ms in the common case; ~100 ms cross-process. Well-behaved agents re-read. |
| 5. `stale_paths` field on tool results | Every tool result includes a list of subscribed paths that drifted since the last call.                      | Belt-and-suspenders for agents that ignore notifications.                      |
| 6. Edit fuzziness                      | Existing `wiki_edit.replace` chain in `_doc_helpers`.                                                        | Final safety net for context drift in `old_string`.                            |

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

Server-side delivery: the in-process push (for same-replica commits)
or the `mcp_notifications` poll loop (for cross-process commits) walks
the local sessions, matches subscriptions against the notified URI,
and pushes `notifications/resources/updated` into each affected
session's outbound queue. The SSE writer drains the queue.

If a session's outbound queue grows past a high-water mark (e.g. the
client stopped reading), the writer drops the connection rather than
buffer indefinitely. The client reconnects and re-subscribes.

## Async jobs

For tools that take longer than ~1s — primarily `update_doc_nl` (LLM
call) — the MCP tool returns a `job_id` immediately and the work runs
in the Huey worker.

`mcp_jobs` table:

| column          | type                                    | notes                                                              |
| --------------- | --------------------------------------- | ------------------------------------------------------------------ |
| id              | TEXT PRIMARY KEY                        | ULID                                                               |
| user_id         | INTEGER NOT NULL REFERENCES users(id)   |                                                                    |
| token_id        | INTEGER REFERENCES mcp_tokens(id)       | for audit                                                          |
| kind            | TEXT NOT NULL                           | `update_doc_nl` for now                                            |
| status          | TEXT NOT NULL                           | `pending`/`running`/`succeeded`/`failed`                           |
| idempotency_key | TEXT                                    | `sha256(user_id‖kind‖canonical_payload)` if not provided by client |
| payload         | TEXT NOT NULL                           | JSON blob: `{path, instruction, base_sha}`                         |
| result          | TEXT                                    | JSON blob: `{committed, sha, reason}`                              |
| error           | TEXT                                    | error code on `failed`                                             |
| created_at      | TEXT NOT NULL DEFAULT (datetime('now')) | ISO-8601                                                           |
| started_at      | TEXT                                    | ISO-8601                                                           |
| finished_at     | TEXT                                    | ISO-8601                                                           |

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
   updates `mcp_jobs.status` → Flask INSERTs an `mcp_notifications`
   row keyed on `job://<job_id>`.
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
│   │   └── sqlite.py               unchanged; +helper for mcp_notifications insert
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
│   ├── config.py                   env-loaded; Flask base URL, internal secret, sqlite path
│   ├── auth.py                     bearer middleware → user
│   ├── session.py                  Session class, in-memory registry, janitor
│   ├── flask_client.py             httpx.AsyncClient wrapper, internal-header injection
│   ├── pubsub.py                   in-process push registry + 100ms mcp_notifications poll loop
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
    └── docker-compose.yml           +mcp service
```

## Deployment

Three long-running processes in production for v0:

| Service     | Image            | Replicas | Notes                                                                                                                 |
| ----------- | ---------------- | -------- | --------------------------------------------------------------------------------------------------------------------- |
| Flask app   | flask.Dockerfile | 1        | The only writer. `threading.Lock` around `commit_file` is sufficient because there is one process.                    |
| MCP server  | mcp.Dockerfile   | 1        | Sessions and subscriptions live in-process. `/healthz` + `/readyz` for orchestrator probes.                           |
| Huey worker | (Flask image)    | 1+       | Background work; talks to Flask over HTTP for any commit. Multiple worker replicas are safe because Flask serializes. |

State (SQLite) lives on a shared volume mounted into Flask, MCP, and
Huey worker. The wiki working tree is a sibling volume only Flask
writes to. No external databases or brokers in v0.

Multi-replica Flask is the trigger for the [Future migration to
Postgres + Redis](#future-migration-to-postgres--redis) — the
`threading.Lock` stops working once N>1 Flask processes share the
working tree, and that's where Postgres advisory locks become useful.

## Divergences from current state

| Area                                     | Today                                             | This proposal                                                            | Why                                                                                                        |
| ---------------------------------------- | ------------------------------------------------- | ------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------- |
| Primary store                            | SQLite (`app.sqlite`)                             | SQLite (`app.sqlite`) — unchanged                                        | v0 stays on the existing store; Postgres is a future migration.                                            |
| Queue store                              | SQLite (`queue.sqlite`, Huey)                     | SQLite (`queue.sqlite`, Huey) — unchanged                                | v0 stays; Redis is a future migration.                                                                     |
| Search index                             | SQLite FTS5                                       | SQLite FTS5 — unchanged                                                  | v0 stays; `tsvector`/GIN follows the Postgres migration.                                                   |
| Inbound MCP                              | none (only an outbound stub at `app/api/mcp.py`)  | Streamable HTTP, FastAPI sidecar                                         | Real protocol surface; ASGI-native for SSE; separate scaling shape.                                        |
| Outbound MCP                             | `app/api/mcp.py` blueprint (stubs)                | Renamed `app/api/mcp_connections.py`                                     | Clarifies direction in the namespace.                                                                      |
| Wiki commit ownership                    | Flask AND worker both call `wiki_git.commit_file` | Only Flask calls `commit_file`; worker POSTs to Flask                    | Eliminates cross-process race on the git working tree. Independent of DB choice.                           |
| Auth for tools                           | none                                              | per-user PAT (`mcp_<32hex>`), sha256-hashed, expiring, revocable         | Real auth + audit per agent.                                                                               |
| Token hashing                            | n/a                                               | sha256 of high-entropy random                                            | bcrypt is for low-entropy passwords; sha256 is correct here.                                               |
| Audit                                    | events table fires on triggers only               | dedicated `audit_log` written by every Flask write endpoint              | Foundational requirement once external agents can mutate state.                                            |
| Subscriptions                            | none                                              | MCP `resources/subscribe`; in-process push + `mcp_notifications` polling | Multi-agent collab; latency floor is the 100 ms poll for cross-process events.                             |
| Pubsub mechanism                         | n/a                                               | in-process registry + polled SQLite table                                | Sub-ms in the common case (same-replica commits); ~100 ms cross-process. LISTEN/NOTIFY is the future move. |
| Web framework for long-lived connections | Flask (sync, WSGI)                                | FastAPI/Uvicorn (async, ASGI) for the MCP service                        | SSE without fighting the framework.                                                                        |
| Deployment shape                         | one Flask container, one worker container         | Flask + worker + MCP sidecar (no new datastores)                         | Adds one process; reuses the existing SQLite + git volumes.                                                |

The architectural fixes (single-writer, MCP-as-thin-client, ASGI for
SSE, base_sha, audit log) all land at v0 and are independent of the
storage choice. The Postgres + Redis migration is its own
self-contained future change.

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
| Rate limiting                                   | Add per-token throttling once abuse is observed.                               |

## Future migration to Postgres + Redis

When v0 is dogfooded and one of the trigger conditions below is hit,
the storage stack moves to Postgres + Redis. The architectural
decisions in this doc are designed so the migration is a swap of the
storage layer rather than a redesign.

Trigger conditions (any one):

- Subscription latency on cross-process commits is felt by users (the
  100 ms poll is too slow for the agent experience).
- Need for >1 Flask replica appears (HA, blue-green deploy, or
  throughput).
- Audit log volume or query patterns outgrow SQLite's single-writer
  ceiling.
- A second service (telemetry, search, eval store) wants the same
  store, and standing up shared Postgres becomes cheaper than another
  SQLite footprint.

What changes when we cut over:

| Layer             | v0 (SQLite)                                | After migration (Postgres + Redis)                                              |
| ----------------- | ------------------------------------------ | ------------------------------------------------------------------------------- |
| Primary store     | `app.sqlite`                               | Postgres                                                                        |
| Search index      | FTS5 (`documents_fts`)                     | `tsvector` + GIN, same callers via `app/wiki/search.py`                         |
| Pubsub            | in-process push + `mcp_notifications` poll | `LISTEN/NOTIFY` (`pg_notify`) inside Flask; `LISTEN` connection per MCP replica |
| Queue             | Huey on `queue.sqlite`                     | Huey on Redis                                                                   |
| Commit lock       | `threading.Lock` (single Flask process)    | Postgres advisory lock keyed on path (multi-replica safe)                       |
| MCP replicas      | 1                                          | N, sticky-by-session at the load balancer                                       |
| Datetime columns  | `TEXT` ISO-8601                            | `TIMESTAMPTZ`                                                                   |
| Autoincrement ids | `INTEGER PRIMARY KEY AUTOINCREMENT`        | `BIGSERIAL`                                                                     |
| JSON blobs        | `TEXT` parsed by callers                   | `JSONB` with operators                                                          |
| SQL idioms        | `?` placeholders, `INSERT OR IGNORE`       | `%s` placeholders, `INSERT … ON CONFLICT`                                       |

What does NOT change:

- The MCP server stays a stateless thin client over Flask.
- Flask stays the only writer.
- `base_sha` optimistic concurrency, audit log, token model, tool
  surface, transport, SSE shape — all identical.
- Free-function repo modules in `app/auth/`, `app/triggers/`, etc.
  keep their shapes; the `connect()` helper changes.

This is the contract we want from "deletable storage layer." The
investment in unified-stack-everywhere is deferred until the
deferred-cost story above breaks.

## Open questions

- **Streamable HTTP vs HTTP+SSE.** Some agent runtimes only speak the
  older transport. Streamable HTTP first; bridge if needed.
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
- **When does the Postgres migration trigger?** The conditions in
  [Future migration](#future-migration-to-postgres--redis) are
  qualitative. Worth tightening once we have observability on
  subscription latency and SQLite write throughput.

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
