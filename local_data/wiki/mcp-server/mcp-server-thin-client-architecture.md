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
2. **Single git writer via a transactional outbox.** Concurrent writes
   to `git` working trees corrupt index state, partial commits, and
   HEAD pointers — `git` provides no native cross-process write
   coordination. Every caller (Flask handler, Huey worker,
   document-updater agent, future cron) writes a row into
   `commit_outbox` in the same DB transaction as the business write; a
   dedicated single-concurrency Huey consumer (`commit_writer`) is the
   only process that calls `wiki_git.commit_file`.
3. **Stateless, horizontally scalable MCP replicas.** Sessions are
   sticky to a replica via load-balancer hashing on session id; nothing
   about a replica is durable beyond an open connection.
4. **The MCP server is deletable.** If MCP is replaced or another
   agent protocol joins, the MCP package goes away without touching
   wiki business logic.

## Core principle — transactional outbox

The git working tree is non-transactional state alongside Postgres,
which is transactional. The classic solution is the **transactional
outbox**: every intent-to-mutate-git is recorded as a row in Postgres
inside the same transaction as the business write. A single dedicated
consumer drains the outbox and performs the git operation. This buys:

- ACID atomicity across the business write and the commit intent. A
  process crash between "I decided to commit X" and "git wrote X" is
  impossible — either both land or neither does.
- Single git writer for free. The drain process is the one and only
  caller of `wiki_git.commit_file`. No advisory locks, no `flock`
  ceremony, no race.
- Retryability. A failed git op leaves the outbox row in `pending` (or
  `failed` with backoff); the next drain picks it up.
- Audit by construction. The outbox row carries `user_id`,
  `token_id`, `action`, `path`, `payload`, `result_sha`, `error`. It
  IS the audit log; a separate `audit_log` table is unnecessary.
- No HTTP loopback. Workers don't call back into Flask just to commit.
  They write the same outbox row Flask does.

| Process                  | Reads wiki + DB? | Writes outbox? | Writes git? |
| ------------------------ | ---------------- | -------------- | ----------- |
| Flask app                | yes              | yes            | no          |
| MCP server               | calls Flask HTTP | no             | no          |
| Huey worker (general)    | yes              | yes            | no          |
| `commit_writer` consumer | yes              | drains/marks   | **yes**     |

`wiki_git.commit_file` is private to `commit_writer`; `app/wiki/git.py`
makes that explicit (the read helpers stay public; `commit_file` and
`move_and_commit` move behind a "writer-only" import path or a runtime
assertion that the caller is the writer process).

## Topology

```
┌──────────────────────────────────────────────────────────────────┐
│ Claude Code  /  Cursor  /  Codex  /  Craft   (MCP clients)       │
└─────────────┬────────────────────────────────────┬───────────────┘
              │ POST /mcp  (JSON-RPC)              │ GET /mcp  (SSE)
              ▼                                    ▼
       ┌───────────────────────────────────────────────────────────┐
       │  MCP server  (FastAPI / ASGI, N stateless replicas)       │
       │  - bearer-token auth → user                               │
       │  - tool dispatch → httpx call to Flask                    │
       │  - per-session subscription set (in-process)              │
       │  - SSE writer fed by Postgres LISTEN                      │
       └─────────────┬──────────────────────────────────┬──────────┘
                     │ HTTP (intra-cluster, mTLS)        │ LISTEN wiki_doc_updated
                     ▼                                    ▼
           ┌──────────────────────────────────────────────────────┐
           │  Flask app  (existing — owns API + outbox enqueue)    │
           │  /api/documents, /api/triggers, /api/jobs, /api/auth  │
           │  INSERT commit_outbox + business rows in one txn      │
           │  LISTEN commit_done for sync-response handlers        │
           └──┬─────────────────────────────────────┬──────────────┘
              │ enqueue                              │
              ▼                                      │
       ┌─────────────────┐                           │
       │  Huey worker    │── INSERT commit_outbox ──┤
       │  (doc-updater   │                           │
       │   agent, etc.)  │                           │
       └─────────────────┘                           │
                                                     ▼
                              ┌────────────────────────────────────────┐
                              │  Postgres                              │
                              │  documents / triggers / events /       │
                              │  mcp_tokens / mcp_jobs / commit_outbox │
                              │  + LISTEN/NOTIFY channels              │
                              └────────────────────┬───────────────────┘
                                                   │ pg_notify('commit_pending', id)
                                                   ▼
                              ┌────────────────────────────────────────┐
                              │  commit_writer  (Huey, concurrency=1)  │
                              │  - SELECT FOR UPDATE SKIP LOCKED       │
                              │  - check base_sha vs head_sha_for_path │
                              │  - wiki_git.commit_file                │
                              │  - update outbox.status, result_sha    │
                              │  - enqueue reindex + trigger fan-out   │
                              │  - pg_notify('wiki_doc_updated', …)    │
                              │  - pg_notify('commit_done', outbox_id) │
                              └────────────────────────────────────────┘
```

## Storage — Postgres + Redis

Postgres is the primary store across all environments — local
development, CI, staging, production. SQLite is removed; there is no
dev-mode fallback, no config flag, no compatibility shim. Local dev
runs Postgres natively (`brew install postgresql@16 && brew services
start postgresql@16`).

Reasons:

- The outbox pattern relies on `SELECT FOR UPDATE SKIP LOCKED`,
  partial indexes on status, and `LISTEN/NOTIFY` for low-latency
  drain. None of these are SQLite-equivalent in semantics.
- Subscriptions and the commit-writer wake-up depend on
  `LISTEN/NOTIFY` for sub-millisecond push — polling is the only
  SQLite alternative and pays CPU continuously.
- Multiple replicas need a single shared writer view. SQLite assumes
  one process; Postgres handles N processes correctly.
- Schema migrations stay simple now, get harder later. Migrating from
  SQLite to Postgres after live tables exist is real work
  (column-type drift, datetime serialization, autoincrement vs
  sequences).
- Backups, point-in-time recovery, replication, observability — paved
  paths in Postgres, hand-rolled in SQLite.

The repo modules in `app/auth/users.py`, `app/triggers/repo.py`, etc.
are already free-function repos using `app.db.sqlite.connect()`. Swap
the `connect()` implementation to `psycopg`; migrate the SQL idioms
(`?` → `%s`, `INSERT OR IGNORE` → `INSERT … ON CONFLICT`,
`AUTOINCREMENT` → `BIGSERIAL`). The repo pattern is preserved.

### Replacing the SQLite FTS5 search index

Search today is a SQLite FTS5 virtual table (`documents_fts`)
maintained by a Huey reindex task that fires after every commit (see
[architecture_diagram.md](../architecture_diagram.md) — `wiki_bm25_huey`
queue + `tasks/reindex.py`). Tokenizer is `porter unicode61`; ranking
is `bm25(documents_fts)`; snippets come from `snippet(documents_fts,
…)`.

This replaces with **Postgres native full-text search**: a generated
`tsvector` column on the `documents` table backed by a GIN index.
Queries use `websearch_to_tsquery` + `ts_rank_cd`; snippets use
`ts_headline`.

The migration is a net simplification — the Postgres design removes a
Huey queue, a reindex task, a virtual table, and a startup bootstrap
hook. None of those ceremonies exist in the new design because
Postgres maintains the index inline in the `UPDATE` that lands the
new body.

#### Schema

```sql
ALTER TABLE documents
  ADD COLUMN content_fts tsvector
  GENERATED ALWAYS AS (
    setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
    setweight(to_tsvector('english', coalesce(body,  '')), 'B')
  ) STORED;

CREATE INDEX idx_documents_content_fts
  ON documents USING GIN (content_fts);
```

`GENERATED ALWAYS AS … STORED` means Postgres recomputes `content_fts`
inside the same transaction as any UPDATE to `title` or `body`. The
index entry updates atomically with the row. There is no separate
"apply this write" / "now reindex it" two-step the way SQLite FTS5
required.

`setweight A/B` lets us rank title hits above body hits in `ts_rank_cd`
output. Today's FTS5 setup ranks title and body uniformly; this is a
small quality bump that comes free with the migration.

#### Operator and function mapping

| Concern          | SQLite FTS5                                      | Postgres                                                                                      |
| ---------------- | ------------------------------------------------ | --------------------------------------------------------------------------------------------- |
| Index unit       | virtual table `documents_fts`                    | column `documents.content_fts` + GIN                                                          |
| Tokenizer        | `porter unicode61`                               | `english` config (Snowball/porter + unicode-aware lower)                                      |
| Match            | `documents_fts MATCH ?`                          | `content_fts @@ websearch_to_tsquery('english', ?)`                                           |
| Rank             | `bm25(documents_fts)`                            | `ts_rank_cd(content_fts, query, 32)` — cover-density                                          |
| Snippet          | `snippet(documents_fts, 1, '**', '**', '…', 32)` | `ts_headline('english', body, query, 'StartSel=**, StopSel=**, MaxFragments=2, MaxWords=20')` |
| Phrase           | `"foo bar"`                                      | `"foo bar"` (parsed by `websearch_to_tsquery`)                                                |
| Negation         | `-term`                                          | `-term` (parsed by `websearch_to_tsquery`)                                                    |
| Boolean          | implicit AND, `OR`, `NEAR`                       | implicit AND, `OR`. `NEAR` has no direct equivalent — see "Differences" below                 |
| Reindex on write | `tasks.reindex.reindex_path` (Huey)              | None — `STORED` column updates inline                                                         |
| Index bootstrap  | `bootstrap_index_if_empty()` at process start    | None — column populated when row is inserted                                                  |

`websearch_to_tsquery` is the parser to use for any user-typed query.
It accepts the same shape Google does (quoted phrases, `-` negation,
`OR`) without operator-escape gymnastics. `to_tsquery` exists too but
expects pre-formatted operator syntax (`foo & !bar`); it's brittle
fed raw user input.

#### Caller surface

`app/wiki/search.py` is the only seam. Internals change; the public
function signature is unchanged.

Before (SQLite):

```python
def search(query: str, limit: int = 10) -> list[Hit]:
    rows = db.execute("""
        SELECT path, title,
               snippet(documents_fts, 1, '**', '**', '…', 32) AS snippet,
               bm25(documents_fts) AS rank
        FROM documents_fts
        WHERE documents_fts MATCH ?
        ORDER BY rank
        LIMIT ?
    """, (query, limit)).fetchall()
    return [Hit(**r) for r in rows]
```

After (Postgres):

```python
def search(query: str, limit: int = 10) -> list[Hit]:
    rows = db.execute("""
        SELECT d.path, d.title,
               ts_headline('english', d.body, q,
                           'StartSel=**, StopSel=**, MaxFragments=2, MaxWords=20')
                 AS snippet,
               ts_rank_cd(d.content_fts, q, 32) AS rank
        FROM documents d, websearch_to_tsquery('english', %s) q
        WHERE d.content_fts @@ q
        ORDER BY rank DESC
        LIMIT %s
    """, (query, limit)).fetchall()
    return [Hit(**r) for r in rows]
```

Same `Hit` dataclass, same `{path, title, snippet, rank}` fields.
Every caller — `/api/documents/search`, the chat agent's
`search_wiki` tool, the MCP server's `search_wiki` tool, any future
caller — is unchanged.

The `Hit.rank` field flips sign convention (`bm25` returns lower =
better; `ts_rank_cd` returns higher = better), so the ORDER BY flips
from `ASC` to `DESC` and any external caller comparing rank values
needs to know. In practice no caller does — they consume an already-
sorted list.

#### What goes away

This is the part that makes the migration a net simplification, not
an addition:

| Removed                                                                                     | Why                                                         |
| ------------------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| `documents_fts` virtual-table migration                                                     | Replaced by the generated `tsvector` column on `documents`. |
| `wiki_bm25_huey` queue                                                                      | No async reindex needed.                                    |
| `tasks/reindex.py` (`reindex_path`, `reindex_document`)                                     | Generated column updates inside the UPDATE; no second step. |
| `tasks/run_worker.py --queue wiki_bm25_huey` invocation                                     | One fewer worker process / fewer worker threads.            |
| `bootstrap_index_if_empty` call in `app/main.py:create_app` and in `mcp_server/__main__.py` | Generated column populates on insert; nothing to bootstrap. |
| Manual reindex button in the admin UI                                                       | Removed — there is no separate index to refresh.            |
| The "schedule a reindex after a doc commits" line in `commit_writer`                        | One less fan-out task to enqueue.                           |

Net delta versus today: **fewer moving parts**, not more. This is
worth saying explicitly because the broader Postgres+Redis migration
adds infrastructure (two new datastores, one new sidecar) — the
search-path migration alone subtracts.

#### Why generated column rather than a write-side trigger

Two PG-native options exist: a `STORED` generated column (compiler
maintains it on every UPDATE) or a trigger (`BEFORE INSERT OR UPDATE`
that sets the column manually). Both produce the same data. The
generated column is preferred because:

- Less code (one column declaration vs a trigger function).
- Trigger functions are runtime-evaluated and have more failure modes
  (PL/pgSQL errors, search_path issues). The generator is part of the
  CREATE TABLE / ALTER TABLE definition.
- The generator participates in `pg_catalog`, so tools like
  `pg_dump` and migration validators see it as schema, not as
  imperative behavior.
- Performance is identical (PG actually compiles generated-column
  expressions inline).

Pick generated column. Don't add a trigger.

#### Differences in ranking quality (and the escape hatch)

`ts_rank_cd` is cover-density ranking — closer term proximity ranks
higher, term frequency adds, document length normalizes. `bm25` is
the same family but weights term rarity (IDF) more aggressively. For
a wiki of hundreds to low-thousands of documents, the practical
difference in result ordering for typical queries is small.

If ranking quality regresses on real queries, the escape hatch is the
[ParadeDB `pg_search`](https://www.paradedb.com/) extension which
adds a true bm25 ranker on top of Postgres tsvector indexes. It's a
drop-in (extension + one index type change), no application-code
churn. Out of scope for v0; flagged as available.

#### Phrase / proximity / fuzzy

| Need                             | Today (FTS5) | New (PG)                               | Notes                                                                                        |
| -------------------------------- | ------------ | -------------------------------------- | -------------------------------------------------------------------------------------------- |
| Exact phrase                     | `"foo bar"`  | `"foo bar"` via `websearch_to_tsquery` | Identical.                                                                                   |
| `NEAR` operator (within N words) | supported    | not directly                           | Use `phraseto_tsquery` for adjacency, or skip — agent-wiki has no caller using `NEAR` today. |
| Fuzzy / typo tolerance           | not in FTS5  | `pg_trgm` extension + `%` operator     | Out of scope; available if user reports type-tolerance need.                                 |
| Stemming                         | porter       | `english` config = porter family       | Identical for ~all real queries.                                                             |

#### Update cost (write-path)

| Phase                               | SQLite FTS5                                                                                       | Postgres                                                            |
| ----------------------------------- | ------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| Write business row                  | `INSERT INTO documents …` (~100µs)                                                                | `INSERT INTO documents …` (~200µs incl. tsvector compute + GIN)     |
| Reindex                             | `enqueue reindex_path` (~50µs to enqueue) + worker pickup + virtual-table rebuild (~ms in worker) | (none — done inline in the INSERT)                                  |
| User-visible latency on next search | depends on worker drain time (typically 10s–100s of ms)                                           | immediate — search reads see new content as soon as the txn commits |

Net: write costs ~100µs more synchronously; eliminates ~5-10ms of
async work and the lag window where a search wouldn't find a
just-committed doc. Lower write amplification (one place writes
instead of two), tighter consistency between write and search.

#### Index size

GIN on a tsvector typically lands at ~30-50% of the source text size,
roughly the same as SQLite FTS5's index size. No concern at the
agent-wiki corpus scale (hundreds to thousands of docs).

#### Migration mechanics

For an existing-data cutover (when we move off SQLite):

1. Dump SQLite `documents` rows.
2. Migration creates the new Postgres `documents` table including the
   `content_fts` STORED generated column.
3. Bulk INSERT loads the rows; PG computes the tsvector inline.
4. `CREATE INDEX … CONCURRENTLY ON documents USING GIN (content_fts)`
   builds the index without locking writes.
5. Cutover: switch the application's DSN from SQLite to Postgres.

There is no "warm the index" step — the generated column populated
during the bulk INSERT, and the GIN index was built CONCURRENTLY.
First search after cutover hits a fully-populated index.

#### Open questions on the search migration

- **`english` config vs `simple`.** `english` does stemming
  (`updated`/`updates`/`updating` collapse to one token), which is
  usually right. Code-symbol searches (`run_chat_loop_stream`) get
  tokenized weirdly under `english`; under `simple` they survive
  intact. If we see code-symbol queries in real usage, we may need a
  separate code-aware index column (`to_tsvector('simple', body)`).
  Not a v0 concern.
- **Title weighting factor.** `setweight A/B` is the categorical
  knob. Numeric weights for `ts_rank_cd` (the `{0.1, 0.2, 0.4, 1.0}`
  array on the function) is the fine-grained knob. Default tuple is
  fine for v0; tune if title hits feel under-ranked.
- **Multi-language wiki content.** All current content is English.
  If a future wiki lands docs in other languages, we'd need a
  `language` column on `documents` and a generator that picks the
  right TS config. Out of scope until we see non-English content.

Huey switches from SQLite-backed to Redis-backed. Same Redis
everywhere — local dev runs it natively (`brew install redis && brew
services start redis`).

Three Huey queues:

- `documents_huey` — heavy LLM tasks (document-updater agent runs).
- `wiki_bm25_huey` — reindexing.
- `commits_huey` — single-concurrency commit writer (NEW).

`triggers_huey` from the existing layout stays as the trigger-eval
queue.

The full stack is **Postgres + Redis**, identical in dev and prod. No
fallbacks, no flags. Two services to install on a fresh laptop;
both are one `brew` line and run forever after.

## Outbox — schema and protocol

```sql
-- migration: commit_outbox
CREATE TABLE commit_outbox (
  id              BIGSERIAL PRIMARY KEY,
  user_id         BIGINT NOT NULL REFERENCES users(id),
  token_id        BIGINT REFERENCES mcp_tokens(id),
  action          TEXT NOT NULL,             -- 'doc.edit'/'doc.write'/'doc.move'/'doc.create_dir'/'doc.patch'
  path            TEXT NOT NULL,
  payload         JSONB NOT NULL,            -- shape varies by action: {body, edits, message, trace, ...}
  base_sha        TEXT,                      -- optional optimistic-concurrency anchor
  idempotency_key TEXT,                      -- client-supplied UUID v4 for retry-safe writes
  status          TEXT NOT NULL DEFAULT 'pending',
                                             -- pending | running | committed | stale_base | failed
  result_sha      TEXT,                      -- set when status='committed'
  error           TEXT,                      -- non-null when status in ('stale_base','failed')
  attempts        INTEGER NOT NULL DEFAULT 0,
  enqueued_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at      TIMESTAMPTZ,
  finished_at     TIMESTAMPTZ
);
CREATE INDEX idx_commit_outbox_pending
  ON commit_outbox(id) WHERE status='pending';
CREATE INDEX idx_commit_outbox_user_created
  ON commit_outbox(user_id, enqueued_at DESC);
CREATE UNIQUE INDEX idx_commit_outbox_idempotency
  ON commit_outbox(user_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
```

### Enqueue protocol

Every Flask write endpoint and every Huey-task that needs to commit
follows the same pattern:

```python
with db.transaction() as txn:
    # 1. business-state write (e.g. mcp_jobs.status='succeeded')
    repo.update(...)
    # 2. intent-to-commit
    outbox_id = commit_outbox.insert(
        user_id=ctx.user_id,
        token_id=ctx.token_id,
        action="doc.edit",
        path=rel,
        payload={"edits": edits, "message": message},
        base_sha=base_sha,
    )
    txn.commit()

# 3. wake the writer
db.notify("commit_pending", outbox_id)
```

The notify is opportunistic — the writer also polls every N seconds as
a backstop for missed wake-ups (NOTIFY isn't durable across listener
disconnects).

### Drain protocol — `commit_writer`

```python
# pseudo, runs forever as a Huey task on commits_huey (concurrency=1)
def drain_commit_outbox():
    while True:
        wait_for_notify_or_timeout("commit_pending", timeout=5)
        with db.transaction() as txn:
            row = txn.execute("""
                SELECT * FROM commit_outbox
                WHERE status='pending'
                ORDER BY id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            """).first()
            if not row:
                continue
            txn.execute("UPDATE commit_outbox SET status='running', "
                        "started_at=now(), attempts=attempts+1 WHERE id=%s",
                        (row.id,))
            txn.commit()

        try:
            if row.base_sha and head_sha_for_path(row.path) != row.base_sha:
                mark(row.id, status="stale_base",
                     error=f"current={head_sha_for_path(row.path)}")
                pg_notify("commit_done", row.id)
                continue
            sha = perform_commit(row)            # the only call to wiki_git.commit_file
            mark(row.id, status="committed", result_sha=sha)
            enqueue_reindex(row.path)
            enqueue_trigger_fan_out(row.path, sha)
            pg_notify("wiki_doc_updated", json({"path": row.path, "sha": sha}))
            pg_notify("commit_done", row.id)
        except Exception as exc:
            mark(row.id, status="failed", error=repr(exc))
            pg_notify("commit_done", row.id)
```

`commit_writer` is the only process that imports `wiki_git.commit_file`
or `move_and_commit`. Other modules import the read helpers (`read_file`,
`history`, `head_sha_for_path`, etc.) freely.

### Synchronous-feel for UI handlers

Flask handlers that need to return the new sha to the caller (in-app
editor saves, MCP `edit_doc`/`write_doc` tool calls):

```python
def edit_doc():
    with db.transaction():
        outbox_id = enqueue_outbox(...)
    db.notify("commit_pending", outbox_id)

    deadline = time.monotonic() + 5  # 5s synchronous budget
    with db.listen("commit_done") as sub:
        for notif in sub.iter(timeout_until=deadline):
            if int(notif.payload) != outbox_id:
                continue
            row = commit_outbox.get(outbox_id)
            if row.status == "committed":
                return {"sha": row.result_sha, ...}
            if row.status == "stale_base":
                return 409, {"error": "stale_base", ...}
            return 500, {"error": row.error}
    # past 5s — return 202 + outbox_id; client polls or subscribes
    return 202, {"outbox_id": outbox_id}
```

Typical drain latency: a single git op + INSERT + NOTIFY ≈ 10–30 ms
on co-located Postgres. The synchronous response feels indistinguishable
from a direct call. The 5-second budget is for the rare slow case (very
large doc, GC pause); past it, we surface a job id and let the client
follow up.

Async callers (Huey workers, the document-updater agent) do not LISTEN
— they enqueue and return. The fan-out tasks the writer enqueues handle
the user-visible side effects.

### Why not `audit_log` as a separate table

The outbox row already carries every field an audit log needs:
who (`user_id`, `token_id`), what (`action`, `path`, `payload`), when
(`enqueued_at`/`finished_at`), result (`result_sha`/`status`/`error`),
diff is reconstructable from `result_sha` against `head_sha_for_path`
at enqueue time.

Adding a parallel `audit_log` table duplicates the data and creates
the question of "what if outbox and audit_log disagree." Outbox IS the
audit log; queries that the audit story would want (`WHERE
user_id=? AND status='committed' ORDER BY finished_at DESC`) hit the
same indexes the writer uses.

For non-mutating reads (logins, search, list-tokens) we may want a
parallel `read_audit` later; out of scope for v0.

## Pubsub — Postgres LISTEN/NOTIFY

Two channels:

- `wiki_doc_updated` — fired by `commit_writer` after every successful
  commit. Payload: `{path, sha, kind}`.
- `commit_done` — fired by `commit_writer` after every outbox row
  reaches a terminal state. Payload: outbox `id`.
- `mcp_job_updated` — fired by Flask when an `mcp_jobs` row changes.
  Payload: `job_id`.

Each MCP server replica holds one Postgres connection in `LISTEN
wiki_doc_updated` mode. When NOTIFY arrives, the replica looks up
sessions subscribed to the affected path and pushes
`notifications/resources/updated` over each subscriber's open SSE
stream.

`commit_done` is a per-handler concern (synchronous Flask write
handlers LISTEN it during their own request lifecycle). The MCP server
does not LISTEN this channel.

## Transport — Streamable HTTP via FastAPI sidecar

The MCP server is its own Python process running FastAPI on Uvicorn. It
is **not** mounted on the Flask app via an ASGI bridge.

- **SSE on Flask is fighting the framework.** Werkzeug's WSGI model is
  thread-per-connection; long-lived SSE connections starve the worker
  pool unless we add a second WSGI server tuned for it. Native ASGI
  with Uvicorn handles this in the loop.
- **Different scaling shape.** Wiki UI traffic is request/response;
  MCP is a small number of long-lived connections per agent. Replica
  counts move independently — a busy agent fleet doesn't force the UI
  tier to scale, and UI traffic doesn't compete with SSE I/O.
- **Different blast radius.** A bug in the MCP tool dispatcher should
  not take down the wiki UI, and vice versa. Separate processes give
  separate restart domains.
- **Deployment hygiene.** The MCP server has a different dependency
  surface (`mcp` SDK, `httpx`) than Flask. Separate images stay
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

`mcp_<32 hex chars>` = 128 bits of randomness. Prefix scopes
greppability in logs.

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
5. Update `last_used_at` (debounced — once per minute per token to
   avoid write churn).
6. Inject `X-Internal-User-Id: <id>` and `X-Internal-Token-Id: <id>`
   on every Flask call. mTLS or a shared secret authenticates the
   call as coming from the MCP service itself.

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
on a new replica. Standard stateful-stickiness pattern.

## Concurrency

| Layer                                  | Mechanism                                                                                                                                | Guarantee                                                                                                                |
| -------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| 1. Single git writer                   | All commits flow through `commit_outbox`; `commit_writer` (Huey, concurrency=1) is the only process that calls `wiki_git.commit_file`.   | Eliminates cross-process race on the working tree by construction.                                                       |
| 2. base_sha optimistic concurrency     | `commit_writer` checks `base_sha == head_sha_for_path(path)` at drain time; mismatch returns `stale_base` to the enqueuer.               | Hard guarantee against blind overwrites — checked at the latest possible moment, so fast followers don't false-negative. |
| 3. Atomic enqueue                      | Outbox INSERT is in the same DB transaction as the business-state write (e.g. `mcp_jobs.status='running'`). Either both land or neither. | No "I committed the DB write but git didn't happen" gap.                                                                 |
| 4. Push notifications                  | `pg_notify('wiki_doc_updated', …)` from `commit_writer` → MCP `LISTEN` → SSE within ~1ms.                                                | Low-latency feedback so well-behaved agents re-read before next edit.                                                    |
| 5. `stale_paths` field on tool results | Every tool result includes a list of subscribed paths that drifted since the last call.                                                  | Belt-and-suspenders for agents that ignore notifications.                                                                |
| 6. Edit fuzziness                      | Existing `wiki_edit.replace` chain in `_doc_helpers`.                                                                                    | Final safety net for context drift in `old_string`.                                                                      |

`base_sha` semantics per tool:

| Tool            | base_sha behavior                                                                                                             |
| --------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `edit_doc`      | Optional. If set, must match HEAD-for-path at drain time.                                                                     |
| `apply_patch`   | Optional. Same as edit_doc.                                                                                                   |
| `write_doc`     | **Required when overwriting an existing file.** New-file creates skip.                                                        |
| `update_doc_nl` | Optional. Recorded on the job row; checked by the writer when the worker's outbox row drains (HEAD may have moved meanwhile). |
| `move_doc`      | N/A — content unchanged.                                                                                                      |

`read_doc` returns `sha`; canonical agent flow is `read_doc →
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

If a session's outbound queue grows past a high-water mark (client
stopped reading), the writer drops the connection rather than buffer
indefinitely. The client reconnects and re-subscribes.

## Async jobs

For tools that take longer than ~1 s — primarily `update_doc_nl` (LLM
call) — the MCP tool returns a `job_id` immediately and the work runs
in the Huey worker.

`mcp_jobs` table:

| column          | type                                 | notes                                                              |
| --------------- | ------------------------------------ | ------------------------------------------------------------------ |
| id              | TEXT PRIMARY KEY                     | ULID                                                               |
| user_id         | BIGINT NOT NULL REFERENCES users(id) |                                                                    |
| token_id        | BIGINT REFERENCES mcp_tokens(id)     |                                                                    |
| kind            | TEXT NOT NULL                        | `update_doc_nl` for now                                            |
| status          | TEXT NOT NULL                        | `pending`/`running`/`succeeded`/`failed`                           |
| idempotency_key | TEXT                                 | `sha256(user_id‖kind‖canonical_payload)` if not provided by client |
| payload         | JSONB NOT NULL                       | `{path, instruction, base_sha}`                                    |
| result          | JSONB                                | `{committed, sha, reason}`                                         |
| outbox_id       | BIGINT REFERENCES commit_outbox(id)  | set when the job's commit has been enqueued                        |
| error           | TEXT                                 | error code on `failed`                                             |
| created_at      | TIMESTAMPTZ NOT NULL DEFAULT now()   |                                                                    |
| started_at      | TIMESTAMPTZ                          |                                                                    |
| finished_at     | TIMESTAMPTZ                          |                                                                    |

Unique partial index on `(user_id, idempotency_key) WHERE
idempotency_key IS NOT NULL` collapses retries.

Flow:

1. MCP `update_doc_nl` tool → POST `/api/jobs/doc-update` on Flask.
2. Flask validates, looks up `idempotency_key` (returns existing
   pending/succeeded job if a match), inserts `mcp_jobs` row, enqueues
   Huey task on `documents_huey`, returns `{job_id}`.
3. MCP tool returns `{job_id, status_uri: "job://<job_id>"}` to the
   client.
4. Huey worker on `documents_huey`: load job, call
   `app.llm.agents.document_updater.run(...)`. Atomic txn: update
   `mcp_jobs.status='running'`, INSERT `commit_outbox` row,
   `mcp_jobs.outbox_id = <new>`, `pg_notify('commit_pending', id)`.
5. `commit_writer` drains the outbox row, commits, fires
   `pg_notify('mcp_job_updated', job_id)` after also stamping
   `mcp_jobs.status='succeeded'/'failed'/'stale_base'`.
6. MCP replicas with subscribers to `job://<job_id>` push the update
   over SSE.

A debounce window (`MCP_NL_DEBOUNCE_SECONDS`, default 30 s) inside the
worker checks for a recent `succeeded` job on the same `(user_id,
path)` and skips the LLM call if found, marking the new job
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
| `edit_doc(path, edits[], message, base_sha?)`                   | `POST /api/documents/<path>/edit`                | Atomic batch of `{old_string, new_string, replace_all?}`. Synchronous via outbox + LISTEN.                                                                    |
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
├── app/                            (existing Flask)
│   ├── api/
│   │   ├── documents.py            +write/edit/patch/move/edit-fuzzy/history endpoints
│   │   ├── jobs.py                 NEW — async job CRUD
│   │   ├── mcp_connections.py      RENAMED from mcp.py (outbound stays here)
│   │   ├── mcp_tokens.py           NEW — user-scoped token CRUD
│   │   └── wiki_ask.py             NEW — POST /api/wiki/ask (sync RAG)
│   ├── auth/
│   │   └── mcp_tokens.py           NEW — sha256 verify, constant-time compare
│   ├── outbox/
│   │   ├── repo.py                 NEW — commit_outbox CRUD: insert, mark, fetch
│   │   └── enqueue.py              NEW — single function callers use to enqueue
│   ├── db/
│   │   └── postgres.py             NEW — replaces sqlite.py; psycopg connect + repo idioms
│   ├── tasks/
│   │   ├── document_update.py      worker — INSERTs commit_outbox; never calls git directly
│   │   └── commit_writer.py        NEW — single-concurrency Huey consumer that drains outbox
│   ├── llm/agents/
│   │   └── wiki_qa.py              NEW — one-shot RAG harness
│   └── wiki/
│       ├── git.py                  +read_file_at_ref(rel, sha), +head_sha_for_path(rel); commit_file becomes "writer-only"
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
    ├── worker.Dockerfile            existing — also runs commit_writer queue
    └── docker-compose.yml           +mcp service, +postgres, +redis
```

## Deployment

Five long-running services in production:

| Service       | Image                               | Replicas                     | Notes                                                                                                      |
| ------------- | ----------------------------------- | ---------------------------- | ---------------------------------------------------------------------------------------------------------- |
| Postgres      | upstream                            | 1 primary (+ replicas later) | Owns all durable state.                                                                                    |
| Redis         | upstream                            | 1                            | Huey backing store.                                                                                        |
| Flask app     | flask.Dockerfile                    | 1+                           | API surface; INSERTs into `commit_outbox` for any write. Behind a load balancer.                           |
| MCP server    | mcp.Dockerfile                      | 1+                           | Stateless; sticky-by-session at the LB; `/healthz` + `/readyz` for orchestrator probes.                    |
| Huey worker   | worker.Dockerfile                   | 1+                           | Runs `documents_huey`, `triggers_huey`, `wiki_bm25_huey` queues. Calls Flask API; INSERTs `commit_outbox`. |
| commit_writer | worker.Dockerfile (different queue) | **exactly 1**                | Drains `commits_huey` with concurrency=1. The only caller of `wiki_git.commit_file`.                       |

`commit_writer` runs the same image as the Huey worker but is launched
with `python -m app.tasks.run_worker --queue commits_huey
--workers 1`. The constraint "exactly 1" is enforced operationally
(orchestrator deploys this service with replica=1, no HPA). If two
were to run simultaneously, `SELECT FOR UPDATE SKIP LOCKED` keeps them
from grabbing the same row, but both could touch the working tree —
defeating the single-writer goal. Operationally pin to one.

Flask is otherwise scalable (multi-replica). The outbox indirection
removes the working-tree race that would otherwise force single-replica.

## Divergences from current state

| Area                                     | Today                                             | This proposal                                                                | Why                                                                                                          |
| ---------------------------------------- | ------------------------------------------------- | ---------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| Primary store                            | SQLite (`app.sqlite`)                             | Postgres (everywhere — dev, CI, prod)                                        | LISTEN/NOTIFY, multi-replica, audit-friendly, advisory locks. SQLite is removed; no dev fallback.            |
| Queue store                              | SQLite (`queue.sqlite`, Huey)                     | Redis (everywhere)                                                           | Idiomatic Huey at scale; one queue store across all environments.                                            |
| Search index                             | SQLite FTS5                                       | Postgres `tsvector` + GIN                                                    | Co-located with primary store; same ACID transaction as the commit-outbox INSERT.                            |
| Inbound MCP                              | none (only an outbound stub at `app/api/mcp.py`)  | Streamable HTTP, FastAPI sidecar                                             | Real protocol surface; ASGI-native for SSE; separate scaling shape.                                          |
| Outbound MCP                             | `app/api/mcp.py` blueprint (stubs)                | Renamed `app/api/mcp_connections.py`                                         | Clarifies direction in the namespace.                                                                        |
| Wiki commit ownership                    | Flask AND worker both call `wiki_git.commit_file` | All callers INSERT `commit_outbox`; `commit_writer` is the only git writer   | Eliminates cross-process race by construction; ACID across business write + commit intent; audit comes free. |
| Auth for tools                           | none                                              | per-user PAT (`mcp_<32hex>`), sha256-hashed, expiring, revocable             | Real auth + audit per agent.                                                                                 |
| Token hashing                            | n/a                                               | sha256 of high-entropy random                                                | bcrypt is for low-entropy passwords; sha256 is correct here.                                                 |
| Audit                                    | events table fires on triggers only               | `commit_outbox` is the audit log; every write lives there forever            | Foundational requirement once external agents can mutate state. No separate `audit_log` table needed.        |
| Subscriptions                            | none                                              | MCP `resources/subscribe`; PG `LISTEN/NOTIFY` fan-out; in-memory per-replica | Real-time multi-agent collab.                                                                                |
| Pubsub mechanism                         | n/a                                               | Postgres `LISTEN/NOTIFY`                                                     | Native, real-time, no polling.                                                                               |
| Web framework for long-lived connections | Flask (sync, WSGI)                                | FastAPI/Uvicorn (async, ASGI) for the MCP service                            | SSE without fighting the framework.                                                                          |
| Deployment shape                         | one Flask container, one worker container         | Flask + Huey worker + commit_writer + MCP sidecar + Postgres + Redis         | Each service scales independently; commit_writer pinned to exactly one replica.                              |

## Operational concerns

The architecture above is correct in steady state; this section covers
what makes it production-operable.

### Observability

Every service emits:

- **Structured logs** (JSON, single line per event) to stderr. Mandatory
  fields on every line: `ts`, `level`, `service`, `request_id`,
  `user_id` (when in a user context), `outbox_id` (when applicable).
  Aggregator-agnostic — pick at deploy time.
- **Prometheus metrics** at `/metrics` (Flask, MCP server,
  `commit_writer`). Required series:
  - `commit_outbox_pending` (gauge, labeled by action) — depth of
    work waiting on `commit_writer`
  - `commit_outbox_drain_seconds` (histogram) — time from `pending`
    to terminal state
  - `commit_outbox_failed_total` (counter, labeled by error code)
  - `mcp_session_count` (gauge) — active sessions per replica
  - `mcp_subscription_count` (gauge) — active subs per replica
  - `mcp_tool_call_seconds` (histogram, labeled by tool name +
    outcome)
  - `flask_outbox_enqueue_seconds` (histogram) — write-endpoint
    latency budget
- **OpenTelemetry traces** with W3C Trace Context propagated across
  the MCP → Flask → `commit_writer` → reindex/trigger fan-out chain.
  `commit_writer` continues the parent span found in the outbox row's
  payload (carry the traceparent header in the outbox row's
  `payload.trace`).
- **Error tracking** (Sentry or equivalent). Capture: every `failed`
  outbox row, every 5xx from Flask write endpoints, every
  unhandled exception in `commit_writer`.

### Health and readiness

| Service         | `/healthz` (liveness) | `/readyz` (readiness)                                                   |
| --------------- | --------------------- | ----------------------------------------------------------------------- |
| Flask app       | process answers       | Postgres reachable, Redis reachable                                     |
| MCP server      | process answers       | Flask reachable, Postgres `LISTEN` connection healthy                   |
| Huey worker     | process answers       | Postgres reachable, Redis reachable, last-heartbeat within 10s          |
| `commit_writer` | process answers       | Postgres reachable, last successful drain within 30s OR no pending rows |

Orchestrator pulls liveness probes constantly; rolling deploys gate on
readiness. Failed `readyz` does not bounce the process — it stops the
LB from routing.

### `commit_writer` failover and stuck-row recovery

`commit_writer` is pinned to one replica, but a process can crash
mid-drain and leave a row in `running` indefinitely. The next replica
(or the same one after restart) must reclaim it.

A janitor task on the `commits_huey` queue runs every 30 s and resets
rows stuck in `running` with `started_at < now() - interval '60
seconds'` back to `pending`, incrementing `attempts`. After 5
attempts, the row goes to `failed` with `error='exceeded_attempts'`
and a paging alert fires.

The 60 s threshold is bigger than the longest legitimate commit
(large files, slow disk) but small enough that a crashed writer
unblocks within a minute.

### Backpressure

When `commit_outbox_pending` grows faster than `commit_writer` drains,
the system has three escalating responses:

1. **Warning at 100 pending rows.** Metric crosses threshold; alert
   fires; no behavior change.
2. **Soft shed at 1000 pending rows.** Flask write endpoints return
   `503 Retry-After: 5` for non-MCP traffic (UI editor saves) and
   reject new outbox INSERTs from `documents_huey` with a transient
   error so the worker retries with backoff. MCP tool calls still
   accept but get a longer synchronous-budget timeout that surfaces
   as `202` more often.
3. **Hard shed at 10000 pending rows.** All write endpoints return
   `503` until the queue drains below soft shed. Reads remain
   available.

Thresholds are config; numbers above are starting points.

### Idempotency on writes

Every Flask write endpoint accepts `Idempotency-Key` (UUID v4 from
the client). Flask stores it on the outbox row in a new column
`idempotency_key TEXT` with a unique partial index on
`(user_id, idempotency_key) WHERE idempotency_key IS NOT NULL`.

Re-submission of the same key (e.g. agent retries after a network
hiccup) returns the existing outbox row instead of enqueueing a
duplicate. This applies to ALL writes, not just `update_doc_nl`.

### Encryption

- **At rest:** Postgres data directory on encrypted volumes (KMS-managed
  keys at the cloud-provider layer is sufficient; PG TDE not required).
  Wiki working tree on the same encrypted volume.
- **In transit:** TLS on every external boundary (load balancer →
  client). Intra-cluster MCP → Flask uses mTLS (or a private-network
  shared secret if the cluster has private networking guarantees).
- **Postgres connections:** TLS required, including from local dev
  (`sslmode=require` in DSN).
- **Token storage:** sha256 hashes only; raw tokens never persisted.

### Backup and recovery

Two state stores need backup policy:

- **Postgres** — point-in-time recovery via WAL archiving. Standard
  cloud-provider managed PG handles this. RPO target: 5 minutes; RTO
  target: 30 minutes for restore from snapshot.
- **Wiki working tree** — git repo on disk. Push to a remote on every
  commit OR snapshot the volume on the same cadence as the PG WAL
  archive. The git remote is the simpler path; `commit_writer` runs
  `git push origin main` after every successful commit (or batches
  every N seconds).

The two stores can briefly disagree (PG ahead of git remote, or vice
versa). The outbox row reconciles: rows with `status='committed'` and
`result_sha` not present in the working tree on restore = need
replay against a clean clone.

### Schema migrations

Postgres migrations land via the existing numbered-`.sql`-files
mechanism in `app/db/migrations/` (already wired). Online-safe rules:

- Never drop or rename a column in a single migration if the column is
  read by deployed code. Deploy in two phases (add new column → ship
  code → drop old column).
- Add columns with defaults using `DEFAULT … NOT VALID` then
  `VALIDATE CONSTRAINT` to avoid full-table rewrites.
- Add indexes `CONCURRENTLY` for tables with active writes.

Migration test runs against a Postgres instance in CI; not a SQLite
shim.

### Graceful shutdown

Each service handles `SIGTERM`:

- **Flask:** stop accepting new requests, drain in-flight (cap 30 s),
  exit. Long-poll-on-`commit_done` handlers exit early with `202`
  pointing the client at the outbox id.
- **MCP server:** stop accepting new sessions, send
  `notifications/cancelled` to active sessions, drain SSE writes
  (cap 5 s), close LISTEN connections, exit.
- **Huey workers and `commit_writer`:** finish current task, refuse
  new work, exit. `SELECT FOR UPDATE` row is released on transaction
  rollback if the task hadn't completed; janitor recovers.

### Operational runbooks (hooks)

The doc proper doesn't carry runbooks; this section enumerates the
named alarms and where the runbook lives:

| Alarm                                 | Where to look                                                                                         |
| ------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| `commit_outbox_pending` > soft shed   | `commit_writer` logs; PG `pg_stat_activity` for the writer's session; check for stuck `running` rows. |
| `commit_writer` `readyz` failing      | `commit_writer` logs; check Postgres reachability; check working-tree disk fullness.                  |
| Outbox row in `failed` with retry max | Logs scoped to that outbox id; the original enqueuer's logs by `request_id`.                          |
| Flask 5xx rate spike                  | Flask logs; commonly a downstream Postgres/Redis issue; check `/readyz`.                              |
| MCP `mcp_session_count` collapse      | Replica restarted or LB drain; clients reconnect automatically; investigate restart cause.            |

Concrete runbook content lives in `docs/ops/` once the system ships.

## Forward-compatibility hooks

The architecture leaves named extension points for capabilities that
are out of scope today but predictable enough that the schema should
not preclude them.

### Multi-tenancy / org isolation

Every table that the [Auth](#auth), [Async jobs](#async-jobs), and
[Outbox](#outbox--schema-and-protocol) sections introduce gets a future
`org_id BIGINT REFERENCES orgs(id)` column. v0 ships with the column
absent; the migration that introduces orgs adds it with `DEFAULT NULL`
and backfills based on `users.org_id` once the `orgs` table exists.
Tokens become org-scoped; the token resolution step adds `org_id` to
the request context the MCP service injects on Flask calls.

Indexes on `(org_id, …)` replace today's `(user_id, …)` indexes via the
two-phase migration pattern.

### RBAC / scoped tokens

Token table gets `scopes JSONB NOT NULL DEFAULT '["*"]'`. Bearer
middleware checks the scope before dispatch. v0 leaves the column
absent and treats every token as `["*"]`; introducing the column is a
single migration with no code change required to existing tokens
(default applies).

### Rate limiting

Per-token Redis token bucket. Middleware in the MCP service checks
`rate:token:<id>` before dispatch; Flask middleware checks
`rate:user:<id>` for non-MCP traffic. Buckets size by token tier (set
on the token at creation). v0 ships with limits unenforced (a single
config flag toggles the middleware); add tiers and enforcement once
the first abuse signal lands.

### API versioning

The MCP transport endpoint mounts at `/api/v1/mcp` from day one
even though there's only one version. New transport revisions go to
`/api/v2/mcp` with the v1 endpoint kept for the protocol-supported
deprecation window (typically 12 months). Same for Flask `/api/v1/...`
on every endpoint that ships.

### Per-doc permissions

A `doc_permissions` table that the write endpoints consult before
inserting into `commit_outbox`. v0 has no such table; the check is a
no-op. Adding it is one migration plus one helper call inside each
write handler.

### Distributed tracing

W3C `traceparent` propagates already (from the MCP service into the
Flask call, from Flask into the outbox row's payload). The
`commit_writer` continues that span. No further hooks needed; this is
purely an operational rollout.

## Out of scope

| Concern                                         | Out of scope here, where it lands                                                                 |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| Outbound MCP dispatch from triggers             | Trigger destination work, separate doc.                                                           |
| Persistent subscriptions across reconnects      | Clients re-subscribe; if proven flaky in practice, revisit.                                       |
| Streaming partial results from `update_doc_nl`  | Job is `pending`/`succeeded`/`failed`; agents don't need token-level progress.                    |
| Resource templates (`resources/templates/list`) | Flat `resources/list` is fine until the wiki has thousands of docs.                               |
| HTTP+SSE legacy MCP transport                   | Streamable HTTP only. Bridge if a real client breaks.                                             |
| Multiple `commit_writer` replicas               | One is correct for the working-tree contract. Sharding by path range is a future scaling concern. |

## Open questions

- **Streamable HTTP vs HTTP+SSE.** Some agent runtimes only speak the
  older transport. Streamable HTTP first; bridge if needed.
- **Synchronous-response budget for write tools.** 5 s is a guess.
  Real distribution lives in observability — tighten or loosen once
  measured.
- **Outbox retention.** `committed`/`stale_base`/`failed` rows live
  forever as audit. Volume is a write per agent edit; manageable for
  years. Move cold rows to a partition or archive table when query
  performance degrades — not a v0 problem.
- **MCP service co-location.** Same cluster as Flask, or behind a
  public-facing edge with its own TLS? Affects the auth model
  between MCP and Flask (mTLS in-cluster is cheap; over public
  internet needs more thought).
- **Internal-header trust.** The MCP service injects
  `X-Internal-User-Id` on Flask calls. Requires either mTLS or a
  shared secret enforced at the Flask boundary. Pick one and document.
- **Tool description sharing.** If the chat-agent and MCP surfaces
  both expose `search_wiki`, do their descriptions diverge or stay
  aligned? Default: each owns its description; cross-pollinate
  through review.
- **`stale_paths` payload size.** Heavily-subscribed agents could
  accumulate many notifications between tool calls. Cap and surface
  truncation explicitly on the result.

## Relationship to other docs

- [mcp-server.md](./mcp-server.md) — the alternative architecture this
  doc is paired with. Same product surface; different state ownership,
  storage, transport, and deployment.
- [seams.md](../seams.md) — needs an updated row pointing inbound MCP
  at this sidecar, outbound at `app/api/mcp_connections.py`, and
  flagging `wiki_git.commit_file` as writer-only (callable only from
  `commit_writer`).
- [architecture_diagram.md](../architecture_diagram.md) — the "as
  built" snapshot; this doc describes the next shape.
- [agents/document-updater.md](../agents/document-updater.md) — the
  agent invoked by `update_doc_nl`; in this design it INSERTs an
  outbox row and stops calling `commit_file` directly.
- [tool-design](../tool-design/tool-design.md) — the in-process
  chat-agent tool primitives this proposal stops sharing the registry
  with.
