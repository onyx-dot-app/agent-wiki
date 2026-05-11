# LLM Interfaces

How LLM calls flow through the codebase: the public client, the
provider seam, the agent layer built on top, and where the rules
live. The whole point of this design is that a slow Anthropic call
on Tuesday and an Ollama swap on Wednesday don't ripple into the
twenty places the system actually invokes a model.

For *why* the seams are shaped this way, see `CLAUDE.md`
("LLM calls — always through `app/llm/client.py`"). For an
end-to-end picture of where LLM work runs, see
[Background Tasks](Background%20Tasks.md) §"documents queue".

## The two surfaces

```
┌────────────────────────────────────────────────────────────┐
│ app/llm/client.py — the only public LLM entry point        │
│                                                            │
│   stream(messages, model?, tools?, max_tokens?) → Iter[ev] │
│   complete(messages, …)                          → Result  │
│                                                            │
└─────────────────────────┬──────────────────────────────────┘
                          │
                          │  reads
                          ▼
                ┌────────────────────────┐
                │ app/llm/settings.py    │
                │  DB-backed config      │
                │  (admin page writes)   │
                └────────────┬───────────┘
                             │
                             │  picks provider
                             ▼
        ┌─────────────────────────────────────────────┐
        │ app/llm/providers/                          │
        │   ─ anthropic.py     PROVIDER instance      │
        │   ─ openai.py        PROVIDER instance      │
        │   ─ gemini.py        PROVIDER instance      │
        │   ─ ollama.py        PROVIDER instance      │
        │   ─ _common.py       translation helpers    │
        └─────────────────────────────────────────────┘
```

### `stream(...)`

```python
from app.llm import client

for ev in client.stream(messages, tools=tools, model=None, max_tokens=4096):
    if ev["type"] == "text_delta":
        ...
    elif ev["type"] == "tool_call":
        ...
    elif ev["type"] == "done":
        # exactly once, last
        ...
```

Yields normalized **stream events** as they arrive. Use this when
streaming-to-the-user matters (chat). One terminal `done` event
carries `stop_reason` + `usage`.

### `complete(...)`

A drainer over `stream` that returns a `CompletionResult`:

```python
class CompletionResult(BaseModel):
    text: str
    tool_calls: list[ToolCall]   # {id, name, arguments}
    stop_reason: str
    usage: Usage                 # {input_tokens, output_tokens, reasoning_tokens}
```

Use this for one-shot callers (trigger evaluator, doc-updater,
chat-title generator) that only need the final result.

The DEBUG payload dump (full request + response, untruncated) lives
in `stream` only — `complete` doesn't re-log to avoid double-print.
Set `LOG_LEVEL=DEBUG` to see it; serialization is gated behind
`log.isEnabledFor(DEBUG)` so the cost is zero at INFO.

## Normalized shapes

Everything below the `client.py` line speaks one shape. Provider
modules translate.

### Messages

```python
messages = [
    {"role": "system",    "content": "..."},
    {"role": "user",      "content": "..."},
    {"role": "assistant", "content": "...", "tool_calls": [
        {"id": "tool_abc", "name": "read_doc", "arguments": {"path": "x.md"}}
    ]},
    {"role": "tool", "tool_call_id": "tool_abc", "content": "...result..."},
    ...
]
```

- `system` messages are pulled out by `split_system` (in
  `providers/_common.py`) and joined with a blank line; providers
  pass them as a separate `system` / `instructions` /
  `system_instruction` argument.
- Multiple system messages are intentional and supported (base
  persona + per-tool reminder, etc.).
- `tool` messages echo the `tool_call_id` from the assistant turn
  that called them. Gemini needs the tool *name* on the result;
  `tool_call_id_to_name` walks earlier turns to recover it.
- `content` may be a string or a structured payload —
  `stringify_tool_result` JSON-encodes anything non-string before
  handing it to a provider.

### Tools

```python
tools = [
    {"name": "search_wiki",
     "description": "...",
     "input_schema": {... JSON Schema ...}},
    ...
]
```

- `input_schema` is JSON Schema; providers may strip keywords they
  don't support but must not change semantics.
- The chat loop loads these from `app/llm/agents/tools/` — each tool
  is a `<name>.json` (the spec) + a `<name>.py` (the handler).

### Stream events

```python
{"type": "text_delta",  "text": "..."}
{"type": "tool_call",   "id": "...", "name": "...", "arguments": {...}}
{"type": "done",        "stop_reason": "...",
                         "usage": {"input_tokens": int,
                                   "output_tokens": int,
                                   "reasoning_tokens": int}}
```

`tool_call` is yielded *after* the JSON args are fully assembled.
Anthropic streams tool args as `input_json_delta` chunks; the
provider buffers them per content-block and emits the parsed dict on
`content_block_stop`. Ollama hands back a dict directly. Either way,
callers see `arguments: dict[str, Any]`. Malformed JSON that some
truncated models emit lands under `{"_raw": "<the string>"}`
(`safe_json_loads` in `_common.py`) so the chat loop can still
report it instead of crashing.

`reasoning_tokens` is non-zero only on providers that report
extended-thinking usage (OpenAI's o-series, Gemini's `thoughts_token_count`).

## The provider seam

`app/llm/providers/__init__.py` defines the contract:

```python
class Provider(Protocol):
    name: str
    def check_configured(self, settings: LLMSettings) -> None: ...
    def stream(
        self,
        messages: list[dict[str, Any]],
        *, model: str,
        tools: list[dict[str, Any]] | None,
        max_tokens: int,
        settings: LLMSettings,
    ) -> Iterator[StreamEvent]: ...
```

Each provider module is a stateless singleton exposed as a
module-level `PROVIDER` instance, registered eagerly at import via
the registry's `register(...)`. `client.py` looks up the
configured provider by name and dispatches — there's no `if/elif`
chain. Adding a provider is dropping a new file with a `PROVIDER`,
adding the import to `providers/__init__.py`, and that's it.

A few rules baked into the protocol:

- **Stateless past the cached SDK client.** Providers receive
  `settings` on every call so the admin UI can flip provider/model
  *live* without a process restart. The `_client(api_key)` helpers
  in each provider use `lru_cache(maxsize=4)` to avoid re-creating
  SDK clients on every call.
- **Translate, don't re-shape.** Providers translate the normalized
  message/tool/stream shapes to/from the SDK and back. They don't
  reinterpret semantics. Keep input_schema as JSON Schema — drop
  unsupported keywords if necessary, but don't rewrite meanings.
- **Map SDK exceptions to `LLMError` (in the provider).**
  `_translate_error(exc)` is the local convention — see
  `providers/anthropic.py:_translate_error` for the canonical
  mapping (auth / rate_limit / network / config / bad_request / …).
  Importing the SDK's exception types is local to that function so
  the rest of the provider doesn't depend on them.
- **Don't import `app.llm.client` from a provider.** That's the
  cycle the `errors.py` module exists to break — providers import
  `LLMError` directly from `app.llm.errors`, never via the client.

### Per-provider notes

| Provider | File | Tool-call shape | Notes |
|---|---|---|---|
| Anthropic | `providers/anthropic.py` | streamed JSON deltas, buffered per `content_block` | System prompt is sent with `cache_control: ephemeral` so multi-turn loops don't re-pay for it. SDK exceptions translated by `_translate_error`. |
| OpenAI | `providers/openai.py` | Responses API; surfaces `reasoning_tokens` from `output_tokens_details.reasoning_tokens` | |
| Gemini | `providers/gemini.py` | function-call shape; needs the tool *name* on result turns — recovered via `tool_call_id_to_name` | `reasoning_tokens` from `thoughts_token_count`. |
| Ollama | `providers/ollama.py` | dict args directly; **no native tool-call ids** — provider synthesizes per-response ids so `tool_call_id` round-trips | No API key. `settings.ollama_base_url` empty → SDK default (localhost). |

## Settings — DB-backed, no env-var fallback

`app/llm/settings.py:get()` reads a single row keyed at `id=1` from
the `llm_settings` table:

```python
class LLMSettings(BaseModel):  # frozen
    provider: str          # "anthropic" | "openai" | "gemini" | "ollama"
    model: str             # e.g. "claude-sonnet-4-6"
    anthropic_api_key: str
    openai_api_key: str
    gemini_api_key: str
    ollama_base_url: str
```

Empty strings everywhere if the row is missing. **Don't read
provider keys from `os.environ` or `CONFIG`.** The admin page (PUT
`/api/admin/llm`, source: `app/api/admin.py`) is the only writer;
its handler implements the "empty string = leave existing secret
untouched, explicit null = clear" convention so the form doesn't
have to echo back stored secrets.

Frozen + per-call lookup means `client.stream` calls `settings.get()`
on entry and passes the snapshot down — there's no in-memory cache to
invalidate when the admin clicks save.

`GET /api/llm/status` (`app/api/llm.py`) is the public-readable
"is-it-configured" check. Any logged-in user can see
`{configured: bool, provider: str}` so the frontend can show a
setup banner without leaking keys.

## Errors

`app/llm/errors.py` defines:

```python
class LLMError(Exception):
    code: str       # short, stable, API-mappable
    message: str    # safe to show the user
```

Standard codes the API layer maps:

| Code | HTTP |
|---|---:|
| `not_configured` | 503 |
| `auth` | 502 |
| `rate_limit` | 429 |
| `network` | 502 |
| `config` | 502 |
| `bad_request` | 400 |
| `provider` | 502 |
| `unknown` | 500 |

Provider modules raise `LLMError` from inside `_translate_error`.
Don't include API keys, secrets, or full SDK stacktraces in the
message — it's user-facing.

## Agents — what's built on top

```
app/llm/agents/
├── chat.py             generic streaming chat loop + run_chat_stream wrapper
├── document_updater.py one-shot doc-reconciliation
├── wiki_qa.py          one-shot read-only NL Q&A
└── tools/              tool registry: <name>.json + <name>.py pairs
```

### `chat.run_chat_loop_stream` — the multi-turn primitive

The streaming, multi-iteration tool-use loop. Generic — pass any
`system_prompt`, any `tools` list, and a `tool_dispatch(name, args)`
callable. Yields a superset of `client.stream`'s events:

```
{"type": "text_delta",  "text": str}
{"type": "tool_call",   "id": str, "name": str, "arguments": dict}
{"type": "tool_result", "id": str, "name": str, "content": str}    # synthesized
{"type": "iteration_done"}                                          # one model turn finished
{"type": "done"}                                                    # final assistant turn, no tools
```

Behavior contract:

- `messages` is mutated in place — each model turn appends one
  `assistant` row; each tool call appends one `tool` row.
- If `messages` has no `system` entry, `system_prompt` is inserted
  at index 0.
- The loop stops when the model returns a turn with no tool calls.
  `max_iterations` (default 8) is the runaway guard.
- Edit safety is handled at the tool layer via the optional
  `base_sha` arg, not via session state. See
  [Chat Harness](Chat%20Harness.md) §"Concurrency control".

`run_chat_stream` and `run_chat` wrap the loop with the standard
wiki tool set and `chat.system` prompt. They also set
`agent_activity.agent_name_var = "Wiki AI Assistant"` for the
duration of the call so any wiki read/write the agent does is
attributed correctly in the agent-activity panel.

### `document_updater.run`

Single-shot. The system prompt
(`prompts/document_updater.system.md`) constrains the output to
either the literal token `NO_CHANGE` or the full new doc body in
markdown — no preamble, no fenced code block. The function returns
`str | None` and *does no I/O beyond the LLM call* — the caller
(a worker task or an MCP tool) commits the body. Defensive
fence-stripping handles a model that ignores the no-fence rule.

### `wiki_qa.run` — one-shot Q&A

Reuses `run_chat_loop` with a narrowed toolset (`search_wiki` +
`read_page` only — no writes). Returns `{answer, sources}` where
`sources` is built from the loop's mutated message log (every
`read_page` call), not parsed from the model's prose. Backs the
`ask_nl_question` tool that the inbound MCP server exposes.

### Lazy imports for cycle-breaking

`wiki_qa.py` imports `chat` and the tool registry **inside** `run()`
because `chat.py` imports the tool registry at module load, which
loads `ask_nl_question.py`, which imports `wiki_qa`. Lazy import =
acyclic graph at runtime. Same trick in any tool that calls back into
the LLM.

## Tool registry

`app/llm/agents/tools/__init__.py` walks the directory at import
time, loading every `<name>.json` and `<name>.py` pair:

- The JSON's `name` field MUST equal the filename stem.
- The Python module MUST expose `handle(args: dict) -> Any`.
- Anything that fails (missing handler, mismatched name) blows up
  *at startup*, not on first tool call. Loud-fail by design.

`TOOL_SPECS` is the full list passed to `client.stream`;
`dispatch(name, args)` is the callable handed to the chat loop.
Adding a tool: drop a new pair, restart, done.

The current tools (with the doc that owns each surface) are
inventoried in [Code Layout](../Code%20Layout.md) §"app/llm/agents/tools/".

## Prompts

```
app/llm/prompts/
├── chat.system.md
├── document_updater.system.md
├── document_updater.user.md
├── wiki_qa.system.md
└── app_help.md
```

`load_prompt("chat.system")` reads `chat.system.md` from this
directory. That's the entire loader — no Jinja, no caching layer.
Format substitution (`document_updater.user.md` uses `.format(...)`)
happens at the call site. Prompts as plain `.md` siblings keeps git
blame meaningful and lets us hand a prompt file to a reviewer the
same way we'd hand them code.

If you change `document_updater.system.md`, **don't squash the
history** — old versions are the eval baseline.

## Outside callers — who uses what

Outside `app/llm/`, only three places hit the LLM:

| Caller | Surface |
|---|---|
| `app/tasks/chat_title.py` | `client.complete` (one short call) |
| `app/triggers/natural_language.py` | `client.complete` (NL match + render) |
| `app/api/chat.py` | `agents.chat.run_chat_stream` (the loop) |

Plus the in-process MCP server (`app/mcp_server/`) and the chat
route, both of which go through the agents layer rather than
`client.py` directly. Trigger fan-out (post-commit) calls
`triggers/natural_language.py` from a worker task — see
[Background Tasks](Background%20Tasks.md).

In tests: patch `app.llm.client.complete` / `client.stream` to
return canned events. Don't patch the SDK directly — tests should
ride the same seam the rest of the app does.

## API surface

| Endpoint | Auth | What |
|---|---|---|
| `GET /api/llm/status` | logged-in | `{configured: bool, provider: str}` — banner-driver |
| `GET /api/admin/llm` | admin | full settings (with redacted key hints) |
| `PUT /api/admin/llm` | admin | upsert settings; "" = leave secret, null = clear |

`_ALLOWED_PROVIDERS` (in `app/api/admin.py`) is the validation list
— must match the registered providers in `providers/__init__.py`.

## What not to do

The seam exists because every one of these has bitten us. Repeated
from `CLAUDE.md` for the people reading the wiki:

- **Don't `import anthropic` / `import openai` / `from google import
  genai` / `import ollama` outside `app/llm/providers/<name>.py`.**
  The whole point of the registry is that the rest of the codebase
  doesn't know which provider is configured.
- **Don't read `os.environ["ANTHROPIC_API_KEY"]` (or any provider
  key) from app code.** Go through `app.llm.settings.get()`. The
  admin UI's overrides won't reach you otherwise.
- **Don't add an `if provider == "..."` branch in `client.py`.** Add
  a provider module with a `PROVIDER` instance and let the registry
  dispatch.
- **Don't log raw provider exceptions to the user.** Translate to
  `LLMError(code, message)` first — secrets and stack traces leak
  out of unwrapped SDK errors.
- **Don't bypass the agent loop** by writing your own multi-turn
  tool-use orchestration. `run_chat_loop_stream` is generic on
  `system_prompt` / `tools` / `tool_dispatch` — pass yours in.

## Adding a provider — checklist

1. Create `app/llm/providers/<name>.py`.
2. Implement `class <Name>Provider`:
   - `name = "<name>"` (matches `llm_settings.provider`).
   - `check_configured(settings)` — raise
     `LLMError("not_configured", ...)` if creds are missing.
   - `stream(messages, *, model, tools, max_tokens, settings)` —
     translate, stream SDK events, yield normalized events, end
     with exactly one `done`.
   - `_translate_error(exc)` — map SDK exception types to the
     standard error codes.
3. `PROVIDER = <Name>Provider()`; `register(PROVIDER)` at module
   bottom.
4. Add `from app.llm.providers import <name> as _<name>` to
   `providers/__init__.py` (alphabetical).
5. Add `"<name>"` to `_ALLOWED_PROVIDERS` in `app/api/admin.py`.
6. Add a `<name>_api_key` (or equivalent) column to the
   `llm_settings` table — schema lives in `app/db/models.py`,
   generate a migration with
   `cd backend && alembic revision --autogenerate -m "add <name> key"`,
   review, commit.
7. Surface the new field on the admin page
   (`frontend/src/app/admin/llm/page.tsx`).
8. Tests patch `app.llm.client.stream` / `client.complete` — your
   provider doesn't need integration tests against a live SDK to
   ship.

## Pointers

- Code: `backend/app/llm/`
- Admin UI: `frontend/src/app/admin/llm/page.tsx`
- Public status: `frontend/src/lib/llm.ts`
- Background calls: [Background Tasks](Background%20Tasks.md)
- Inbound MCP that exposes the wiki agents:
  [MCP Server Inbound](MCP%20Server%20Inbound.md)
- Architectural seams: `CLAUDE.md` ("LLM calls — always through
  `app/llm/client.py`")
