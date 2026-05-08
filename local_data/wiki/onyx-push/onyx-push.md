# Onyx-side push integration

> **Part of agent-wiki v0.** See the master doc
> [`../architecture_and_progress.md`](../architecture_and_progress.md) for
> the cross-area map. This doc owns the contract by which Onyx pushes
> deltas (document changes from public connectors) into agent-wiki
> so doc-updater agents can keep wiki pages current. The actual code on
> the Onyx side ships from a different repo; the receiving endpoint lives
> in [flask-and-apis](../flask-and-apis/flask-and-apis.md). The
> doc-updater agent that processes the payload lives in
> [agents/document-updater.md](../agents/document-updater.md).

_Last updated: 2026-05-08_

---

## Design

### Goal (per V0 brief)
> "Onyx-side changes to push all document changes to this system, only public
> connectors for now."

The wiki should react to org-wide activity surfaced through Onyx
connectors (Slack threads, Drive docs, GitHub PRs, …). Whenever Onyx
indexes a new or updated document from a public connector, it enqueues
an async push to agent-wiki. Agent-wiki accumulates these records and
runs one LLM doc-updater pass per doc on a periodic schedule.

### Onyx-side architecture (decided 2026-05-08)

**Feature control:** Three env vars in `app_configs.py`:
- `AGENT_WIKI_ENABLED` — explicit boolean flag; feature is off unless this is `true`.
- `AGENT_WIKI_BASE_URL` — required when enabled.
- `AGENT_WIKI_API_KEY` — passed as a bearer token on every push request.

**Where to hook in:** At the end of `index_doc_batch` in
`backend/onyx/indexing/indexing_pipeline.py`, after
`primary_doc_idx_insertion_records` is known. Only enqueue for docs that
were actually indexed (new or updated) — unchanged docs are already
filtered out by `get_doc_ids_to_update` and never reach this point.

**Public connectors only:** Filter on `cc_pair.access_type == AccessType.PUBLIC`
before enqueueing. Look up via `get_connector_credential_pair` using the
`connector_id` and `credential_id` from the indexing adapter. Skip the push if
the pair is not public.

**Async via a new `agent_wiki_push` Celery queue:** A dedicated queue named
`agent_wiki_push` is added to the existing `light` worker's `-Q` list — no new
worker process is needed. Routing via a `@shared_task` with
`queue=OnyxCeleryQueues.AGENT_WIKI_PUSH` keeps the indexing path non-blocking
if agent-wiki is slow or unavailable. The task:
- `push_to_agent_wiki(doc_id, source, title, content, url, doc_updated_at)`
- Makes an HTTPS POST to `AGENT_WIKI_BASE_URL/api/documents/ingest`.
- Authenticates with `AGENT_WIKI_API_KEY` as a bearer token.
- Retries with exponential backoff on failure (up to 3 times).

**Payload shape:** Onyx already normalizes all connectors into a `Document`
with `sections`. We concatenate the text sections and send as `content`.

```json
{
  "content": "...",
  "title": "...",
  "source_type": "slack",
  "metadata": {
    "external_id": "<onyx-doc-id>",
    "url": "..."
  },
  "updated_at": "..."
}
```

`metadata` is opaque JSON passed through to the doc-updater agent.
Routing (which wiki page to update) is resolved on the agent-wiki side.
A 413 response means the payload exceeds agent-wiki's size cap — the
task logs a warning and does not retry.

### Surface in agent-wiki

`POST /api/documents/ingest` — receives a single document push from any
external system (Onyx today; other connectors in the future). The API
layer is intentionally dumb: it validates the payload, enforces the
admin-configured size cap, enqueues a task on `documents_huey`, and acks
**202** immediately. All routing (which wiki page to update) and
reconciliation happens in the background task via the doc-updater agent.

**Request body** (JSON, `application/json`):

| Field         | Type   | Required | Notes                                              |
| ------------- | ------ | -------- | -------------------------------------------------- |
| `content`     | string | yes      | Full document text. Must be non-empty.             |
| `title`       | string | no       | Display title from the source.                     |
| `source_type` | string | no       | Connector identifier — `slack`, `drive`, …         |
| `metadata`    | object | no       | Opaque JSON; passed through to the agent.          |
| `updated_at`  | string | no       | Source-side last-modified timestamp.               |
| `diff`        | string | no       | Diff vs. last pushed version, if known.            |

**Size cap:** `len(content) + len(diff)` must not exceed
`ingest_settings.max_doc_chars` (default 100,000; see admin config below).
Bounded jointly because both fields are LLM input on the consumer side.

**Responses:**
- `202 Accepted` — `{"queued": true, "task_id": "<huey-id>"}`. The push
  has been put on `documents_huey`. The HTTP request never waits for the
  agent.
- `400 Bad Request` — malformed JSON, missing/empty `content`, or any
  field with a wrong type. Body: `{"error": "<message>"}`.
- `413 Payload Too Large` — content + diff exceeds the cap. Body
  includes `limit` and `received` so callers can see the diagnosed size.
- `503 Service Unavailable` — failed to enqueue the background task
  (queue DB unreachable, etc.). The pusher should retry with backoff.

Auth: not yet implemented. Today the endpoint is open and matches the
`POST /api/webhooks/<source>` pattern (private network / auth proxy
front-end). Shared bearer token / HMAC validation lands when the
`api_tokens` table does — tracked in **Next up** below.

**Background task:** `app.tasks.document_update.process_pushed_document`
on `documents_huey`. Stub today (`raise NotImplementedError`) — picking
target page(s) and running the doc-updater agent is the next chunk of
work. The API contract is fixed.

### Admin config: max document size

`POST /api/admin/ingest` (admin-gated, separate from `/admin/llm` and
`/admin/web`). Single setting:

- `max_doc_chars` — integer. Default **100,000**. Bounds enforced
  server-side: 1,000 ≤ value ≤ 5,000,000. Persisted in the `ingest_settings`
  single-row table (migration `0007_ingest_settings.sql`).

Schema in code: `app/ingest/settings.py:IngestSettings`.

### What we're explicitly **not** doing in v0
- Two-way sync (agent-wiki pushing back to Onyx).
- Routing payloads to multiple wiki docs from one Onyx event.
- Private-connector data — public only, by spec.
- Backfill of historical Onyx data — only forward deltas.

---

## Progress

### Working
<<<<<<< Updated upstream
- **Onyx side (shipped on `bo/agent_wiki_push`):**
  - `AGENT_WIKI_ENABLED` / `AGENT_WIKI_BASE_URL` / `AGENT_WIKI_API_KEY` env vars in `app_configs.py`.
  - `OnyxCeleryQueues.AGENT_WIKI_PUSH` + `OnyxCeleryTask.PUSH_TO_AGENT_WIKI` constants.
  - `backend/onyx/background/celery/tasks/agent_wiki/tasks.py` — `push_to_agent_wiki` Celery task.
  - `_maybe_enqueue_agent_wiki_push` helper in `indexing_pipeline.py` — enqueues after `primary_doc_idx_insertion_records` for public connectors only.
  - `agent_wiki_push` queue added to light worker in `supervisord.conf`.
=======
- `POST /api/documents/ingest` validates the payload, enforces the
  admin-configured size cap, enqueues `process_pushed_document` on
  `documents_huey`, and acks 202.
- `GET/PUT /api/admin/ingest` for the `max_doc_chars` setting (default
  100,000). Persisted in `ingest_settings`.
- Migration `0007_ingest_settings.sql`.
>>>>>>> Stashed changes

### Stubbed
- `app.tasks.document_update.process_pushed_document` runs on the right
  queue but raises `NotImplementedError`. Routing + agent invocation is
  the next implementation step.

### Next up

<<<<<<< Updated upstream
**Agent-wiki side (deferred):**

**Agent-wiki side (next):**
1. Implement `POST /api/documents/ingest`: validate bearer token → insert into
   `pending_doc_updates` → return `{id}`.
2. Migration adding `pending_doc_updates` and `api_tokens` tables.
3. Periodic drain task in `tasks.periodic`.
=======
**Onyx side (Bo):**
1. Add `AGENT_WIKI_BASE_URL` + `AGENT_WIKI_API_KEY` to `app_configs.py`.
2. Create `backend/onyx/background/celery/tasks/agent_wiki/tasks.py` with
   `push_to_agent_wiki` task (`@shared_task` on existing light queue,
   exponential backoff retry, bearer token auth).
3. Update the push body to the agent-wiki contract above
   (`content` required, `title` / `source_type` / `metadata` /
   `updated_at` / `diff` optional). Onyx's normalized `Document.sections`
   gets joined into `content`.
4. Enqueue from `index_doc_batch`: after `primary_doc_idx_insertion_records`
   is known, for each successfully indexed doc, check `AGENT_WIKI_ENABLED`
   and `cc_pair.is_public`, then enqueue.

**Agent-wiki side:**
1. Implement `process_pushed_document`: pick target wiki page(s), run the
   doc-updater agent, commit + reindex + trigger fan-out on body change.
2. Add bearer-token / HMAC auth to `/api/documents/ingest`. Likely an
   `api_tokens` table with admin-managed tokens.
3. Idempotency: optionally accept a client-supplied dedup key
   (`source_type` + external id) to drop duplicate pushes from retries
   without enqueuing twice.
>>>>>>> Stashed changes

---

## Cross-link
- Doc-updater agent contract: `agents/document-updater.md`
- `pending_doc_updates` table design and drain task: `background-tasks/background-tasks.md`
- Trigger fan-out runs after each ingest-driven commit:
  `natural-language-triggers/natural-language-triggers.md`
