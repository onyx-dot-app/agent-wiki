# mcp_server — wiki-as-MCP-server

Exposes the agent-wiki tool surface to external coding agents (Claude Code,
Craft, etc.) over MCP. Independent of `app/api/mcp.py`, which is the
outbound (wiki-uses-MCP-clients) blueprint.

## Run it

```bash
SECRET_KEY=... \
WIKI_DIR=/path/to/wiki \
APP_DB_PATH=/path/to/app.sqlite \
QUEUE_DB_PATH=/path/to/queue.sqlite \
python -m app.mcp_server
```

The server takes over stdio for MCP JSON-RPC. Logs route to stderr.

## Register with Claude Code

```bash
cd /path/to/agent-wiki
claude mcp add agent-wiki \
  -e SECRET_KEY=dev-only-not-secret \
  -e WIKI_DIR=/Users/you/agent-wiki/local_data/wiki \
  -e APP_DB_PATH=/Users/you/agent-wiki/local_data/app.sqlite \
  -e QUEUE_DB_PATH=/Users/you/agent-wiki/local_data/queue.sqlite \
  -- /Users/you/agent-wiki/backend/.venv/bin/python -m app.mcp_server
```

`claude mcp list` should show `agent-wiki: ✓ Connected`.

## D2 tools (4)

`search_wiki`, `read_page`, `edit_doc`, `write_doc`. Specs and handlers live
in `app/llm/agents/tools/` — the MCP server reuses them verbatim. The same
read-before-write enforcement (`seen_doc_paths` ContextVar, set once per
server lifetime) gates `edit_doc` / `write_doc`.

## Auth (v0)

Stdio transport: **process-spawn = authentication.** The parent process
chooses what to launch and what env to pass. There's no in-band auth check.

Move to a real auth bridge when adding non-stdio transport (HTTP/SSE for
remote agents). Likely shape:

- v1: per-user PAT issued from the admin UI; MCP server validates header
  on each call against the same `users` table the Flask app uses.
- v2: OIDC for the same flow.
