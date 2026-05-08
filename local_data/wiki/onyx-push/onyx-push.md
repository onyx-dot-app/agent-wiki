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

_Last updated: 2026-05-06_

---

## Design

### Goal (per V0 brief)
> "Onyx-side changes to push all document changes to this system, only public
> connectors for now."

The wiki should react to org-wide activity surfaced through Onyx
connectors (Slack threads, Drive docs, GitHub PRs, …). Whenever Onyx
indexes a document change from a public connector, it forwards a small
event to agent-wiki, which queues a doc-updater pass.

### Surface in agent-wiki

`POST /api/documents/ingest` — already specced (stub today). Body:

```json
{
  "source": "onyx.slack",                 // <connector>.<source-id>
  "external_id": "<source-side id>",      // optional, for dedup later
  "occurred_at": "2026-05-06T14:00:00Z",
  "target_path": "projects/foo.md",       // optional hint; if omitted,
                                          // agent or routing logic decides
  "payload": { "...": "free-form" }
}
```

Auth: a per-source signed token (same approach as `POST /api/webhooks/<source>`).

Backend behavior: validate signature → write `events` row of kind
`webhook.in` → enqueue `tasks.document_update.update_document_from_payload`
(if `target_path` provided) or `route_payload_to_doc` (TBD; out of scope
for v0 unless we need it).

### What changes in Onyx

- A connector callback that fires after each indexed-document update.
- Filter: only **public** connectors and public docs (per the brief).
- Outbound HTTP to agent-wiki's ingest endpoint with the payload
  shape above. Reuse Onyx's existing webhook/queue infra; do not block
  the indexing path on this call.
- Configuration: agent-wiki base URL + per-source signing secret,
  managed in Onyx admin.

### What we're explicitly **not** doing in v0
- Two-way sync (agent-wiki pushing back to Onyx).
- Routing payloads to multiple wiki docs from one Onyx event.
- Private-connector data — public only, by spec.
- Backfill of historical Onyx data — only forward deltas.

### Open contract questions (resolve before Onyx-side work starts)
- **Payload shape per connector.** Slack thread vs. GitHub PR vs. Drive
  doc — they're shaped differently. Two options:
  1. Onyx normalizes to a common shape (URL, title, source, snippet,
     change type, timestamp).
  2. Pass through the connector-native payload and let the document-updater
     prompt handle variance.
  Lean toward (1) for v0 — simpler doc-updater prompt; we can specialize
  later.
- **Dedup / idempotency.** Onyx may retry; we should de-dup on
  `(source, external_id)` for a window. Cheap to add: an extra index on
  `events.payload_json` is awkward in SQLite, so consider a small
  `ingest_seen(source, external_id, ts)` table.
- **Routing — who picks `target_path`?** v0: Onyx picks. Later: an
  agent step on our side decides which doc(s) to update.

---

## Progress

### Working
- Nothing yet on the Onyx side.
- `POST /api/webhooks/<source>` and `POST /api/documents/ingest` exist as
  stubs in agent-wiki.

### Stubbed
- Both endpoints raise `NotImplementedError`.

### Next up
1. Lock the payload shape (option 1 vs. 2 above; recommend 1).
2. Implement `POST /api/documents/ingest` with signature verify + event
   record + enqueue.
3. Add `ingest_seen` dedup table + check.
4. Onyx-side: connector hook + outbound HTTP client + config storage.
5. Test contract end-to-end in a dev pair.

### Open questions
- Auth: shared signing secret per source (HMAC) or token-per-source? HMAC
  is fine and matches webhook conventions.
- Where does `target_path` come from on the Onyx side? Probably a
  per-connector mapping (e.g. "all Slack #project-foo updates →
  `projects/foo.md`") configured in Onyx.

---

## Cross-link
- Doc-updater agent contract: `agents/document-updater.md`
- Trigger fan-out runs after each ingest-driven commit:
  `natural-language-triggers/natural-language-triggers.md`
