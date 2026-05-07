# Frontend (ChatUI + Wiki UI + Events + Admin)

> **Part of agent-workspace v0.** See the master doc
> [`../architecture_and_progress.md`](../architecture_and_progress.md) for
> the cross-area map. Backend-side chat lives in
> [agents/chat-agent.md](../agents/chat-agent.md); trigger semantics in
> [natural-language-triggers](../natural-language-triggers/natural-language-triggers.md).
> This doc owns everything in `frontend/`: the Next.js App Router pages,
> the auth/api libs, the AppShell chrome, and the three primary views
> (Wiki / Chat / Events).

_Last updated: 2026-05-06_

---

## Design

### Stack
- Next.js 14 App Router, TypeScript, "use client" pages where they read
  auth or interact with the API.
- `react-markdown` + `remark-gfm` for rendering wiki content.
- No state library — local `useState` + a single `<AuthProvider>` context.

### Architectural rules (also in CLAUDE.md)

- **Network only via `src/lib/api.ts:apiFetch`.** Sets
  `credentials: "include"`, JSON content-type, parses `{error}` envelope
  into `ApiError` with `.status`. Never `fetch` directly.
- **Auth only via `src/lib/auth.tsx`.** `useAuth()` for state,
  `useRequireAuth()` to gate. Don't call `/api/auth/me` from a component.
- **Chrome via `<AppShell>`.** All authenticated pages wrap their content
  in it; navigation, user badge, sign-out and the admin link live there.
- **No backend HTML.** Markdown is rendered client-side; never inject HTML
  from the API.

### Sidebar — three primary views (per product spec)

| Tab | Route | Purpose |
|---|---|---|
| Wiki   | `/wiki` (default; `/` redirects here) | File tree + reader + editor |
| Chat   | `/chat`                              | Multi-turn LLM chat with search |
| Events | `/events`                            | Trigger-fire history |

**Current AppShell has Home + Wiki + Chat + Triggers — needs swap to
Wiki + Chat + Events. Triggers are inline on doc/dir pages, not a
top-level view.**

Avatar menu (top of sidebar) keeps Admin + Sign out.

### File-by-file (current state)

#### `src/app/`
- `layout.tsx` — wraps everything in `<AuthProvider>`.
- `page.tsx` — gated home, wrapped in `<AppShell>`.
- `login/page.tsx`, `signup/page.tsx` — **real**, fully working.
- `wiki/page.tsx` — **partial**: flat list of `.md` paths in a sidebar +
  rendered markdown on the right via `react-markdown` + `remark-gfm`.
  Has a "Reindex" button calling `POST /api/documents/reindex`.
  **No directory navigation, no editor, no triggers UI.**
- `chat/page.tsx` — **real**: bubbles + textarea + send (Enter submits,
  Shift+Enter newline). Conversation lives in component state — no
  persistence yet. Errors keep the user message visible with Retry.
- `triggers/page.tsx` — gated stub. **Slated for removal** — triggers
  belong inline on doc/dir pages.
- `admin/page.tsx`, `admin/users/page.tsx`, `admin/llm/page.tsx` — admin
  surface, all working. Each owns its own admin gate via
  `useRequireAuth` + `is_admin` redirect.

#### `src/lib/`
- `api.ts` — `apiFetch<T>` (credentials include, JSON content type, parses
  `{error}` envelope into `ApiError` with `.status`).
- `auth.tsx` — `<AuthProvider>` fetches `/auth/me` + `/auth/config` on
  mount; `useAuth()`, `useRequireAuth()` (redirects to `/login?next=...`,
  excludes `/login` and `/signup`); `login`, `signup`, `logout`.

#### `src/components/common/AppShell.tsx`
Vertical icon nav (Home, Wiki, Chat, Triggers) + avatar menu (admin link,
sign out).

### Wiki view — design

#### Layout
- **Tree on the left** of every wiki page — directories collapsible,
  `.md` files as leaves. Sourced from `GET /api/documents?prefix=` and
  grouped client-side.
- **Right panel** is one of: directory page, reader, editor.

#### Routing
`/wiki/[...path]` resolves at runtime:
- empty path → root tree.
- ends in `/` or matches a directory in the tree → **directory page**.
- ends in `.md` and is a file → **reader page**.

#### Directory page
- Lists immediate children (subdirs + `.md` files).
- Triggers panel (directory-scoped) — list, add, edit, delete.

#### Reader page
- `react-markdown` + `remark-gfm` rendering of the file body fetched via
  `GET /api/documents/file?path=`.
- Triggers panel (file-scoped).
- "Edit" button toggles to editor.

#### Editor
- Plain `<textarea>` with the raw markdown — **not WYSIWYG**, per spec.
- **Save** → `PUT /api/documents/file?path=` with `{body, message?}`,
  then re-fetch + return to reader.
- **Cancel** → discard, return to reader.
- **Navigate-away warning** when body is dirty (`beforeunload`).

#### Special files (deferred — see `agents.md` TBD in master)
Not implementing yet.

### Chat view (v0: stateless, no persistence)

- Message list + input at the bottom.
- Each turn, send the **full prior message list** as
  `POST /api/chat/messages`. Response replaces the list.
- Show provider error messages literally — backend already maps
  `LLMError.code` to clean `{error, code}` JSONs (e.g. "configure LLM in
  admin" for `not_configured`).
- No streaming in v0. Show a spinner while waiting.

When persistence lands (see
[agents/chat-agent.md F](../agents/chat-agent.md#f-chat-http--persistence)):
store `conversation_id` in URL (`/chat/[id]`), list past convos in a left
sub-pane.

### Events view

- Reverse-chrono list from `GET /api/events?kind=trigger.fire`.
- Each row: trigger name (resolve from cache via API), doc path,
  verdict + one-line `reason`, timestamp.
- Click expands the full payload (before/after slice, change kind).
- Cursor pagination with infinite scroll.
- Filters: `since`, `until`, `kind`, scope path.

### Admin

`/admin` already split across three routes:
- `/admin` — landing.
- `/admin/users` — list, promote/demote, delete.
- `/admin/llm` — provider/model/keys (existing-key hint shown redacted;
  empty string in PUT means "leave existing untouched"; per-key Clear
  button).

Will need a third route for **MCP connections** when we wire that.

### Components conventions
- Functional, typed props.
- Reusable UI under `src/components/<area>/`; route-scoped pieces
  co-located with the page.
- Server components fine, but anything touching auth must be `"use client"`.

---

## Progress

### Working
- Auth + signup pages, fully real.
- `<AuthProvider>` + `useAuth` / `useRequireAuth`.
- `apiFetch` + `ApiError`.
- `<AppShell>` (icon nav + avatar menu w/ admin link + sign out).
- Wiki page: flat list of `.md` paths in a sidebar; right panel renders
  the selected file via `react-markdown`; Reindex button.
- Chat page: full thread UI, stateless, error-aware.
- Admin pages (landing + Users + LLM).

### Stubbed
- Triggers page — to be deleted.
- No directory navigation, no editor in the wiki view.
- No `<EventsView>`.

### Not started
- Inline triggers panel on doc/dir pages.
- MCP admin route.

---

## Work breakdown (Next up)

### G. AppShell + routing cleanup
1. Swap nav: **Wiki / Chat / Events**, drop Home + Triggers.
2. Make `/` redirect to `/wiki`.
3. Remove the `/triggers` top-level page once inline UX is built.

### C. Wiki UI: tree, directory, reader, editor
1. **Tree component** — call `GET /api/documents?prefix=` and group paths
   into a tree. Render directories + `.md` leaves. Click navigates
   client-side.
2. **Directory page** — at route `/wiki/[...path]`; if the resolved path
   is a directory, list children + show its triggers (placeholder until
   D lands).
3. **Reader page** — at the same route when path resolves to a file;
   render markdown. Keep `react-markdown` + `remark-gfm`.
4. **Editor toggle** — Reader has an "Edit" button → swaps to a `<textarea>`
   showing the raw body. Save calls the new `PUT /api/documents/file`
   (see [flask-and-apis B](../flask-and-apis/flask-and-apis.md#b-wiki-write-path));
   Cancel discards. Add a `beforeunload` warning when dirty.

### D.6 Triggers UI (inline)
- Inline panel on doc and directory pages: list, add (NL description
  input), edit, delete, enable/disable.
- Backend: see
  [natural-language-triggers D](../natural-language-triggers/natural-language-triggers.md#d-triggers-crud--storage--engine--fan-out).

### E.2 Events page
- `/events` page with infinite-scroll list. Each row shows trigger name
  (resolved via cache), doc path, verdict + reason snippet, timestamp.
  Click expands full payload.
- API: see
  [flask-and-apis E.1](../flask-and-apis/flask-and-apis.md#e1-events-api-ui-in-frontend).

### F.6 Chat persistence UI (after backend persistence lands)
- List past conversations in a left sub-pane.
- URL holds `conversation_id` (`/chat/[id]`).

### K.2 MCP admin route
- New `/admin/mcp` once the API is real.

### Open questions
- Diff preview on Save? Cheap to add and high-value for the editor; punt
  to backlog until we feel pain.
- Chat draft auto-save in localStorage? Mirrors editor draft behavior.
- Should the tree show a subtle "last updated" timestamp per file? V0
  brief implies "I should know how progress is tracking" — could be a
  good signal.
