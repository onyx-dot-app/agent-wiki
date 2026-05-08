# Agent harness — Chat Agent

> **Part of agent-wiki v0.** See the master doc
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

**Location context** (added when the chat panel lands). The body grows an
optional `location: { path: string }` field carrying the user's current
spot in the wiki (a doc path or directory path). The HTTP layer splices
it into the system prompt — the agent uses it to answer "what's here?"
and to scope `propose_doc_edit` / `propose_create_trigger` calls.

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
`current_user`).

**Read-only tools (no acknowledgement needed):**

| Tool | Input | Output | Notes |
|---|---|---|---|
| `search_wiki` | `{query: str, limit?: int}` | bm25 hits | Wraps `app/wiki/search.py` |
| `read_doc`    | `{path: str}`              | `{path, body}` | Validates path |
| `list_dir`    | `{path: str}`              | `{path, children: [{path, kind: "file"|"dir"}]}` | Walks the wiki tree |
| `list_my_triggers` | `{}`                  | list | Filtered to `current_user.id` |

**Write / propose tools (require user acknowledgement — propose-and-apply):**

The agent **never writes directly**. These tools emit a draft into the
chat thread; the panel renders it as a card with **Apply** / **Reject**
buttons. The HTTP layer takes the user's choice, performs the real call
(or not), and reports the outcome back as a tool-result on the next turn.

| Tool | Input | On Apply | On Reject |
|---|---|---|---|
| `propose_doc_edit` | `{path, body, message?}` | `PUT /api/documents/file` | tool-result `{applied: false}` |
| `propose_create_trigger` | `{scope_path, kind, nl_description}` | `POST /api/triggers` (owner = current user) | tool-result `{applied: false}` |
| `propose_update_trigger` | `{id, ...}` | `PUT /api/triggers/<id>` (owner-scoped) | tool-result `{applied: false}` |
| `propose_delete_trigger` | `{id}` | `DELETE /api/triggers/<id>` (owner-scoped) | tool-result `{applied: false}` |

Acknowledgement is the contract — keeps the agent honest and gives users
durable control over what lands while we lack eval data. (`upsert_trigger`
/ `delete_trigger` from the earlier plan are folded into the
`propose_*_trigger` family — no direct-write trigger CRUD from the agent.)

### Wiki traversal capability — required

The agent must be able to **traverse the wiki and update associated
pages**, not just answer the current page. The minimum tool set above
(`search_wiki` + `read_doc` + `list_dir` + the propose-write family)
covers this. **Open question:** whether to additionally expose a
**filesystem-style "bash" tool** (e.g. `wiki_shell({command})` running
read-only commands like `ls`, `grep`, `cat`, `find` against the wiki
working tree) so the agent can do richer multi-step exploration than
bm25 surfaces. Pros: matches how coding agents already work, lower
prompt overhead than chaining `list_dir` + `read_doc`. Cons: another
attack surface, requires careful sandboxing (no writes, no escapes from
the wiki root), output truncation. **Decision deferred** — start with
the structured tools; add the bash tool only if structured calls feel
too clunky in dogfooding.

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

### F. Chat HTTP + persistence + tools

1. **Confirm `prompts/chat.system.md` exists** (or create it). Keep terse:
   "You're an assistant for an org wiki. Be concise. Cite docs by path."
2. **Location context** — accept `location: { path }` on
   `POST /api/chat/messages`; splice into the system prompt for the turn.
3. **Read-only tool set** — wire `search_wiki`, `read_doc`, `list_dir`,
   `list_my_triggers` (last one filtered to `current_user.id`). Pass to
   `run_chat_loop` from the HTTP layer.
4. **Wiki traversal** — make sure the read-only tool set actually
   supports traversing the wiki and reasoning across multiple pages. If
   the structured tools feel clunky in dogfooding, evaluate adding
   `wiki_shell({command})` (sandboxed read-only `ls`/`grep`/`cat`/`find`
   against the wiki working tree). Track in the open questions below.
5. **Propose-and-apply tools** — `propose_doc_edit`,
   `propose_create_trigger`, `propose_update_trigger`,
   `propose_delete_trigger`. Each emits a draft tool-call into the
   thread; the panel renders the Apply / Reject card; on Apply the HTTP
   layer performs the real API call (owner = current user for trigger
   ones); the outcome is replayed as a tool-result on the next turn.
6. **Persistence migration** — `chat_conversations` + `chat_messages`.
7. **`run_chat_turn`** — load prior turns, append new user message, call
   `run_chat_loop`, persist new turns, return payload.
8. **HTTP layer** — switch to optionally accept `conversation_id`; persist.
9. **Frontend** — `<ChatPanel>` rendering, propose-and-apply card UX,
   conversation sub-pane. See [frontend](../frontend/frontend.md).

### Open questions
- **Wiki traversal: structured tools vs. a sandboxed bash tool?** The
  structured set (`list_dir` + `read_doc` + `search_wiki`) is enough in
  principle; a `wiki_shell` would be more ergonomic for multi-step
  exploration but is another attack surface. Start structured; revisit
  if dogfooding shows the agent floundering on cross-page edits.
- Streaming responses? Already wired (SSE) — chat panel reads via
  `apiStream`. Tool calls + propose-and-apply still need to fit cleanly
  into the SSE event shape.
- Tool-result truncation? Search hits and `read_doc` bodies can be
  large. v0: cap snippets + body slice in the tool itself, not in the
  loop.
