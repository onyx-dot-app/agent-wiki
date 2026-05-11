# Chat Harness

The in-app chat ("Wiki AI Assistant") is a streaming, multi-turn,
tool-using agent over the wiki. This doc walks through how the loop
is built, what it streams, how messages and tool history get
assembled and persisted, and where the seams are if you want to
extend it.

The two adjacent docs are required reading:

- [LLM Interfaces](LLM%20Interfaces.md) — the `client.py` /
  provider seam and the `run_chat_loop_stream` primitive.
- [Background Tasks](Background%20Tasks.md) — chat-title generation
  rides `documents_queue`; activity cleanup rides `triggers_queue`.

## The picture

```
┌────────────────────────┐
│   ChatWidget (React)   │   keeps a session_id in localStorage,
│                        │   renders messages incrementally.
└──────────┬─────────────┘
           │ POST /api/chat/messages  {session_id, content}
           │   (SSE)
           ▼
┌──────────────────────────────────────────────────────────┐
│ app/api/chat.py:send_message                             │
│   1. validate session ownership                          │
│   2. persist user turn to chat_messages                  │
│   3. hydrate prior history (role+content only)           │
│   4. yield SSE frames as the loop streams                │
│   5. on clean close: persist assistant turn (with        │
│      full events JSON)                                   │
│   6. on first turn: enqueue generate_chat_title          │
└──────────────────────────┬───────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────┐
│ app/llm/agents/chat.py:run_chat_stream                   │
│   wraps run_chat_loop_stream with                        │
│     ─ system_prompt = chat.system.md                     │
│     ─ tools = TOOL_SPECS (from tools/ registry)          │
│     ─ tool_dispatch = registry.dispatch                  │
│     ─ agent_activity.agent_name_var = "Wiki AI Assistant"│
└──────────────────────────┬───────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────┐
│ run_chat_loop_stream  (the generic primitive)            │
│   for each iteration (≤ 8):                              │
│     drain client.stream(messages, tools=...)             │
│        ▸ yield text_delta as it arrives                  │
│        ▸ collect tool_call objects                       │
│     append assistant turn to messages                    │
│     if no tool_calls → yield {type: "done"}, return      │
│     for each tool_call:                                  │
│        result = tool_dispatch(name, args)                │
│        append {role: tool, tool_call_id, content}        │
│        yield {type: "tool_result", id, name, content}    │
│     yield {type: "iteration_done"}                       │
└──────────────────────────────────────────────────────────┘
```

The loop is generic — `run_chat_loop_stream` doesn't know about the
wiki, the system prompt, or the tool registry. `run_chat_stream` is
just a five-line wrapper that plugs them in. The same primitive
backs the read-only Q&A sub-agent (see
[LLM Interfaces](LLM%20Interfaces.md) §`wiki_qa.run`).

## The streaming protocol

`POST /api/chat/messages` returns `text/event-stream`. Each event is
a single SSE frame:

```
data: {"type": ..., ...}\n\n
```

| Event | Payload | Meaning |
|---|---|---|
| `text_delta` | `{text}` | One chunk of assistant tokens — append to the bubble. |
| `tool_call` | `{id, name, arguments}` | Agent invoked a tool. UI shows a "calling…" hint. |
| `tool_result` | `{id, name, content}` | Tool returned. Content is JSON-stringified for non-string results. |
| `iteration_done` | `{}` | One model turn finished. The loop will fire another. |
| `done` | `{}` | Final assistant turn (no tool calls). Stream closes. |
| `error` | `{code, message}` | Fatal. Stream closes after this. |

Headers:

- `Cache-Control: no-cache`
- `X-Accel-Buffering: no` — disables nginx buffering so the
  frontend sees deltas in real time. Both the dev proxy and prod
  nginx honor this.

The frontend's `apiStream` (`frontend/src/lib/api.ts`) is the SSE
parser: it splits on `\n\n`, concatenates `data:` lines within a
frame, and `JSON.parse`s the result. Malformed frames are skipped
silently.

## The loop

`app/llm/agents/chat.py:run_chat_loop_stream` is the workhorse.
Behavior contract:

- **`messages` is mutated in place.** Each iteration appends one
  `{"role": "assistant", "content": "...", "tool_calls": [...]?}`
  followed by one `{"role": "tool", "tool_call_id": ..., "content": ...}`
  per tool call. After the loop returns, the list looks the same
  way it would after a non-streaming `complete()` call — that's
  what lets the same handler power both surfaces.
- **`system_prompt` insertion.** If the caller's `messages` list
  has no `system` entry, the prompt is inserted at index 0. The
  chat route always passes a system-less list, so this is the
  insertion site.
- **Termination.** The loop returns the iteration the model emits a
  turn with no tool calls (yielding `done`). `max_iterations` (default
  8) is the runaway guard — hitting it raises `RuntimeError`, which
  the chat route catches and surfaces as an `error` event.
- **Two `done` events to track.** `client.stream` emits its own
  terminal `done` event (with `stop_reason` + `usage`) at the end of
  *each* model turn. The chat loop **does not forward that** — it
  synthesizes its own `iteration_done` (turn finished, may loop) and
  `done` (whole conversation turn complete) so the SSE consumer sees
  exactly one terminal event per user message.
- **Tool dispatch errors don't crash the stream.** If
  `tool_dispatch(name, args)` raises, the loop catches it, logs, and
  feeds `{"error": str(exc)}` to the model as the tool result. The
  model usually recovers gracefully ("I hit an error trying X, let
  me try Y").

### Concurrency control — `base_sha`, not session state

The chat loop does **not** track which docs the model has read this
turn. Edit safety is handled at the tool layer via the optional
`base_sha` argument: when the model passes the sha it last read for a
doc, the write tool (`edit_doc`, `multi_edit`, `apply_patch`,
`update_doc_nl`) checks it against current HEAD and returns
`{error: "stale_base", base_sha, current_sha, ...}` if HEAD has
drifted. `write_doc` makes `base_sha` **required** for overwrites of
existing files (full-body has no fuzzy fallback if HEAD moved).

Conversation history is replayed in full on every turn (see "How
history is built" below), so prior `read_page` / `read_doc` results
stay in the model's context across turns — the model can edit a doc
it read several turns ago without re-reading, as long as it carries
the `base_sha` forward and the file hasn't been changed by another
writer.

### `agent_activity.agent_name_var` — attribution

`run_chat_stream` sets a sibling ContextVar to `"Wiki AI
Assistant"` for the duration of the call. Every wiki read or write
the agent performs flows through `agent_activity.upsert_activity`,
which reads the var to label the activity row. That's how the
"Active agents on this page" widget knows the chat agent is
reading vs. another user.

## How history is built

This is the most subtle part of the design. There are three layers
of "history" and they don't all carry the same information.

### 1. In-loop `messages` list (full fidelity)

While the loop is running, `messages` accumulates one assistant
turn + N tool turns per iteration. By the time the loop returns, a
multi-tool turn looks like:

```python
[
    {"role": "system",    "content": "..."},
    {"role": "user",      "content": "what's in foo.md?"},
    {"role": "assistant", "content": "Let me read it.",
     "tool_calls": [{"id": "tu_1", "name": "read_page",
                     "arguments": {"path": "foo.md"}}]},
    {"role": "tool",      "tool_call_id": "tu_1",
     "content": "{...full read_page result...}"},
    {"role": "assistant", "content": "It says ..."},
]
```

That's what the **next iteration of the loop** sees, and what
`client.stream` sends to the model. Provider modules translate to
their SDK's content-block / function-call shape (see
[LLM Interfaces](LLM%20Interfaces.md) §"Per-provider notes").

### 2. DB persistence (`chat_sessions` + `chat_messages`)

Schema in `app/db/models.py`:

```
chat_sessions(id, user_id, title, created_at, updated_at)
chat_messages(id, session_id, ordering, role, content, events_json, created_at)
   role IN ('user', 'assistant')                     -- CHECK constraint
   ordering = next_per_session                       -- monotonic int
   events_json: NULL for user; full event log for assistant
```

For an assistant turn, `content` is the **rendered text shown in
the bubble** (concatenation of all `text_delta` payloads), and
`events_json` is the **full list of stream events** — text deltas,
tool calls, tool results, iteration markers — serialized verbatim.
This is what lets a returning user see the same tool-call
breakdowns they saw the first time, without us having to replay the
loop.

Tool call/result rows from the in-loop list are **not** stored as
their own `chat_messages` rows — they live entirely inside the
assistant turn's `events_json`.

### 3. Hydration into a fresh loop (rendered prefix only)

When the user sends turn N+1, `send_message` reads the session and
hydrates:

```python
messages = [
    {"role": m["role"], "content": m["content"]}
    for m in history
]
```

**Just `role` + `content`.** No replay of `tool_calls` or `tool` rows.
The rendered assistant text is the only context the model sees from
prior turns. There are two reasons this is fine:

- The assistant text already reflects what the agent *learned* from
  the tools (rephrased into prose). Replaying tool calls would
  bloat the prompt with raw JSON for marginal benefit.
- Provider-specific tool-call replay is fragile (Anthropic wants
  content-block tool_use; Gemini wants function-call name-mapping;
  Ollama synthesizes ids…). Skipping it altogether sidesteps the
  whole translation problem.

So a "thread" the agent reasons about is a flat conversational
trace, plus the *current* turn's full tool-using context built from
scratch each send. Old tool calls are visible in the UI (via
`events_json`) but invisible to the model on subsequent turns.

## Session lifecycle

| Step | Endpoint | What |
|---|---|---|
| Create | `POST /api/chat/sessions` | Empty row with `title=NULL`. |
| Send | `POST /api/chat/messages` | Persist user turn → run loop → stream → persist assistant turn → `touch` `updated_at` → enqueue title task **only if this was the first user turn**. |
| Read | `GET /api/chat/sessions/<id>` | Returns session + ordered `chat_messages` with `events` parsed back from JSON. |
| List | `GET /api/chat/sessions` | Caller's sessions, newest-updated first. |
| Delete | `DELETE /api/chat/sessions/<id>` | FK CASCADE drops messages. |

Every `get` / `delete` filters by `user_id` so the API can't
accidentally return another user's conversation.

The user message is **persisted before the loop starts**. That's
deliberate: if the LLM call dies halfway, the user's message is
still on the timeline (and the bubble re-renders cleanly when the
session is re-loaded).

### Title generation

`generate_chat_title` runs on `documents_queue` (it's a short LLM
call — reusing the LLM-bound worker is cheaper than spinning up a
separate queue). Source: `app/tasks/chat_title.py`. Only enqueued
when `prior_count == 0` *before* the user turn was persisted, so
exactly the first complete round-trip triggers it. Failures are
non-fatal — the frontend falls back to displaying the first user
message as the title.

## The tool registry

`app/llm/agents/tools/__init__.py` walks the directory at import
time, loading every `<name>.json` (the spec) plus its `<name>.py`
sibling (the handler). Loud-fail at startup if anything is missing
or mismatched. The chat loop takes:

- `TOOL_SPECS` — the list passed to `client.stream` (becomes the
  provider's tool list).
- `dispatch(name, args)` — the callable the loop invokes on each
  tool_call.

Adding a tool is dropping a new pair. The JSON's `name` field MUST
equal the filename stem; the Python module MUST expose `handle(args:
dict) -> Any`.

### What the chat agent gets

The **standard wiki tool set**, as wired into `run_chat_stream`:

| Tool | Family | What |
|---|---|---|
| `search_wiki` | discovery | BM25 + ACL filter; ~64-token snippets. |
| `read_page` | read | Full markdown body + active-agents list. The only thing that registers a path as "seen". |
| `web_search` | external | Serper-backed snippets. |
| `open_urls` | external | Firecrawl-backed full pages, batch (one call, many URLs). |
| `get_trigger_destinations` | trigger | List of available destinations. |
| `create_trigger` | trigger | Register an NL trigger. |
| `update_trigger` | trigger | Edit an existing trigger. |
| `edit_doc` | write | Surgical find-and-replace (default for changes). |
| `multi_edit` | write | Atomic multi-replace on one file. |
| `write_doc` | write | Full-body overwrite or new file. |
| `create_directory` | write | New empty folder. |
| `move_path` | write | Rename or relocate. |
| `explain_functionality` | meta | Returns canonical "what is this app" reference. |
| `run_bash` | discovery | Read-only `cat / find / grep / ls / head / tail / wc` over the wiki tree. |

Two extra tools live in the registry but **aren't part of the chat
set** — they're MCP-only entry points: `apply_patch`,
`update_doc_nl`, `ask_nl_question`. They get registered because the
registry is shared, but `chat.system.md` doesn't mention them so the
chat agent has no incentive to call them. (The MCP server uses its
own subset — see `app/mcp_server/tools.py`.)

### Tool-handler conventions

Read `_doc_helpers.py` once and the rest of the write tools are
copy-paste:

- **`validate_doc_path`** — normalize, reject traversal, reject
  non-`.md`. Raises `ToolError`.
- **`assert_base_sha(rel, base_sha)`** — optimistic concurrency.
  Returns the `stale_base` error dict when `base_sha` no longer
  matches HEAD; no-op when `base_sha is None`. Both the chat agent
  and MCP clients go through the same check.
- **`require_can(action, path)`** — every read/write tool gates
  through ACL. `ToolError(str(exc))` becomes `{"error": ...}` to
  the model, so the model can apologize and pivot rather than
  crashing the stream. See
  [Auth and Permissions](Auth%20and%20Permissions.md).
- **`commit_and_fan_out(rel, body, msg, change_kind=)`** — the
  one-stop side-effect helper. Permission gate, agent-activity
  upsert + scheduled cleanup, `git.commit_file`, then
  `wiki/notify.after_doc_write` (which fans out reindex + trigger
  eval).
- **Result shape** — every write tool returns
  `{path, sha, diff, broken_links}` on success or `{error: msg}` on
  failure (or the structured `{error: "stale_base", base_sha,
  current_sha, message}` on concurrency miss). Keep that shape so
  the model's mental model is uniform.

### `run_bash` — the safety story

`_bash.py` parses a piped chain into segments, runs each with
`shell=False` against the wiki working tree, and:

- **First-token allowlist:** `cat / find / grep / ls / head / tail
  / wc`. Anything else (`rm`, `mv`, `git`, `bash`, `sh`, `python`,
  `>`, `<`) is rejected before execution.
- **Pipes / `&&` / `||` / `;` honored** by inspecting return codes
  between segments. No persistent shell, no env-var carry-over.
- **Truncation caps** — 2000 lines / 50 KB generic; tighter 100-line
  cap if the last segment is `grep` / `find`. Per-segment timeout 30s.

Writes go through the proper write tools (which commit via
`app/wiki/git.py`), so `run_bash` stays read-only by construction.

## System prompt

`app/llm/prompts/chat.system.md` is the entire policy document the
agent reads. The shape:

1. Identity + scope ("you are the chat agent inside agent-wiki…").
2. Wiki overview (markdown directory + three update channels +
   triggers).
3. Tool reference — one bullet per tool with usage hints. **This is
   the canonical place to teach the agent which tool to reach for
   when** — not the `description` field on the JSON spec, which
   is short and provider-truncated.
4. Wiki scope rule (markdown only — `read_page`, `write_doc`,
   `edit_doc`, `multi_edit` reject non-`.md`).
5. **Approval rule** — write tools (`edit_doc`, `multi_edit`,
   `write_doc`, `create_directory`, `move_path`, `create_trigger`,
   `update_trigger`) only fire on explicit user intent. No
   proactive writes.

If you change this prompt, **don't squash history** — old versions
are eval baselines.

## Frontend

`frontend/src/components/chat/ChatWidget.tsx` is the main piece.
Three modes (`closed`, `widget`, `expanded`) persisted in
`localStorage`. The active session id is also persisted, so a
reload returns to the same conversation.

Sending a message:

```ts
streamMessage(sessionId, content, onEvent, abortSignal)
   ↓ POST /api/chat/messages (SSE)
   ↓ apiStream parses frames
   ↓ onEvent({type, ...}) — switch by type
        ─ text_delta → append to current assistant bubble
        ─ tool_call  → show "Using <name>…" hint
        ─ tool_result → clear hint
        ─ done       → finalize bubble, refresh history list
        ─ error      → show error, finalize bubble
```

The widget renders messages incrementally as `text_delta` events
arrive — that's the user-visible payoff for SSE over a one-shot
POST. Tool calls show as a brief "Using X…" hint while in flight
(driven by `tool_call` / `tool_result` pairs).

The history panel (`ChatHistoryPanel.tsx`) lists prior sessions for
the current user, ordered by `updated_at`. Clicking switches the
active session and re-hydrates the messages from
`GET /api/chat/sessions/<id>` — both the rendered text and the
events JSON, so old tool-call detail re-renders too.

## Error paths

The server-side `try/except` in `send_message` is the single
funnel:

- `LLMError` — caught, surfaced as
  `{type: "error", code, message}`. Stream closes cleanly. The
  user message is already persisted; the assistant turn is **not**
  (the partial output is dropped — it would just confuse a re-load).
- Unknown `Exception` — logged with `log.exception`, surfaced as
  `code="unknown"` with a generic message ("check the server logs").
- Persisting the assistant turn after a clean close — wrapped in
  its own `try/except`. A persistence failure logs but doesn't
  retry; the user sees the bubble in real time, just won't see it
  on re-load. Acceptable trade for keeping the stream non-blocking.

Tool errors (handler raises, returns `{"error": ...}`, or
ToolError'd) become tool results, not error events. The loop
continues; the model usually pivots.

## Where to extend

- **Add a tool.** Drop `<name>.json` + `<name>.py` in
  `app/llm/agents/tools/`, mention it in `chat.system.md` so the
  model knows when to use it. Restart the backend (the registry
  loads at import time).
- **Adjust an existing tool's behavior.** Edit the handler — the
  shape is small. If the change affects the description, edit the
  `chat.system.md` bullet too.
- **Add a non-chat agent.** Reuse `run_chat_loop` (the
  non-streaming wrapper) with your own `system_prompt`, narrowed
  `tools`, and `tool_dispatch`. `wiki_qa.run` is the canonical
  example.
- **Surface a new event type to the UI.** Add to the chat-loop
  yield contract, the SSE protocol table at the top of
  `app/api/chat.py`, and the `StreamEvent` union in
  `frontend/src/components/chat/ChatWidget.tsx`. Persist it in
  `events_json` automatically (the route stores everything verbatim).

## Pointers

- API: `backend/app/api/chat.py`
- Agent loop: `backend/app/llm/agents/chat.py`
- Tools: `backend/app/llm/agents/tools/`
- Tool helpers: `backend/app/llm/agents/tools/_doc_helpers.py`
- System prompt: `backend/app/llm/prompts/chat.system.md`
- Session repo: `backend/app/chat/sessions.py`
- ORM: `ChatSession`, `ChatMessage` in `backend/app/db/models.py`
- Frontend: `frontend/src/components/chat/ChatWidget.tsx`,
  `frontend/src/lib/chat.ts`
- Title task: `backend/app/tasks/chat_title.py`
- Activity attribution: `backend/app/wiki/agent_activity.py`
