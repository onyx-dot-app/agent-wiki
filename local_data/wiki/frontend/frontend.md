# Frontend (ChatUI + Wiki UI + Events + Admin)

> **Part of agent-wiki v0.** See the master doc
> [`../architecture_and_progress.md`](../architecture_and_progress.md) for
> the cross-area map. Backend-side chat lives in
> [agents/chat-agent.md](../agents/chat-agent.md); trigger semantics in
> [natural-language-triggers](../natural-language-triggers/natural-language-triggers.md).
> This doc owns everything in `frontend/`: the Next.js App Router pages,
> the auth/api libs, the AppShell chrome, and the three primary views
> (Wiki / Chat / Events).

_Last updated: 2026-05-09_

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
| Wiki     | `/wiki` (default; `/` redirects here) | File tree + reader + editor |
| Triggers | `/triggers`                           | Owner's triggers (full CRUD) |
| Events   | `/events`                             | Owner's trigger-fire history |

**Chat is not a tab.** It's a global widget — see "Chat widget" below.

Avatar menu (top of sidebar) keeps Admin + Sign out.

### File-by-file (current state)

#### `src/app/`
- `layout.tsx` — wraps everything in `<AuthProvider>` and renders the
  global `<ChatWidget>`.
- `page.tsx` — gated home, wrapped in `<AppShell>`.
- `login/page.tsx`, `signup/page.tsx` — **real**, fully working.
- `wiki/page.tsx` — **partial**: flat list of `.md` paths in a sidebar +
  rendered markdown on the right via `react-markdown` + `remark-gfm`.
  Has a "Reindex" button calling `POST /api/documents/reindex`.
  **No directory navigation, no editor, no triggers UI.**
- `triggers/page.tsx` — owner-scoped Triggers tab; full CRUD over the
  current user's triggers. **Kept as a top-level view.** Inline panels
  on doc/dir pages mirror this for path-scoped management.
- `admin/page.tsx`, `admin/users/page.tsx`, `admin/llm/page.tsx`,
  `admin/web/page.tsx`, `admin/groups/page.tsx`, `admin/health/page.tsx`
  — admin surface, all working. Each owns its own admin gate via
  `useRequireAuth` + `is_admin` redirect. The Health page (backend
  liveness + queue depths, polling `/api/health` every 5s) lives here
  rather than in the main sidebar.

#### `src/lib/`
- `api.ts` — `apiFetch<T>` (credentials include, JSON content type, parses
  `{error}` envelope into `ApiError` with `.status`).
- `auth.tsx` — `<AuthProvider>` fetches `/auth/me` + `/auth/config` on
  mount; `useAuth()`, `useRequireAuth()` (redirects to `/login?next=...`,
  excludes `/login` and `/signup`); `login`, `signup`, `logout`.
- `swr.tsx` — `<SWRProvider>` mounted in `app/layout.tsx`. Default
  fetcher is `apiFetch` keyed by API path, so `useSWR("/events?...")`
  Just Works. Defaults: `revalidateOnFocus`, `revalidateOnReconnect`,
  `keepPreviousData` (no flash on key change), `dedupingInterval: 2s`,
  `errorRetryCount: 3`. **Per-resource hooks live next to the resource**
  (`useEvents` in `events.ts`, `useTriggers` in `triggers.ts`,
  `useHealth` in `health.ts`, `useLLMStatus` in `llm.ts`) so pages
  call a typed hook, not raw SWR. After a write, mutate the cache —
  optimistic update + revalidate, e.g.
  `refresh((cur) => ({...}), { revalidate: true })`.

  Why SWR over a hand-rolled `useEffect` + `useState`: cache survives
  cross-route navigation, so revisiting a page shows the previous data
  instantly while a background revalidation runs. The old pattern blanked
  to `[]`/`null` on remount.

#### `src/components/common/AppShell.tsx`
Vertical icon nav (Wiki, Triggers, Events) + avatar menu (admin link,
sign out). Also renders a unified **status banner** at the top of the
content column via the `StatusBanner` component, which shows at most
one of:

1. **Backend health banner** (red) — driven by `useHealth` polling
   `GET /api/health` every 15s. Fires when the request fails (backend
   unreachable) or `status === "degraded"`. Non-dismissible — clears
   automatically when the backend recovers. Admins get a deep link to
   `/admin/health`; non-admins are told to ask an admin.
2. **LLM setup banner** (amber) — driven by `useLLMStatus`
   (`GET /api/llm/status`). Fires when `configured === false`.
   Dismissible per-tab (`sessionStorage["llm-banner-dismissed"]`) so
   it re-appears in a fresh tab until the system is configured.
   Admins get a deep link to `/admin/llm`; non-admins are told to ask
   an admin.

Backend health takes precedence over LLM setup — if both signals fire,
only the health banner renders. Both banners skip rendering pre-auth.

#### `src/components/chat/ChatWidget.tsx`
Global chat widget mounted from `app/layout.tsx`. Renders only when a
user is logged in. Three modes (`closed` | `widget` | `expanded`)
persisted in `localStorage` along with the expanded width.

### Wiki view — design

#### Layout
- **Tree on the left** of every wiki page — directories collapsible,
  `.md` files as leaves. Sourced from `GET /api/documents?prefix=` and
  grouped client-side.
- **Right panel** is one of: directory page, reader, editor.
- **Search bar** (`components/wiki/WikiSearch.tsx`) at the top of every
  wiki page (rendered by `WikiRoute`, above both Explorer and FileViewer).
  Debounced typeahead against `GET /api/documents/search?q=…&limit=8`
  (BM25 via pg_textsearch). Dropdown shows title, path, and a snippet
  with `**match**` markers rendered as bold spans; ↑/↓/Enter selects;
  Esc / outside-click closes; picking a hit routes to `/wiki/<path>`.
  Stale-response guard via a request-sequence counter so a slower reply
  can't overwrite a fresher one.

#### Routing
`/wiki/[...path]` resolves at runtime:
- empty path → root tree.
- ends in `/` or matches a directory in the tree → **directory page**.
- ends in `.md` and is a file → **reader page**.

#### Directory page
- Lists immediate children (subdirs + `.md` files).
- **My-triggers panel** (directory-scoped): the current user's triggers
  scoped to this directory — list, add, edit, delete. Per-user only;
  other users' triggers are not visible.
- **Drag-and-drop reorganize.** Each row is `draggable`. Folders are drop
  targets; breadcrumb crumbs (including "Wiki" for the root) are drop
  targets too — drop on a crumb to move out of the current folder. Move
  goes through `POST /api/documents/move`; a name conflict at the
  destination returns 409 and is surfaced as an inline error.
- **Rename inline.** A pencil icon on each row opens an in-place rename
  input; submits via the same `/documents/move` endpoint.

#### Reader page
- `react-markdown` + `remark-gfm` rendering of the file body fetched via
  `GET /api/documents/file?path=`.
- **My-triggers panel** (file-scoped): the current user's triggers
  scoped to this file. Per-user only.
- "Edit" button toggles to editor. "Rename" button prompts for a new
  filename and calls `/documents/move`, then routes to the new path.

#### Editor
- Plain `<textarea>` with the raw markdown — **not WYSIWYG**, per spec.
- **Save** → `PUT /api/documents/file?path=` with `{body, message?}`,
  then re-fetch + return to reader.
- **Cancel** → discard, return to reader.
- **Navigate-away warning** when body is dirty (`beforeunload`).

#### Special files (deferred — see `agents.md` TBD in master)
Not implementing yet.

### Chat widget

`<ChatWidget>` is mounted globally from `app/layout.tsx` so the agent is
reachable on **every** page (Wiki / Triggers / Events / Admin). Not a
top-level tab, not a route.

- **Three modes:**
  - `closed` — floating circular FAB in the bottom-right corner.
  - `widget` — small floating panel anchored bottom-right (~380×560).
  - `expanded` — full-height panel anchored to the **right** edge of the
    screen, resizable by dragging its left edge. While expanded, the
    page content is pushed left (body gets `padding-right`) rather than
    being overlaid.
- **Persistence.** Mode and expanded-width persist in `localStorage`
  (`chat-widget:mode`, `chat-widget:expanded-width`). Conversation
  history lives in component state for now and resets on page refresh.
- **Streaming.** Reads the SSE stream from `apiStream`; renders text
  deltas live and surfaces `error` events with Retry.
- **New-chat button** clears the in-memory thread.

Not yet built (still planned):

- **Location-awareness.** Sending the current route as
  `location: { path }` on `POST /api/chat/messages`.
- **Propose-and-apply UX.** Inline draft cards for `propose_doc_edit` /
  `propose_create_trigger` tool calls with Apply / Reject buttons that
  call the corresponding API and report the outcome back as a
  tool-result.
- **Persistent conversations** (when the backend persistence lands):
  list past convos in a sub-pane within the widget; URL fragment carries
  `conversation_id`.

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
- Chat: global `<ChatWidget>` (FAB → bottom-right widget → resizable
  right-side expanded panel that pushes the page left), error-aware,
  streaming.
- Admin pages (landing + Users + LLM).

### Stubbed
- Triggers page — kept; needs CRUD UI wired to per-user API.
- No directory navigation, no editor in the wiki view.
- No `<EventsView>`.
- Chat widget is stateless across reloads and not yet location-aware /
  propose-and-apply.

### Not started
- Inline (per-user) triggers panel on doc/dir pages.
- Chat widget: location-aware messages + propose-and-apply UX.
- MCP admin route.

---

## Work breakdown (Next up)

### G. AppShell + routing cleanup
1. ~~Swap nav: **Wiki / Triggers / Events**, drop Home and the standalone
   Chat tab.~~ **Done** (Chat tab removed; nav is Wiki / Triggers / Events).
2. Make `/` redirect to `/wiki`.
3. ~~Mount the chat surface globally so it's available on every page.~~
   **Done** as `<ChatWidget>` mounted in `app/layout.tsx`.
4. **Chat widget enhancements** — read current route as `location.path`
   and send it on every `POST /api/chat/messages`. Inline draft cards
   for `propose_doc_edit` and `propose_create_trigger` with Apply /
   Reject buttons that call the corresponding API and report the outcome
   back as a tool-result.

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

### D.6 Triggers UI (inline + top-level, per-user)
- **Triggers tab (`/triggers`)** — full CRUD over the current user's
  triggers across the whole wiki. List, search by scope path, create with
  arbitrary `scope_path`, edit, delete, enable/disable.
- **Inline panel** on doc and directory pages — same CRUD but pre-scoped
  to the current path; shows only the user's own triggers.
- Both views call the per-user API; no other user's triggers ever appear.
- Backend: see
  [natural-language-triggers D](../natural-language-triggers/natural-language-triggers.md#d-triggers-crud--storage--engine--fan-out).

### E.2 Events page
- `/events` page with infinite-scroll list. Each row shows trigger name
  (resolved via cache), doc path, verdict + reason snippet, timestamp.
  Click expands full payload.
- API: see
  [flask-and-apis E.1](../flask-and-apis/flask-and-apis.md#e1-events-api-ui-in-frontend).

### F.6 Chat persistence UI (after backend persistence lands)
- Sub-pane inside `<ChatWidget>` listing past conversations.
- URL fragment carries `conversation_id` so deep-linking + reload work.

### K.2 MCP admin route
- New `/admin/mcp` once the API is real.

### Open questions
- Diff preview on Save? Cheap to add and high-value for the editor; punt
  to backlog until we feel pain.
- Chat draft auto-save in localStorage? Mirrors editor draft behavior.
- Should the tree show a subtle "last updated" timestamp per file? V0
  brief implies "I should know how progress is tracking" — could be a
  good signal.
