# MCP server (operator + agent guide)

This is the **how to use it** doc. The agent-wiki backend exposes itself
as a Model Context Protocol server so external coding agents (Claude
Code, Cursor, Codex, custom harnesses) can read, search, edit, and
subscribe to wiki docs as a first-class workspace.

> For the architectural design — request lifecycle, ACL plumbing,
> staleness model, module layout — see
> `local_data/wiki/mcp-server/mcp-server.md`. This doc is the
> end-user-facing companion.

## What you can do

Authenticated with a personal API key, an external agent can:

| Capability | Tool | Notes |
|---|---|---|
| Search | `search_wiki(query)` | BM25 over `pg_textsearch`, ACL-filtered. |
| Read at HEAD or any sha | `read_doc(path, sha?)` | HEAD reads auto-subscribe so future changes push notifications. |
| Browse history | `list_history(path)` | Same shape as `GET /api/documents/file/history`. |
| Ask in natural language | `ask_nl_question(query)` | RAG over the wiki via a curated read-only sub-agent. |
| Surgical edit | `edit_doc(path, old_string, new_string, ...)` | Fuzzy match. Optional `base_sha` for optimistic concurrency. |
| Atomic batch edit | `multi_edit(path, edits[], ...)` | One commit covers all edits. |
| Full-body overwrite or create | `write_doc(path, body, ...)` | `base_sha` REQUIRED on overwrites. |
| Line-anchored diff | `apply_patch(path, patch, ...)` | Unified-diff format with fuzzy fallback. |
| Rename / move | `move_path(old, new, ...)` | Rewrites ACL rows on the moved path. |
| Create folder | `create_directory(path, ...)` | `.gitkeep` placeholder. |
| Heavy NL update | `update_doc_nl(path, instruction, ...)` | Async — returns `{job_id, status_uri: "job://<id>"}`. The session is auto-subscribed; status changes push over the SSE stream. |

You can also list all wiki pages (`resources/list`), read individual
pages (`resources/read`), and subscribe to per-page or per-job push
notifications (`resources/subscribe`, `resources/unsubscribe`) via the
standard MCP resources surface.

## Step 1 — generate an API key

The wiki UI has an **Agents** tab in the left sidebar. Open it, click
"Generate API key", give it a name (e.g. `claude-code laptop`), copy
the value once. You won't see it again — if you lose it, revoke and
regenerate.

The endpoint URL is shown on the same page; it follows the deployment's
origin (e.g. `https://wiki.your-org.example/api/mcp` in production,
`http://localhost:8080/api/mcp` for local development).

## Step 2 — wire it into your agent

The transport is **Streamable HTTP** with bearer auth.

- POST `/api/mcp` for per-call request/response.
- GET `/api/mcp` for the long-lived SSE stream that delivers
  server-initiated notifications (`resources/updated`,
  `resources/list_changed`).

Both wear the same `Authorization: Bearer mcp_<token>` header. Sessions
are established on the first `initialize` call and carried in the
`Mcp-Session-Id` header on every subsequent request.

### Claude Code

`~/.config/claude/mcp_servers.json` (or your project-local equivalent):

```json
{
  "mcpServers": {
    "agent-wiki": {
      "url": "https://wiki.your-org.example/api/mcp",
      "headers": {
        "Authorization": "Bearer mcp_REPLACE_ME"
      }
    }
  }
}
```

Restart Claude Code. The tools should appear under the `agent-wiki`
namespace; verify with `claude mcp list` if your build supports it.

### Cursor

`~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "agent-wiki": {
      "url": "https://wiki.your-org.example/api/mcp",
      "headers": {
        "Authorization": "Bearer mcp_REPLACE_ME"
      }
    }
  }
}
```

Reload the editor. The `agent-wiki` server should show up in Cursor's
MCP panel; toggle the tools you want available to the assistant.

### Codex CLI

```toml
# ~/.codex/config.toml
[mcp_servers.agent-wiki]
url = "https://wiki.your-org.example/api/mcp"
headers = { Authorization = "Bearer mcp_REPLACE_ME" }
```

### Generic MCP client (smoke test)

The official `mcp` Python SDK works against this server too:

```python
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async with streamablehttp_client(
    "https://wiki.your-org.example/api/mcp",
    headers={"Authorization": "Bearer mcp_REPLACE_ME"},
) as (read, write, _):
    async with ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
        print([t.name for t in tools.tools])
```

Use this when integrating a custom harness or to confirm a key works
before chasing client-config issues.

## Permissions

Every read, write, and subscribe goes through the same ACL the web UI
enforces (`app/wiki/acl.py`). An MCP token can do exactly what the
minting user can do via the UI — no more, no less.

- Search results are pre-filtered: pages the user can't read never
  appear.
- Subscribing to a page requires read access; if access is later
  revoked, in-flight notifications stop and the subscription is
  dropped automatically.
- Admins bypass page-level checks (matching the UI behavior).
- Tokens can't currently be scoped narrower than the user's
  permissions (read-only keys, path-prefix keys) — see "Open
  questions" in the design doc.

## Concurrency

Every write tool accepts an optional `base_sha` for optimistic
concurrency:

- Pass the sha you got from the matching `read_doc` call.
- If HEAD has moved since (someone else committed; an automated
  worker ran), the write returns `{"error": "stale_base", ...}`.
  Re-read with `read_doc`, re-derive the edit, retry.
- `write_doc` *requires* `base_sha` when overwriting an existing
  file — full-body writes have no fuzzy fallback to catch drift.
- `read_doc` auto-subscribes the session to push notifications, so
  if a doc is changing under you, you'll see
  `notifications/resources/updated` over the SSE stream and can
  rebase before retrying.

## Operational notes

- **Single-replica today.** The web process holds session state and
  SSE streams in memory. When this scales to multiple replicas, the
  load balancer must pin sessions to a replica via the
  `Mcp-Session-Id` header.
- **Cross-process pub-sub.** Doc commits from the worker process
  (the document-updater agent's async writes) reach SSE subscribers
  via Postgres `LISTEN/NOTIFY` — no Redis or message bus needed.
- **`update_doc_nl` debounce.** Successful same-`(user, path)`
  jobs within a 30-second window are skipped (returned with
  `committed=false reason=debounced`). Override with
  `MCP_NL_DEBOUNCE_SECONDS` if your deployment needs a different
  cadence; raise it for cost control, lower it if you have an
  agent that genuinely batches well and shouldn't be throttled.
- **Idempotency.** `update_doc_nl` collapses retries of the same
  `(user, path, instruction)` onto the same `job_id`. Pass an
  explicit `idempotency_key` if you want a different grouping.
- **No rate limits in v0.** A misbehaving agent can spam other
  tools as fast as the wiki backend can serve them. If we see
  abuse, the design doc tracks the rate-limit-table follow-up.
- **Tokens are personal.** Don't share. They inherit the minting
  user's full permissions, including admin if the user is an
  admin.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `401 missing bearer token` | The client isn't sending `Authorization: Bearer …`. Check the client config. |
| `401 invalid bearer token` | Token revoked, regenerated, or never existed. Open the Agents page and check the key list. |
| `400 missing or invalid Mcp-Session-Id` | The client didn't echo the `Mcp-Session-Id` header from the `initialize` response. Most MCP SDKs handle this automatically — if you wrote a custom client, verify it's threading the header through. |
| `400 session not initialized` | Client called a method before sending `notifications/initialized`. The SDK should send it automatically right after `initialize` returns. |
| `403 session does not belong to this bearer` | Two different tokens, one session id — looks like a hijack attempt to the server. Almost always a config bug; refresh the client. |
| `error: forbidden: read on <path>` | The token's user lacks read access to that page. Check the page's share settings via the wiki UI. |
| `error: forbidden: write on <path>` | Same, for write. |
| `error: stale_base` | Someone else committed since your last read. Re-`read_doc` and retry. The doc carries the new sha. |
| `error: base_sha_required_for_overwrite` | `write_doc` overwrite without a `base_sha`. Read the doc first, pass its sha. |
| `error: read_page` (in a write error) | Read-before-write violated. Call `read_doc` on the path before editing. Searching alone doesn't satisfy this — `search_wiki` only returns snippets. |
| Missing notifications | Check the SSE stream is open (`GET /api/mcp` with the same session id) and that the path was either auto-subscribed via `read_doc(subscribe=true)` or explicitly subscribed via `resources/subscribe`. |
| `update_doc_nl` returns `committed=false reason=debounced` | Another succeeded job for this `(user, path)` committed within 30 seconds. Either wait or batch the changes into one instruction. |

## See also

- `local_data/wiki/mcp-server/mcp-server.md` — design / architecture
  reference (request lifecycle diagrams, module layout, testing
  matrix, open questions).
- `local_data/wiki/permissions/permissions.md` — wiki-page ACL model.
- `docs/api.md` — broader HTTP API surface (login, documents, triggers,
  events, etc.).
