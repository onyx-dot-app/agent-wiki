# Chat history (persisted sessions)

> **Part of agent-wiki v0.** The in-app chat widget
> (`frontend/src/components/chat/ChatWidget.tsx`) now persists every
> conversation server-side so users can switch sessions, reopen the
> widget after a reload, and carry conversations across devices.

_Last updated: 2026-05-09_

---

## Storage shape

Two Postgres tables (migration `0005_chat_sessions`):

- `chat_sessions(id, user_id, title, created_at, updated_at)` — one row
  per conversation. `title` is NULL until the title-generation task
  fills it in (after the first assistant turn). `updated_at` is bumped
  whenever a new turn is appended, so the listing query orders by it
  desc.
- `chat_messages(id, session_id, ordering, role, content, events_json,
  created_at)` — one row per turn (`role` is `'user'` or `'assistant'`).
  `content` is the rendered text (what the bubble shows).
  `events_json` carries the full streamed event log for assistant
  turns — text deltas, tool calls, tool results, iteration markers —
  serialized as JSON, NULL on user rows. The event log is the basis
  for re-rendering tool-use detail when a session is reopened.

Both tables are Postgres-only (not committed to the wiki repo).
`chat_messages.session_id` cascades on delete, so dropping a session
removes its messages atomically.

## Repo

`backend/app/chat/sessions.py` — free-function repo modeled on
`app/auth/users.py`. Ownership checks (`user_id` filter) live in the
repo so the API can't accidentally return another user's data.

Key functions:

- `create(user_id)` / `get(session_id, user_id)` /
  `list_for_user(user_id)` / `delete(session_id, user_id)` — owner-scoped.
- `append_message(session_id, role=, content=, events=)` — allocates the
  next ordering value for the session.
- `get_messages(session_id)` — ordered list, parses `events_json`.
- `update_title(session_id, title)` — used by the title task.
- `touch(session_id)` — bump `updated_at` so the session sorts to top.

## HTTP API

Routes on the `chat` blueprint (see `backend/app/api/chat.py`):

| Method | Path | Body | Returns |
|---|---|---|---|
| `GET`    | `/api/chat/sessions`        | —   | List of `{id, title, created_at, updated_at}`, newest first |
| `POST`   | `/api/chat/sessions`        | —   | Newly created session (title null) |
| `GET`    | `/api/chat/sessions/<id>`   | —   | `{session, messages: [{id, role, content, events, created_at}]}` |
| `DELETE` | `/api/chat/sessions/<id>`   | —   | 204 |
| `POST`   | `/api/chat/messages`        | `{session_id, content}` | SSE stream (see protocol below) |

`POST /api/chat/messages` flow:

1. Look up the session (404 if missing or not the caller's).
2. Persist the user message before streaming starts. If the LLM call
   fails halfway, the user's message remains on the timeline so the
   user can see what they sent and retry.
3. Hydrate prior history from the DB (now including the just-saved
   user turn) into the `[{role, content}, …]` shape the agent loop
   expects. We don't replay tool-use blocks from the event log — the
   rendered text trace is sufficient context for continuation.
4. Iterate `run_chat_stream(messages)`, forwarding events as SSE while
   accumulating them into a local `events` list and a `final_text`
   accumulator (concat of `text_delta.text`).
5. On clean stream end: `append_message(role='assistant',
   content=final_text, events=events)` and `touch(session_id)`.
6. If this was the session's first turn, enqueue
   `generate_chat_title` (see below) so the panel picks up a title
   on the next refresh.

## Title generation

Background task on `documents_queue` (the LLM-bound queue — we reuse
its existing worker rather than spinning up a new one for one task).

`backend/app/tasks/chat_title.py:generate_chat_title(session_id)`:

- Loads the first user/assistant pair, calls `llm.client.complete`
  with a "summarize in 3-6 words, no quotes" prompt, calls
  `sessions.update_title`.
- Failures are non-fatal — we log and leave the title NULL; the
  frontend falls back to `"Untitled chat"`.

## Frontend

- `frontend/src/lib/chat.ts` — typed wrappers over `apiFetch`/`apiStream`
  (`listSessions`, `createSession`, `getSession`, `deleteSession`,
  `streamMessage`).
- `frontend/src/components/chat/ChatHistoryPanel.tsx` — slide-over
  panel inside the widget, absolute-positioned over the message
  area. Same panel works in widget mode (380×560 floating) and
  expanded mode (resizable right rail) — no separate variant.
  Renders the session list with title + relative timestamp, an
  on-hover delete button, and a "+ New chat" button in the header.
- `frontend/src/components/chat/ChatWidget.tsx` — owns
  `sessionId` (persisted to `chat-widget:session-id` localStorage)
  alongside the existing mode/width keys. On widget open the active
  session's messages are hydrated from the backend; on first send the
  session is lazily created (so opening + closing the widget doesn't
  pile up empty rows). When a stream finishes cleanly, a
  `historyRefreshKey` bump triggers a refetch in the panel — that's
  how a freshly-generated title shows up without a page reload.

## Open follow-ups

- **Tool-use replay** — assistant `events` are stored but the widget
  doesn't yet re-render tool calls / results when a session is
  reopened (it just shows the final text). Adding a richer renderer
  is a UI-only change; the data is already there.
- **Stream cancellation** — the current widget doesn't expose an abort
  button. If we add one, hook it through the existing `apiStream`
  `signal` parameter and decide whether to persist the partial
  assistant text or drop it.
- **Title regeneration** — title is generated once after the first
  turn. A long-running session could outgrow its title; we'd need a
  trigger (manual button? after N turns?) to regenerate.
