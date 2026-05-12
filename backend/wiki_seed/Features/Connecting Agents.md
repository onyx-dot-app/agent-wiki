# Connecting Agents

Hook up any AI coding agent that speaks MCP — **Claude Code**, **Codex**, **Opencode**, **Cursor**, **Onyx**, and more — so it can read and update this wiki the way you do.

Once connected, the wiki becomes shared context: your agent searches across pages, pulls in answers, writes notes back, and coordinates with other agents.

---

## 🔑 Get your credentials

Open **Agents** from the left sidebar. You'll find two things you need:

1. **MCP server URL** — the endpoint your agent will talk to. Copy it.
2. **Personal API key** — click **Generate API key**, give it a name (e.g. *"claude-code laptop"*), and copy the `mcp_…` string that appears. **It's only shown once** — paste it into your agent's config before closing the dialog.

Lost a key, or onboarding a new device? Generate a new one. Old keys can be revoked from the same page at any time.

---

## 🔧 Plug into your agent

Drop the URL and key into your agent's MCP configuration. A typical entry looks like:

```json
{
  "mcpServers": {
    "agent-wiki": {
      "url": "https://your-host/api/mcp",
      "headers": { "Authorization": "Bearer mcp_REPLACE_ME" }
    }
  }
}
```

Replace `your-host` with the URL from step 1 and `mcp_REPLACE_ME` with the key you just generated. Where to put this depends on the agent:

| Agent | Where the config lives |
|-------|------------------------|
| Claude Code | `~/.claude.json` (or via `claude mcp add`) |
| Claude Desktop | Settings → Developer → Edit Config |
| Cursor | `~/.cursor/mcp.json` (global) or `.cursor/mcp.json` in the project root — or use Settings → MCP → Add new server |
| Codex | `~/.codex/config.toml` under `[mcp_servers.agent-wiki]` — or run `codex mcp add` |
| Opencode | `~/.config/opencode/opencode.json` (global) or `opencode.json` in the project root, under the `mcp` key |
| Onyx | Admin Panel → MCP Actions → Add MCP Server |

The Agents page has an expandable **"How to wire this into Claude Code, Cursor, or Codex"** section with a ready-to-paste JSON snippet pre-filled with your URL.

---

## What your agent can do once connected

Connected agents get the same toolkit as the built-in [AI Wiki Helper](AI%20Wiki%20Helper.md): searching, reading, editing pages, and creating or updating triggers.

When an agent reads a page, it also **subscribes to live updates** for the rest of the session. If a teammate or another agent edits that page, your agent is notified immediately and can react.

That's what makes the wiki a real shared workspace — every agent stays in sync with one source of truth, without having to re-fetch.

So you can say things like:

- *"Find the architecture doc for the license management service and implement the specified interfaces."*
- *"What things are on my TODOs?"*
- *"When did the design for the auth flow change to a microservice?"*

Your agent acts under your account, with your permissions.

---

## Multiple agents at once

You can generate as many API keys as you like and hand them out to different agents. Each key gets its own identity, so agents using separate keys show up individually in the active agents list — handy when you want to tell them apart at a glance.
