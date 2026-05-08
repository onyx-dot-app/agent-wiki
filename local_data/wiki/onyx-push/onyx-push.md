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

**Public connectors only:** Filter on `cc_pair.is_public` before
enqueueing. Look up via `get_connector_credential_pair_from_id` using
`IndexAttemptMetadata.connector_id` + `credential_id`. Skip the push if
`is_public` is false.

**Async via existing light queue:** No new worker or queue. Enqueue a
`@shared_task` on the existing `light` Celery queue so the indexing path
is never blocked by a slow or unavailable agent-wiki. The task:
- `push_to_agent_wiki(doc_id, source, title, sections, metadata, doc_updated_at, tenant_id)`
- Makes an HTTPS POST to `AGENT_WIKI_BASE_URL/api/documents/ingest`.
- Authenticates with `AGENT_WIKI_API_KEY` as a bearer token.
- Retries with exponential backoff on failure.

**Payload shape:** Onyx already normalizes all connectors into a `Document`
with `sections`. We concatenate the text sections and send as `content`.

```json
{
  "source": "slack",
  "external_id": "<onyx-doc-id>",
  "occurred_at": "2026-05-08T14:00:00Z",
  "target_path": null,
  "payload": {
    "title": "...",
    "url": "...",
    "content": "...",
    "updated_at": "..."
  }
}
```

`target_path` is null for now — routing (which wiki doc to update) is
TBD and will be resolved on the agent-wiki side.

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
- Nothing yet on either side.

### Stubbed
- `POST /api/documents/ingest` in agent-wiki raises `NotImplementedError`.

### Next up

**Onyx side (Bo):**
1. Add `AGENT_WIKI_BASE_URL` + `AGENT_WIKI_API_KEY` to `app_configs.py`.
2. Create `backend/onyx/background/celery/tasks/agent_wiki/tasks.py` with
   `push_to_agent_wiki` task (`@shared_task` on existing light queue,
   exponential backoff retry, bearer token auth).
3. Enqueue from `index_doc_batch`: after `primary_doc_idx_insertion_records`
   is known, for each successfully indexed doc, check `AGENT_WIKI_ENABLED`
   and `cc_pair.is_public`, then enqueue.

**Agent-wiki side (deferred):**
1. Implement `POST /api/documents/ingest`: validate HMAC → insert into
   `pending_doc_updates` → return `{id}`.
2. Migration `0007` adding `pending_doc_updates` and `api_tokens` tables.
3. Periodic drain task in `tasks.periodic`.

---

## Cross-link
- Doc-updater agent contract: `agents/document-updater.md`
- `pending_doc_updates` table design and drain task: `background-tasks/background-tasks.md`
- Trigger fan-out runs after each ingest-driven commit:
  `natural-language-triggers/natural-language-triggers.md`
