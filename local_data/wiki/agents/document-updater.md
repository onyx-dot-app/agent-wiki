# Agent harness — Document Updater

> **Part of agent-wiki v0.** See the master doc
> [`../architecture_and_progress.md`](../architecture_and_progress.md) for
> the cross-area map. Sister doc:
> [agents/chat-agent.md](chat-agent.md). This doc owns the design and
> progress for the LLM agent that reconciles a wiki doc with new info
> coming in from a connector update / webhook / explicit ingest call.

_Last updated: 2026-05-06_

---

## Design

### Goal
Given a doc and a payload describing recent activity, decide whether the doc
needs to change and, if so, produce the new full body. **This is the
trickiest part of the V0 system** (per the original brief).

### Hard constraints
1. **Don't bloat the doc.** Surgical edits beat full rewrites.
2. **Don't drop information.** The payload is incremental, not authoritative.
3. **Preserve structure and tone.** The agent is editing, not rewriting.
4. **Be explicit about no-op.** Return the literal `NO_CHANGE` sentinel when
   nothing should change — saves a commit and avoids noisy git history.

These are encoded in `app/llm/prompts/document_updater.system.md`.

### Interface

```python
# app/llm/agents/document_updater.py
def run(doc_id: str, current_body: str, payload: dict, source: str) -> str | None:
    """Return new body, or None if NO_CHANGE."""
```

Driven by the Huey task `tasks.document_update.update_document_from_payload`
(see [background-tasks](../background-tasks/background-tasks.md)), which:
1. Loads `current_body` via `wiki.git.read_file`.
2. Calls `run(...)`.
3. If new body returned: `wiki.git.commit_file(...)` → enqueue
   `reindex_path` → fan out to trigger evaluator (see
   [natural-language-triggers](../natural-language-triggers/natural-language-triggers.md)).
4. Records an `event` row of kind `doc.update` regardless.

### Why a single LLM call (v0)
The V0 brief explicitly accepts the cost: "V0 can just be an LLM comparison
on every doc; worry about scaling later." Watch the cost line.

### Cost / scaling concerns (deferred)
- **Debounce / batching.** Many connector updates can hit the same doc in a
  short window. v0: 1 task = 1 LLM call. Later: coalesce within a window.
- **Per-doc lock.** If updates pile up, run them serially so two passes
  don't race on the same git path.
- **Provider failover.** `complete()` raises `LLMError` with a `code`
  (`auth`, `rate_limit`, `network`, …); the task should catch and surface
  these to the events log instead of dying silently.

### Prompt design (current)
- **System prompt** (`document_updater.system.md`): the four hard
  constraints above + output contract (NO_CHANGE or full body).
- **User prompt** (`document_updater.user.md`): doc id, source, current body,
  payload — interpolated by `.format()`.

Both are checked into git so prompt history is durable. **Don't squash** —
the V0 brief calls out the importance of being able to look back at past
versions while we lack eval data.

### LLM seam
This agent calls `app.llm.client.complete()` and nothing else from the
provider SDKs. Provider/model/keys come from `app.llm.settings.get()`
(DB-backed, admin-managed). See
[flask-and-apis: Stabilize the LLM seam](../flask-and-apis/flask-and-apis.md#a-stabilize-the-llm-seam)
for the known no-row bug.

### Out of scope (do not build into this agent yet)
- **Multi-doc orchestration** — the agent only edits the doc passed in.
  Routing payload → doc(s) is the caller's problem.
- **Tool use** — v0 is a single non-tool-using completion. If we need
  context (e.g. "what's in the parent dir?"), the caller pre-fetches and
  splices into the prompt.
- **Streaming** — irrelevant; this is a background task.

---

## Progress

### Working
- Prompts written and committed.
- Provider-agnostic LLM client (`app/llm/client.py:complete`) with
  Anthropic prompt-caching on system messages — important since each task
  re-uses the same system prompt across docs.
- `LLMError` taxonomy from `complete()` (codes: `not_configured`, `auth`,
  `rate_limit`, `network`, `bad_request`, `provider`, `unknown`).

### Stubbed, not wired
- `app/llm/agents/document_updater.py:run` — currently raises
  `NotImplementedError`.
- `tasks.document_update.update_document_from_payload` — stub.
- `tasks.document_update.update_document_direct` — stub (used when an agent
  PUTs a doc directly through the API rather than from a payload).
- `POST /api/documents/ingest` and `POST /api/documents/<doc_id>` — stubs.

---

## Work breakdown (Next up)

### H. Document-updater agent + ingest path

1. **Implement `run(...)`:**
   - Build messages from prompts.
   - Call `client.complete(messages, max_tokens=...)`.
   - Parse output: literal `NO_CHANGE` sentinel → `None`; else strip and
     return as new body. Defensive: reject responses that look like a
     wholesale rewrite (e.g. less than 50% length retained) unless the
     payload explicitly authorizes.

2. **Wire `update_document_from_payload`:**
   - Read body, call `run`, commit on change, enqueue reindex, write event.
   - On `LLMError`, write an event of kind `doc.update.failed` with the
     code; don't crash the task.

3. **Wire the two API entry points:**
   - `POST /api/documents/ingest` — generic connector update; enqueues
     `update_document_from_payload`.
   - `POST /api/documents/<doc_id>` — agent updating a specific doc; the
     V0 brief calls this out separately from generic ingest.

4. **Trigger fan-out** (after [natural-language-triggers](../natural-language-triggers/natural-language-triggers.md)
   D lands): from the same task, evaluate matching delta triggers and
   record `trigger.fire` events.

### Open questions
- How much context does the agent need beyond the doc body? V0 says none;
  but parent-directory context (especially the eventual `agents.md`) might
  be cheap and high-value. Keep it minimal until we have eval data.
- What payload shapes do connectors actually send? Defines how rigid the
  user-prompt template can be. Tracked under
  [onyx-push](../onyx-push/onyx-push.md).
