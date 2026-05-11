# Coding Tool Launchers — Design

**Status:** spec, not yet implemented
**Date:** 2026-05-11
**Source ask:** `local_data/wiki/Wiki Project/Help Wanted.md` →
"Start Claude Code / Codex from the button with the context. Ask the
user to set up MCP if not already."

## Goal

Turn the wiki's **Run Agent** button into a true kickoff for coding
work. One click on a wiki page should:

1. Open the user's preferred CLI coding agent (Claude Code today,
   Codex today, anything CLI tomorrow).
2. Land it in the right working directory — a scratch dir, a repo on
   disk the user has linked to this wiki page, or a path the user
   picks at launch.
3. Pre-wire the agent with our inbound MCP server so reads and writes
   to the wiki happen first-class from inside the agent's tool surface.
4. Inject the page body + a user message + the working dir + linked
   repos as the first prompt so the agent has full context from t=0.
5. Track the session on the wiki side so the file viewer shows
   "1 agent currently working on this doc," supports Resume, and
   stamps every MCP edit with the originating agent session.

The wiki is the place where work is initiated and tracked. The agent
runs wherever the user wants their work to happen — local repos
included. The wiki does not have to be the agent's working directory.

## Non-goals (v1)

- Cloud-hosted ephemeral container sessions (the cloud-launch option
  from brainstorming — deferred to v2; not to be confused with
  Approach C, the manifest registry, which _is_ v1).
- Native (Tauri/Rust) helper binary (H1 — deferred; v1 ships an
  npm-distributed launcher).
- Onyx Craft itself (it's listed in the manifest registry but its
  agent implementation is out of scope; the launcher framework just
  makes room for it).
- Per-token permission scoping for the auto-minted launcher token —
  inherits user's full ACL same as today's MCP tokens.
- Codespaces / claude.ai web handoff (`web_handoff` kind reserved in
  the manifest but no manifest ships).

## Architecture overview

```
┌────────────────┐   1. click Run Agent       ┌────────────────────┐
│ Wiki UI (Next) │ ─────────────────────────▶ │  GET /api/         │
│ file viewer    │ ◀── tool catalog ────────── │  launchers         │
│ RunAgentModal  │                            └────────────────────┘
└────────┬───────┘
         │ 2. wizard: pick tool, mint token if missing,
         │    install helper if missing, pick working dir
         │
         │ 3. POST /api/launch {tool_id, wiki_path, message, working_dir}
         ▼
┌────────────────────────────────────────────┐
│  backend/app/api/launchers.py              │
│   • mints `launch_codes` row (lc_…, 60s)   │
│   • creates `agent_sessions` row           │
│   • returns launch_code + agentwiki:// URI │
└────────┬───────────────────────────────────┘
         │ 4. browser → `agentwiki://run?code=lc_…&tool=claude-code`
         ▼
┌────────────────────────────────────────────┐
│  Local helper (npm: @agentwiki/launcher)   │
│   • URI handler dispatch                   │
│   • POST /api/launch/exchange {code}       │
│     ← {mcp_token, manifest, payload}       │
│   • write mcp config to tmpfile per        │
│     manifest.mcp_config_format             │
│   • mkdir ~/.agentwiki/sessions/<id>/      │
│     (or chdir to user's chosen dir)        │
│   • open user's terminal, spawn binary     │
│     with interpolated argv + env           │
│   • capture cli_session_id per manifest    │
│   • heartbeat /api/agent-sessions/:id      │
└────────┬───────────────────────────────────┘
         │ 5. agent runs; MCP writes carry
         │    X-Agentwiki-Session: as_… header
         │    → activity rows stamped, pubsub
         │    fans live updates to web UI
         ▼
┌────────────────────────────────────────────┐
│  backend/app/mcp_server/  (already shipped)│
└────────────────────────────────────────────┘
```

## Approach decision — Hybrid manifest (Approach C)

Three approaches were considered:

- **A — Backend-authoritative registry.** Tool defs in Python; helper
  is a thin generic dispatcher receiving `{kind, command, argv, env,
cwd, mcp_config_blob}`. New tool = 1 backend file. Helper trusts
  backend within a binary allow-list.
- **B — Helper-authoritative registry.** Adapters compiled into the
  npm helper. New tool = helper version bump + npm publish + every
  user upgrades. Release cadence coupled to tool catalog growth.
- **C — Hybrid manifest (recommended).** Backend serves versioned
  manifest JSONs; helper has a small bounded interpolator + named
  adapters. New tool = drop one JSON file in the backend. Helper
  retains validation surface.

**Picking C.** Reasoning: Onyx Craft / opencode / openclaw can land
without forcing every user to `npm update -g`. Manifest DSL is small
(~9 substitution vars, 3 mcp_config_format adapters). Forward path to
a native helper (H1) reuses the identical manifest contract — the
manifest is the stable seam across helper rewrites.

## Data model

Three new tables. All Postgres + SQLAlchemy ORM. Migration
`backend/app/db/migrations/versions/0005_launchers.py`.

### `launch_codes`

Single-use short-lived bearer the helper exchanges for the MCP token
and manifest payload. The MCP token itself is **never** in the URI bar.

| col                | type                   | notes                                      |
| ------------------ | ---------------------- | ------------------------------------------ |
| `id`               | `str(34)` PK           | `lc_<32-byte-urlsafe>`                     |
| `user_id`          | FK `users.id`          |                                            |
| `agent_session_id` | FK `agent_sessions.id` |                                            |
| `mcp_token_id`     | FK `mcp_tokens.id`     | which token to hand the helper             |
| `created_at`       | `datetime`             |                                            |
| `expires_at`       | `datetime`             | `created_at + 60s`                         |
| `consumed_at`      | `datetime?`            | exchange marks this; second exchange = 409 |

Index `ix_launch_codes_expires_at` for the sweep.

### `agent_sessions`

One Run-Agent invocation. Lives across launch → terminal → close.

| col                | type        | notes                                                                                          |
| ------------------ | ----------- | ---------------------------------------------------------------------------------------------- |
| `id`               | `str` PK    | `as_<uuid>`                                                                                    |
| `user_id`          | FK          |                                                                                                |
| `tool_id`          | `str`       | `"claude-code"`, `"codex"`, … (matches manifest id)                                            |
| `wiki_path`        | `str?`      | page the user launched from                                                                    |
| `working_dir`      | `str?`      | absolute path; null = scratch (`~/.agentwiki/sessions/<id>/`)                                  |
| `initial_prompt`   | `text`      | full prompt (page body + workdir + linked_repos + user msg); persisted so resume reproduces it |
| `cli_session_id`   | `str?`      | helper writes back what claude/codex assigned                                                  |
| `status`           | `str`       | `pending` → `active` → (`idle`) → `closed` / `failed`                                          |
| `started_at`       | `datetime`  |                                                                                                |
| `last_activity_at` | `datetime`  | bumped by heartbeat + every MCP call                                                           |
| `closed_at`        | `datetime?` |                                                                                                |

Indexes: `(user_id, status)`, `(wiki_path)`.

### `page_working_dirs`

Wiki page → preferred working directory binding. Per-user (different
machines, different paths).

| col           | type                        | notes         |
| ------------- | --------------------------- | ------------- |
| `user_id`     | FK (composite PK part 1)    |               |
| `wiki_path`   | `str` (composite PK part 2) |               |
| `working_dir` | `str`                       | absolute path |
| `updated_at`  | `datetime`                  |               |

### Page frontmatter — `linked_repos`

Not a table — markdown frontmatter on wiki pages:

```yaml
---
linked_repos:
  - git@github.com:onyx-dot-app/onyx
  - git@github.com:onyx-dot-app/agent-wiki
linked_working_dir_hint: "~/code/onyx" # display-only suggestion; per-user real value lives in page_working_dirs
---
```

Parsed by existing `app/wiki/frontmatter.py`. Repo URLs are shared
across users (they're project metadata); the local checkout path is
per-user (different machines).

### `agent_activities` extension

Add nullable column `agent_session_id` (FK `agent_sessions.id`) +
index. Stamped by the MCP server when the request carries
`X-Agentwiki-Session: as_…`. Backward compatible — header absent =
NULL stamp, existing chat-agent edits unaffected.

## Launch protocol

```
USER          FRONTEND                    BACKEND                  HELPER (npm)             CLI (claude/codex)
 │            │ GET /api/launchers        │                        │                         │
 │            │ ◀── catalog ──            │                        │                         │
 │ wizard:    │  (token? helper? cli?     │                        │                         │
 │            │   workdir defaults?)      │                        │                         │
 │ click Run  │ POST /api/launch          │                        │                         │
 │            │   {tool_id, wiki_path,    │                        │                         │
 │            │    message, working_dir}  │                        │                         │
 │            │  ─────────────────────────▶                        │                         │
 │            │                            │ • create agent_sessions│                         │
 │            │                            │ • mint launch_codes    │                         │
 │            │                            │ • build initial_prompt│                         │
 │            │ ◀─ {launch_code, uri} ───  │                        │                         │
 │            │ window.location = uri      │                        │                         │
 │            │                            │  POST /api/launch/     │                         │
 │            │                            │  exchange {code}       │                         │
 │            │                            │  ◀─────────────────────│                         │
 │            │                            │ • consume code         │                         │
 │            │                            │ • status pending→active│                         │
 │            │                            │ ─── {mcp_token,        │                         │
 │            │                            │     endpoint,          │                         │
 │            │                            │     manifest,          │                         │
 │            │                            │     payload} ────────▶ │                         │
 │            │                            │                        │ • validate workdir      │
 │            │                            │                        │ • write mcp config tmp  │
 │            │                            │                        │ • interpolate argv      │
 │            │                            │                        │ • open terminal, spawn ▶│
 │            │                            │                        │   env AGENTWIKI_…       │
 │            │                            │                        │ • capture cli_session_id│
 │            │                            │ POST /api/agent-       │                         │
 │            │                            │ sessions/:id/cli-      │                         │
 │            │                            │ session {cli_session_id}                         │
 │            │                            │  ◀─────────────────────│                         │
 │            │                            │                        │                         │
 │            │                            │ MCP calls land with    │  CLI Bearer + X-Agent-  │
 │            │                            │ X-Agentwiki-Session    │  wiki-Session header    │
 │            │                            │ stamp every activity   │                         │
 │            │ ◀── SSE: agent_session_    │                        │                         │
 │            │     updated frames         │                        │                         │
```

### State machine — `agent_sessions.status`

- `pending` — row + launch code minted, helper not yet exchanged.
- `active` — helper exchanged successfully, CLI running.
- `idle` — `last_activity_at` > 5 min ago. Soft, computed in sweep.
  Resumable from UI.
- `closed` — user closed in UI, helper POSTed `/close` on CLI exit,
  or sweep evicted after 24h idle.
- `failed` — exchange failed, helper reported spawn error, MCP token
  revoked mid-session.

### Failure paths

| Trigger                       | Helper reaction                  | UX                                                |
| ----------------------------- | -------------------------------- | ------------------------------------------------- |
| `lc_` expired (>60s)          | exchange returns 410             | Modal re-opens with "Launch expired, try again"   |
| `lc_` consumed                | exchange returns 409             | Same as expired                                   |
| Helper not URI-registered     | OS shows "no application"        | Frontend probe times out → install pane           |
| `claude` CLI absent           | helper aborts spawn              | POSTs `/close {error="cli_not_found"}` → toast    |
| `working_dir` doesn't exist   | helper validates                 | aborts, `/close {error="invalid_workdir"}`        |
| `working_dir` outside `$HOME` | helper prompts OS-native confirm | extension; defer                                  |
| MCP token revoked mid-session | CLI 401 on next tool call        | session flips `failed`, error visible in terminal |

### Resume

`/agents` page and the file viewer's `ActiveSessionsList` show
`AgentSession` rows. Resume button POSTs `/api/launch` with
`resume_session_id=as_…`. Backend reuses `wiki_path`, `working_dir`,
`initial_prompt`; mints a new launch code; payload includes prior
`cli_session_id`. Helper interpolates manifest's `resume.argv` (e.g.
`claude --resume <cli_session_id>`). Same scratch dir, same
conversation.

### Heartbeat + sweep

Helper POSTs `/api/agent-sessions/:id/heartbeat` every 60s while CLI
process is running. New task `expire_launch_artifacts` on the
existing `lightweight_maintenance_queue`, cron every 60s:

- Delete `launch_codes` past `expires_at`.
- Mark `agent_sessions` `idle` after 5 min no activity.
- Mark `closed` after 24h idle.

## Manifest DSL (`manifest_v1`)

```json
{
  "manifest_version": 1,
  "id": "claude-code",
  "name": "Claude Code",
  "tagline": "Anthropic's terminal coding agent.",
  "icon_url": "/icons/claude-code.svg",
  "kind": "local_cli",
  "cli_check": {
    "binary": "claude",
    "version_flag": "--version",
    "min_version": "1.0.0",
    "install_hint_url": "https://docs.claude.com/code/install"
  },
  "mcp_config_format": "claude_json",
  "launch": {
    "binary": "claude",
    "argv": ["--mcp-config", "${mcp_config_path}", "${initial_prompt}"],
    "env": {
      "AGENTWIKI_SESSION_ID": "${session_id}",
      "AGENTWIKI_ENDPOINT": "${endpoint}"
    },
    "cwd": "${working_dir}"
  },
  "resume": {
    "argv": [
      "--resume",
      "${cli_session_id}",
      "--mcp-config",
      "${mcp_config_path}"
    ],
    "env": { "AGENTWIKI_SESSION_ID": "${session_id}" },
    "cwd": "${working_dir}"
  },
  "session_id_capture": {
    "source": "file_watch",
    "path": "${home}/.claude/projects/${dirhash}/",
    "pattern": "*.jsonl",
    "extract": "filename_basename"
  }
}
```

### `kind` enum

- `local_cli` — helper spawns terminal + binary. Claude Code, Codex,
  opencode, openclaw.
- `in_app` — no helper involvement; backend enqueues a server-side
  agent task. Onyx Craft. Carries `task_kind` not `launch`.
- `web_handoff` — open URL in new tab with substituted params.
  Reserved (Codespaces / claude.ai).

### Bounded interpolation vars

Helper enforces an allow-list. Anything else in `${…}` → manifest
rejected at validation, helper exits with error. No nesting, no
shell substitution, no command chaining.

| var                  | source                                                       |
| -------------------- | ------------------------------------------------------------ |
| `${token}`           | exchange response `mcp_token`                                |
| `${endpoint}`        | exchange response `endpoint`                                 |
| `${session_id}`      | exchange response `payload.session_id`                       |
| `${cli_session_id}`  | exchange response `payload.cli_session_id` (resume only)     |
| `${working_dir}`     | exchange response `payload.working_dir`                      |
| `${initial_prompt}`  | exchange response `payload.initial_prompt`                   |
| `${mcp_config_path}` | helper-local temp file path written from `mcp_config_format` |
| `${home}`            | `os.homedir()`                                               |
| `${dirhash}`         | claude-code's hash of `working_dir` for file_watch capture   |

### `mcp_config_format` adapters

Named adapters built into the helper:

- `claude_json` — writes `{"mcpServers":{"agent-wiki":{"url":"…","headers":{"Authorization":"Bearer …"}}}}` to tmpfile.
- `codex_toml` — writes `[mcp_servers.agent-wiki]\nurl="…"\nheaders.Authorization="Bearer …"` to tmpfile.
- `none` — no mcp config (edge case).

### `session_id_capture.source` adapters

- `file_watch` — helper watches directory for new file matching
  pattern, extracts id from filename. Claude Code's model.
- `stdout_regex` — helper tees CLI stdout to regex, captures match.
  Codex's model.
- `none` — no resume support; manifest's `resume` is also null.

### Shipped manifests

`backend/app/launchers/manifests/`:

- `claude_code.json` (above).
- `codex.json` — `kind: local_cli`, `mcp_config_format: codex_toml`,
  argv uses `--config-overrides 'mcp_servers.agent-wiki.url=…'`,
  capture `stdout_regex` for session id.
- `onyx_craft.json` — `kind: in_app`, `task_kind: craft_agent`. Used
  by `POST /api/launch` to enqueue a server-side task instead of
  minting a launch code. Streams updates via existing `job://`
  resource subscription.

## Wizard UX

### State A — steady state (everything set up)

```
┌─── Run Agent ────────────────────────────[×]──┐
│ Tool:   ( • ) Claude Code                      │
│         (   ) Codex                            │
│         (   ) Onyx Craft (in-app)              │
│         [+ Set up another tool]                │
│                                                │
│ Working directory:                             │
│  [ ~/code/onyx                          ▾ ]    │
│  ☑ Remember as default for this page           │
│                                                │
│ Message:                                       │
│  ┌────────────────────────────────────────┐    │
│  │ Audit the chat assistant flow.        │    │
│  └────────────────────────────────────────┘    │
│                                                │
│ Active sessions on this page:                  │
│  • Claude Code · 12 min ago · 3 edits          │
│    [Resume] [Close]                            │
│                                                │
│                       [Cancel]  [Run ▶]        │
└────────────────────────────────────────────────┘
```

### State B — setup wizard

Step 1: multi-select tool catalog. Step 2: per-tool checklist with
✓/⚠ for: MCP token, helper installed, CLI binary detected.

```
┌─── Set up your coding tools ─── step 2 of 2 ──┐
│ Claude Code                                    │
│   ✓ MCP API key (minted "claude-launcher")     │
│   ⚠ Launcher not installed on this machine     │
│        $ npm install -g @agentwiki/launcher    │
│        [Copy] [I've installed it →]            │
│                                                │
│ Codex                                          │
│   ✓ MCP API key (reused)                       │
│   ✓ Launcher detected                          │
│   ⚠ codex CLI not found in PATH                │
│        Install: https://github.com/openai/codex│
│        [Skip Codex] [I've installed it →]      │
│                                                │
│                          [Back]  [Done]        │
└────────────────────────────────────────────────┘
```

### Detection probes

**Helper presence** — frontend creates hidden iframe with
`agentwiki://probe?nonce=…`. Helper's URI handler POSTs
`/api/launch/probe-ack {nonce, helper_port}`. Frontend polls
`/api/launch/probe-status?nonce=…` for 800ms. Result cached in
`sessionStorage`.

**CLI binary** — once helper detected, frontend POSTs to helper's
ephemeral localhost port (read from probe-ack): `/probe-cli
{tool_ids:[…]}`. Helper runs `which <binary>`, `<binary>
--version`, compares to manifest's `min_version`. Returns
`{[tool_id]: {present, version, meets_min}}`.

**MCP token** — pure server-side; reads `mcp_tokens` for current
user. Auto-mints "claude-launcher" if none exist.

### Default working dir resolution order

1. User's most recent `page_working_dirs` entry for this wiki path.
2. Frontmatter `linked_working_dir_hint` (display-only; user must
   confirm and write to `page_working_dirs`).
3. Scratch `~/.agentwiki/sessions/<id>/`.

## New backend modules

```
backend/app/
  api/
    launchers.py            GET /api/launchers,
                            POST /api/launch,
                            POST /api/launch/exchange (bearer = launch_code),
                            POST /api/launch/probe-ack,
                            GET  /api/launch/probe-status
    agent_sessions.py       GET /api/agent-sessions,
                            POST /api/agent-sessions/:id/heartbeat,
                            POST /api/agent-sessions/:id/cli-session,
                            POST /api/agent-sessions/:id/close
  launchers/
    __init__.py
    registry.py             ManifestRegistry, Manifest pydantic model
    manifests/              git-tracked JSON files
      claude_code.json
      codex.json
      onyx_craft.json
    sessions.py             AgentSession repo (free functions)
    page_dirs.py            page_working_dirs repo
  auth/
    launch_codes.py         create / consume / expire_sweep
  tasks/
    expire_launch_artifacts.py  lightweight_maintenance_queue cron, 60s
  db/
    models.py               + LaunchCode, AgentSession, PageWorkingDir
                            + agent_activities.agent_session_id column
    migrations/versions/
      0005_launchers.py
```

## New frontend modules

```
frontend/src/
  components/
    wiki/
      RunAgentModal.tsx           rewrite from stub — wizard host
      ActiveSessionsList.tsx      file-viewer header widget
    agents/
      SetupWizard.tsx             multi-step (tool select → checklist)
      ToolCard.tsx                catalog row with setup status
  lib/
    launchers.ts                  typed API client, helper probe,
                                  CLI probe, session list SWR hook
  app/
    agents/page.tsx               + "Coding tools" section above keys
```

## New repo

```
packages/agentwiki-launcher/    (npm workspace, publishes
                                 @agentwiki/launcher)
  package.json
  bin/agentwiki-launcher          single Node CLI entry
  src/
    index.ts                      URI handler dispatch
    manifest.ts                   JSON-Schema validator + interpolator
    exchange.ts                   POST /api/launch/exchange call
    mcp_config/
      claude_json.ts
      codex_toml.ts
    spawn.ts                      terminal-open + binary spawn per OS
    capture/
      file_watch.ts
      stdout_regex.ts
    register/
      postinstall.ts              per-OS URI scheme registration
      darwin.ts                   .app bundle + LSSetDefaultHandler
      linux.ts                    .desktop file + xdg-mime
      win32.ts                    registry edits via reg.exe
    server.ts                     localhost http for /probe-cli
```

## Security model

- **No long-lived token in URI.** `launch_codes` is single-use,
  60-second TTL.
- **Helper has bounded execution surface.** Manifest interpolator
  allow-lists vars. No shell strings, no nested templates, no
  arbitrary command construction. Binary names come from manifest's
  `launch.binary` (literal, not templated). Backend ships the
  manifests; if backend is compromised the manifest can request
  running any binary in PATH, so backend integrity == client
  integrity (same trust boundary as any web app pushing client code).
- **Working dir validation** — helper rejects non-existent dirs,
  prompts OS-native confirm for paths outside `$HOME`.
- **Same ACL as web UI.** Auto-minted launcher token inherits user's
  full ACL — same as today's manually-created MCP tokens. No new
  authz surface.
- **Heartbeat is per-session, not credential auth.** Heartbeat
  endpoint uses session id in URL (not as bearer) but requires the
  user's cookie / bearer; spoofing requires already being authenticated
  as the same user.

## Feature flag + rollout

`CONFIG.launchers_enabled` (env `LAUNCHERS_ENABLED`, default `false`).
Gates: Run Agent button visibility, `/api/launchers` returning 404,
exchange returning 503.

| Phase | Scope                                                                                     | Gate                                     |
| ----- | ----------------------------------------------------------------------------------------- | ---------------------------------------- |
| P0    | Backend tables + APIs + manifests. TestClient only. Flag off.                             | Backend tests green                      |
| P1    | `@agentwiki/launcher@0.1.0-alpha` published. URI scheme registration.                     | macOS + Linux + Windows manual smoke     |
| P2    | Frontend wizard + ActiveSessionsList + `/agents` Coding-tools section. Self-host flag on. | Self-hosting smoke (real claude session) |
| P3    | Public release. Flag default-on. README + wiki docs updated.                              | Cross off Help Wanted bullet             |

## Testing

### Backend

| File                                   | Coverage                                                                                                                 |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `tests/test_launch_codes.py`           | Repo round-trip, idempotent consume, expired returns None, sweep deletes, cross-user fails                               |
| `tests/test_launchers_manifests.py`    | Registry loads + validates; rejected unknown `${var}`; rejected missing required fields; Python model ↔ JSON parity     |
| `tests/test_launch_api.py`             | Catalog with setup_status; happy launch; exchange 410/409/cross-user; auto-mint token if none; pending→active transition |
| `tests/test_agent_sessions.py`         | Heartbeat updates; idle/closed sweep; cli_session_id POST; close from helper; list-for-page; concurrent-launch race      |
| `tests/test_page_working_dirs.py`      | Per-user isolation; default cascade                                                                                      |
| `tests/test_mcp_session_stamp.py`      | `X-Agentwiki-Session` stamps `agent_activities.agent_session_id`; absent = NULL; unknown id = 400                        |
| `tests/test_launch_wiki_metadata.py`   | `linked_repos` frontmatter parsed and included in initial_prompt; ACL respected (403)                                    |
| `tests/integration/test_launch_e2e.py` | Full flow via TestClient — mint, fake helper exchange, assert row, fake MCP call stamps activity. No real CLI spawn.     |

### Helper

`packages/agentwiki-launcher/`:

- `manifest.test.ts` — schema validator accepts good, rejects bad `${vars}`, shell metachars, nested templates.
- `interpolator.test.ts` — all 9 vars substitute; unknown throws.
- `mcp_config_writer.test.ts` — `claude_json` + `codex_toml` byte-exact output for known token.
- `spawn.test.ts` (mocked) — argv per manifest; env merge; workdir validation (non-dir, unreadable, outside-$HOME confirm).
- `uri_handler.test.ts` — extracts `code`/`tool`/`endpoint`; rejects unknown scheme + extra params.

No live-CLI tests in v1. Manual QA checklist below.

### Manual QA checklist (pre-release)

Per OS:

- Install `npm install -g @agentwiki/launcher`.
- Click Run Agent on a wiki page.
- Verify Terminal/$TERMINAL opens, `claude` runs in scratch dir, MCP wired.
- From inside claude: write a doc edit → confirm wiki UI updates live.
- Close terminal → confirm session marks `closed` in wiki UI.

OSes: macOS (Terminal.app), Linux Ubuntu (gnome-terminal), Windows (Windows Terminal).

## Docs to update at P3

- This page — rewrite the "Status: spec, not yet implemented" header to a status section describing shipped surface area; move the "three approaches" rationale into a "Design rationale" appendix; keep the rest mostly intact (architecture diagrams stay valid).
- `local_data/wiki/Wiki Project/Specific Features/MCP Server Inbound.md` — add launcher integration section + `X-Agentwiki-Session` header docs.
- `README.md` — feature bullet, link to wiki page.
- `local_data/wiki/Wiki Project/Help Wanted.md` — cross off the launcher bullet, add v2 follow-ons (H1 native binary, Option C cloud session).
- `local_data/wiki/Wiki Project/Work Progress Tracker.md` — new "Coding Tool Launchers" section with P0–P3 checklist.

## Open questions (defer to v2 or as-arises)

- **CLI binary version drift.** If `claude --version` jumps to 2.x with breaking argv changes, manifest's `min_version` blocks launch but doesn't auto-pin. Decision: ship per-version manifests (`claude_code_v1.json`, `claude_code_v2.json`)? Or version-aware argv blocks inside one manifest? Defer — pick when it first hurts.
- **Windows terminal-open** — `wt` (Windows Terminal) is the modern default but isn't on every Windows install. Fallback to `cmd /k`? Or detect at install?
- **Onyx Craft task implementation.** This spec makes room for it but Craft's own agent is its own work. Whether Craft runs on `documents_queue` or gets `craft_queue` is a Craft-team decision.
- **Multiple concurrent launchers on same machine.** What if user clicks Run Agent twice in quick succession? Each gets its own URI + helper invocation + scratch dir. Two parallel terminals open. Allowed; we don't serialize. May want per-user concurrent-session cap later.
- **Per-machine helper identity.** Helper posts a `machine_id` (random uuid persisted at install, `~/.agentwiki/machine.id`) on exchange. Used today only for telemetry / `page_working_dirs` defaulting. Future: per-machine session lists, "active sessions on your other laptops".
