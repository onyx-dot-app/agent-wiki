# Architecture (v0)

## Containers

| Container | Role |
|---|---|
| `backend`  | Flask app on :8080. Hosts the API. |
| `worker`   | Same image as backend, runs Huey consumer. |
| `frontend` | Next.js + TS UI on :3000. |
| `nginx`    | Reverse proxy on :80 — `/api/*` → backend, everything else → frontend. |

## Volumes

| Volume | Mount | Purpose |
|---|---|---|
| `app-data`  | `/data`  | `app.sqlite` (state) and `queue.sqlite` (Huey). |
| `wiki-data` | `/wiki`  | Git-backed wiki working tree. The backend shells out to `git`. |

## Storage

- **Wiki content & triggers** — files in the `wiki-data` volume, committed to git on every write. Triggers live under `.triggers/<id>.yaml`.
- **App state** — SQLite (`app.sqlite`): users, MCP connections, document metadata, trigger cache, events.
- **Search** — FTS5 virtual table (`documents_fts`) with bm25 ranking. Rebuilt by `tasks.reindex` after every doc commit.
- **Queue** — Huey on its own SQLite file (`queue.sqlite`).

## Data flow: doc gets updated as work happens

1. Connector or webhook posts to `/api/documents/ingest` (or `/api/webhooks/<source>`).
2. Backend records an `events` row and enqueues `update_document_from_payload`.
3. Worker pulls the task, calls the **document-updater agent** (`app/llm/agents/document_updater.py`).
4. If the agent returns a new body, the worker commits it via `app.wiki.git.commit_file`.
5. Worker enqueues `reindex_document` to refresh FTS.
6. Worker evaluates **delta triggers** scoped to the doc and to each parent directory; matched triggers dispatch (webhook / external service / agent message).

## Data flow: scheduled trigger

1. `tasks.periodic.evaluate_scheduled_triggers` (every 5 min) loads enabled `kind=schedule` triggers due now.
2. For each, the engine runs the configured action and records `trigger.fire`.

## Open questions

- **Cost** — every connector update triggers an LLM pass on the relevant doc. Need batching / debounce; right now it's an explicit accept-cost decision.
- **Doc bloat / loss** — the agent must avoid both growing docs unboundedly and dropping important context. The system prompt forbids both, but we'll need eval data to keep it honest.
- **Agent hand-off discipline** — coding agents are supposed to update high-level project plans here without spamming. Open: is this best done via MCP tool description, or as a skill the agent loads?
- **Permissioning** — out of scope for v0. Anything authenticated can read/write everything.
