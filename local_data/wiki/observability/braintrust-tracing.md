# Braintrust LLM tracing

_Last updated: 2026-05-10_

Every LLM exchange — inbound messages, available tools, tool calls,
tool results, model output, stop reason, token usage — is recorded as
a Braintrust span when tracing is enabled. The same single seam
(`app/tracing/`) wraps the chat agent loop, the document-updater, the
QA agent, the title-generation task, and all four trigger-evaluation
phases. Anything new that calls `app.llm.client.stream` /
`complete` picks up the LLM-level span automatically; new flows just
need a `with trace_flow(...)` block at their entry point to root the
trace.

## How to enable it

1. Sign in as an admin and open **Admin → Braintrust tracing**
   (`/admin/braintrust`).
2. Enter the Braintrust **project** name (e.g. `agent-wiki`) and paste
   the **API key**. Click **Save**.
3. The **Enable** button activates once both fields are saved. Toggle
   it on; tracing flips on within the next call (the logger is cached
   keyed by `(project, api_key)`, so credential rotations also
   propagate immediately).
4. Drive the app — chat, edit a doc, fire a trigger. Traces appear in
   the Braintrust UI under your project name.

API keys are write-only: stored in the `braintrust_settings` Postgres
row, masked to a `sk-…last4` hint on read, and never echoed back in
full. The same convention as `/admin/llm`.

## The seam (`app/tracing/`)

Three context managers, all no-op when tracing is disabled, when
config is missing, or when SDK init fails. Instrumentation must
**never** break the call path.

```python
from app.tracing import trace_flow, start_llm_span, start_tool_span
```

- `trace_flow(name, **metadata)` — root span for a user-facing flow.
  Wraps a chat turn, an agent run, a background task. Inner LLM /
  tool spans nest under it via Braintrust's contextvar parenting.
- `start_llm_span(...)` — already wired in `app/llm/client.py:stream`.
  You don't call this yourself; every `stream` / `complete` call
  emits one automatically with messages, tools, model, max_tokens,
  output, stop_reason, usage.
- `start_tool_span(name, arguments)` — already wired in the chat
  agent loop (`app/llm/agents/chat.py:_drive_loop`). Every tool
  dispatch produces a span with input args + result.

## Tracing a new flow

If you add a new top-level operation that calls the LLM (a new agent,
a new background task), wrap its entry point:

```python
from app.tracing import trace_flow

def run_my_agent(query: str, *, user_id: str):
    with trace_flow("agent.my_agent", user_id=user_id, query_len=len(query)):
        # everything inside — LLM calls, tool dispatch — auto-nests
        ...
```

That's the whole API. Don't reach for `start_llm_span` or
`start_tool_span` directly unless you're instrumenting a brand-new
seam (e.g. a new `app/llm/client.py`-style chokepoint, or a new tool
registry that doesn't go through the chat loop).

## Where it's already wired

| Flow                                 | File                                          | Span name                            |
| ------------------------------------ | --------------------------------------------- | ------------------------------------ |
| Chat send (`POST /api/chat/messages`)| `app/api/chat.py`                             | `chat.send_message`                  |
| Doc-updater agent                    | `app/llm/agents/document_updater.py`          | `agent.document_updater`             |
| Wiki Q&A agent                       | `app/llm/agents/wiki_qa.py`                   | `agent.wiki_qa`                      |
| Chat title task                      | `app/tasks/chat_title.py`                     | `task.chat_title`                    |
| Trigger NL match (delta)             | `app/triggers/natural_language.py:matches`    | `trigger.matches`                    |
| Trigger render (delta)               | `app/triggers/natural_language.py:render_message` | `trigger.render_message`         |
| Trigger NL match (snapshot)          | `app/triggers/natural_language.py:matches_snapshot` | `trigger.matches_snapshot`     |
| Trigger render (snapshot)            | `app/triggers/natural_language.py:render_snapshot_message` | `trigger.render_snapshot_message` |
| New-file-in-dir trigger              | `app/triggers/natural_language.py:evaluate_new_file_in_dir` | `trigger.evaluate_new_file_in_dir` |
| Every LLM call                       | `app/llm/client.py:stream`                    | `llm:<provider>` (auto-nested)       |
| Every tool dispatch                  | `app/llm/agents/chat.py:_drive_loop`          | `tool:<tool_name>` (auto-nested)     |

## Storage and config

- Schema: `braintrust_settings` (singleton row, `id = 1`). Columns:
  `project`, `api_key`, `enabled`, `updated_at`. Migration:
  `app/db/migrations/versions/0010_braintrust_settings.py`.
- Settings module: `app/tracing/settings.py` (`get`, `upsert`).
- HTTP: `GET /api/admin/braintrust`, `PUT /api/admin/braintrust`
  (`app/api/admin.py`). Same secret-resolution convention as the LLM
  endpoint — empty string = "leave existing", explicit `null` =
  "clear".

No env-var fallback. The DB row is the single source of truth, same
as the LLM provider settings.

## What we explicitly didn't do

- **No SDK wrapping** (`braintrust.wrap_anthropic`, etc.). Our
  normalized event stream in `app/llm/client.py:stream` is already a
  uniform shape across providers — wrapping at the SDK level would
  duplicate the data and bypass our tool-call assembly.
- **No sampling.** Off when disabled, full-fidelity when on. Add
  later if volume warrants.
- **No custom span framework.** The Braintrust SDK is used directly;
  parent/child relationships ride on the SDK's contextvars.
