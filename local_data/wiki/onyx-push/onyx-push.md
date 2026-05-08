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

`POST /api/documents/ingest` (stub today). Backend behavior:
validate token → insert row into `pending_doc_updates` → return `{id}`
immediately. A periodic drain task in `tasks.periodic` processes all
pending rows per doc in one LLM pass.

**Dedup / idempotency:** `pending_doc_updates.id` is an idempotency key
(`sha256(source + external_id + occurred_at)`). Duplicate POSTs from
Onyx retries return the existing row without re-inserting.

Auth: shared signing secret (HMAC), same approach as
`POST /api/webhooks/<source>`.

### What we're explicitly **not** doing in v0
- Two-way sync (agent-wiki pushing back to Onyx).
- Routing payloads to multiple wiki docs from one Onyx event.
- Private-connector data — public only, by spec.
- Backfill of historical Onyx data — only forward deltas.

---

## Progress

### Working
- **Onyx side (shipped on `bo/agent_wiki_push`):**
  - `AGENT_WIKI_ENABLED` / `AGENT_WIKI_BASE_URL` / `AGENT_WIKI_API_KEY` env vars in `app_configs.py`.
  - `OnyxCeleryQueues.AGENT_WIKI_PUSH` + `OnyxCeleryTask.PUSH_TO_AGENT_WIKI` constants.
  - `backend/onyx/background/celery/tasks/agent_wiki/tasks.py` — `push_to_agent_wiki` Celery task.
  - `_maybe_enqueue_agent_wiki_push` helper in `indexing_pipeline.py` — enqueues after `primary_doc_idx_insertion_records` for public connectors only.
  - `agent_wiki_push` queue added to light worker in `supervisord.conf`.

### Stubbed
- `POST /api/documents/ingest` in agent-wiki raises `NotImplementedError`.

### Next up

**Agent-wiki side (deferred):**

**Agent-wiki side (next):**
1. Implement `POST /api/documents/ingest`: validate bearer token → insert into
   `pending_doc_updates` → return `{id}`.
2. Migration adding `pending_doc_updates` and `api_tokens` tables.
3. Periodic drain task in `tasks.periodic`.

---

## Cross-link
- Doc-updater agent contract: `agents/document-updater.md`
- `pending_doc_updates` table design and drain task: `background-tasks/background-tasks.md`
- Trigger fan-out runs after each ingest-driven commit:
  `natural-language-triggers/natural-language-triggers.md`
