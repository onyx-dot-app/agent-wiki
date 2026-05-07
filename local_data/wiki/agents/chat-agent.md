# Agent harness — Chat Agent

> **Part of agent-workspace v0.** See the master doc
> [`../architecture_and_progress.md`](../architecture_and_progress.md) for
> the cross-area map. Sister doc:
> [agents/document-updater.md](document-updater.md). Frontend rendering
> of the chat lives in [frontend/frontend.md](../frontend/frontend.md).
> This doc owns the in-app chat experience: the LLM loop primitive, the
> HTTP endpoint, the LLM-error mapping, and the conversation persistence
> work that's still TBD.

_Last updated: 2026-05-06_

---

## Design

### LLM seam (shared across agents)

`app/llm/client.py:complete()` is the only path to a provider — no other
module imports `anthropic`/`openai`. Returns the normalized
`{text, tool_calls, stop_reason, usage, raw}` shape. Anthropic system
prompts get `cache_control: ephemeral` so prompt caching helps within
multi-iteration loops. Provider/model/keys come from
`app.llm.settings.get()`.

`complete()` raises `LLMError(code, message)` for both up-front
configuration problems and SDK errors. Codes:

| code | meaning |
|---|---|
| `not_configured` | no provider/model/key set |
| `auth` | provider returned 401 |
| `rate_limit` | provider returned 429 |
| `network` | connection failed |
| `config` | client construction failed |
| `bad_request` | provider returned 400 (usually our fault) |
| `provider` | other provider error |
| `unknown` | escape hatch |

### Loop primitive

`app/llm/agents/chat.py:run_chat_loop` — pure function, message-list-in /
message-list-out, multi-iteration tool use:

```python
def run_chat_loop(
    messages: list[Message],
    *,
    tools: list[dict] | None = None,
    tool_dispatch: Callable[[name, args], Any] | None = None,
    model: str | None = None,
    max_iterations: int = 8,
) -> list[Message]:
```

- Mutates `messages` in place AND returns it.
- Each iteration: `complete()` → append assistant turn → if tool calls,
  dispatch and append `role=tool` turns → loop.
- Termination: assistant turn with no tool calls (final message), or
  `max_iterations` exceeded (raises — runaway tool loops should not
  silently truncate).
- System prompt auto-injected from `prompts/chat.system.md` if not present.
- v0 ships `tools=None`, so the loop collapses to one completion call.

### HTTP endpoint (v0: stateless)

`POST /api/chat/messages` — client sends the **full conversation each turn**;
server runs the loop and returns the final assistant message.

Validation:
- `messages` is a non-empty list of `{role, content}` with
  `role ∈ {user, assistant}` and string content.
- Last message must be from `user`.

`LLMError.code` → HTTP status:

| code | status |
|---|---|
| `not_configured` | 503 |
| `auth`, `config`, `bad_request`, `provider`, `unknown` | 502 |
| `rate_limit` | 429 |
| `network` | 504 |

Anything else → 500 with `{error, code: "unknown"}` (logged). Frontend's
`ApiError` parses the `{error}` envelope and the chat UI keeps the user's
message visible on failure with a Retry affordance.

### Persistence (deferred to a follow-up)

`run_chat_turn(user_id, conversation_id, message)` is currently
`NotImplementedError`. When wired:
- Migration: `chat_conversations(id, user_id, title?, created_at)` and
  `chat_messages(id, conversation_id, role, content, tool_calls_json?,
  tool_call_id?, ts)`.
- Reads owner-scoped — chats are per-user.
- HTTP layer accepts an optional `conversation_id` and elides client-side
  history; returns the updated message list (or just the new turns).

### Tools (v0 plan)

When tools land, the dispatch table is owned by the HTTP layer (it has the
`current_user`). Initial set:

| Tool | Input | Output | Notes |
|---|---|---|---|
| `search_wiki` | `{query: str, limit?: int}` | bm25 hits | Wraps `app/wiki/search.py` |
| `read_doc`    | `{path: str}`              | `{path, body}` | Validates path |
| `propose_doc_edit` | `{path, body, message?}` | draft id | UI surfaces a diff for the user to accept |
| `list_my_triggers` | `{}` | list | Filtered to `current_user.id` |
| `upsert_trigger` | `{id?, scope_path, kind, nl_description, ...}` | trigger | Owner-scoped |
| `delete_trigger` | `{id}` | `{ok}` | Owner-scoped |

`propose_doc_edit` is a draft, not a direct write — user has to accept in
the UI. Keeps the agent honest while we don't have eval data.

### Why tool-less in v0
The brief says "answer questions about the wiki with a search." That's
table stakes. Everything else (`read_doc`, edits, trigger CRUD) is
incremental and rides on the same dispatch plumbing once we're confident
in the loop.

### Cost
Each turn pays for the system prompt + full prior conversation. Anthropic
system-prompt caching (already wired in `client.py`) helps within a
session; cross-session caching is fine because the system prompt is
stable. Conversation length is the unbounded growth axis — eventually
we'll need turn pruning, but not in v0.

---

## Progress

### Working
- `app/llm/client.py` — full implementation, both providers, prompt
  caching, normalized return shape, `LLMError` taxonomy.
- `app/llm/settings.py` — DB-backed read/upsert (with the no-row bug
  flagged in [flask-and-apis](../flask-and-apis/flask-and-apis.md)).
- `run_chat_loop` — full implementation; validated guard rails (tools
  imply tool_dispatch; loop raises on runaway).
- `POST /api/chat/messages` — real, stateless. Robust error mapping.
- System prompt slot (`load_prompt("chat.system")` invoked when the
  prompt file exists).
- `complete()` returns normalized `tool_calls` for both providers; the
  loop is provider-agnostic.

### Stubbed, not wired
- `run_chat_turn` (persistence-aware wrapper) — `NotImplementedError`.
- `chat_conversations` / `chat_messages` tables — not migrated.
- All tools — none registered yet.
- Frontend chat view — wired (see
  [frontend](../frontend/frontend.md)) but session-only state.

---

## Work breakdown (Next up)

### F. Chat HTTP + persistence

1. **Confirm `prompts/chat.system.md` exists** (or create it). Keep terse:
   "You're an assistant for an org wiki. Be concise. Cite docs by path."
2. **Search tool** — wrap `wiki.search.search` with the input schema
   above. Pass to `run_chat_loop` from the HTTP layer.
3. **Persistence migration** — `chat_conversations` + `chat_messages`.
4. **`run_chat_turn`** — load prior turns, append new user message, call
   `run_chat_loop`, persist new turns, return payload.
5. **HTTP layer** — switch to optionally accept `conversation_id`; persist.
6. **Frontend** — list past convos in a left sub-pane; URL keeps
   `conversation_id`. See [frontend](../frontend/frontend.md).
7. Add `read_doc` and `list/upsert/delete` trigger tools once the
   triggers domain lands (see
   [natural-language-triggers](../natural-language-triggers/natural-language-triggers.md)).

### Open questions
- Streaming responses? Not required for v0; would force us to change the
  HTTP shape and the loop function. Defer until UX demands it.
- Tool-result truncation? Search hits can be large. v0: cap snippets +
  result count in the tool itself, not in the loop.
