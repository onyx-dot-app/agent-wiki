# API surface (v0)

All routes are mounted under `/api`. Authentication is required on everything
except the inbound webhook endpoints (which use per-source signing secrets).

## Auth
| Method | Path | Purpose |
|---|---|---|
| POST   | `/api/auth/login`         | Basic auth login → session cookie |
| POST   | `/api/auth/logout`        | Clear session |
| GET    | `/api/auth/oidc/callback` | OIDC redirect handler |
| GET    | `/api/auth/me`            | Current user |

## Users
`GET /api/users`, `POST /api/users`, `GET /api/users/:id`

## MCP — inbound server (external coding agents → wiki)

Streamable HTTP transport, bearer auth. See `docs/mcp-server.md` for
the user-facing setup guide and
`local_data/wiki/mcp-server/mcp-server.md` for the design.

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST   | `/api/mcp`                | Bearer | JSON-RPC 2.0 request/response (initialize, tools/list, tools/call, resources/*, ping). |
| GET    | `/api/mcp`                | Bearer | Long-lived SSE stream — server-initiated `notifications/resources/updated` and `…/list_changed` frames. |
| GET    | `/api/mcp/tokens`         | Cookie | Current user's personal API tokens (no hashes). |
| POST   | `/api/mcp/tokens`         | Cookie | Mint a new token; raw value returned exactly once. |
| DELETE | `/api/mcp/tokens/:id`     | Cookie | Revoke. |

## MCP — outbound connections (wiki harness → external MCP servers)

`GET /api/mcp/connections`, `POST /api/mcp/connections`,
`DELETE /api/mcp/connections/:id` — manages the in-process agent
harness's *use* of *other* MCP servers. Distinct from the inbound
surface above.

## Documents
| Method | Path | Notes |
|---|---|---|
| GET    | `/api/documents`                | list, optional `?prefix=` |
| GET    | `/api/documents/search`         | BM25 over `pg_textsearch` |
| GET    | `/api/documents/:id`            | read latest body |
| PUT    | `/api/documents/:id`            | agent direct edit → commits + reindex |
| POST   | `/api/documents/ingest`         | generic update payload → LLM agent reconciles |
| GET    | `/api/documents/:id/history`    | git log for the doc path |

## Triggers
| Method | Path | Notes |
|---|---|---|
| GET    | `/api/triggers`             | list, scoped to current user |
| POST   | `/api/triggers`             | create — body: `{scope_path, kind, nl_description, action, schedule_cron?}` |
| PUT    | `/api/triggers/:id`         | edit |
| DELETE | `/api/triggers/:id`         | delete |
| GET    | `/api/triggers/:id/history` | fire history |

## Events (audit log)
`GET /api/events?kind=&since=&until=&cursor=&limit=`
`GET /api/events/:id`

## Webhooks (inbound)
`POST /api/webhooks/:source` — no session auth; verify signature.

## Chat
`POST /api/chat/messages` — `{conversation_id?, message}`
`GET /api/chat/conversations`
`GET /api/chat/conversations/:id`
