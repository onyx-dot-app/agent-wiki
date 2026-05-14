# agentwiki-launcher (Go)

macOS helper for the agent-wiki "Run Agent" flow. Owns the
`agentwiki://` URL scheme, exchanges launch codes with the wiki
backend, materialises MCP config, spawns Claude Code / Codex in
Terminal.app.

Functional twin of the Node TypeScript helper in
`../agentwiki-launcher/` — ported to Go so distribution is a single
static binary instead of `npm install -g …` + Node runtime.

## Build

```
make build              # local arch
make dist               # darwin-arm64 + darwin-amd64
```

## Install (dev)

```
make build
./agentwiki-launcher set-endpoint http://127.0.0.1:8088
./agentwiki-launcher install
```

## Subcommands

| Subcommand                          | Purpose                                                   |
| ----------------------------------- | --------------------------------------------------------- |
| `set-endpoint <wiki-url>`           | Pin the wiki backend URL (required once).                 |
| `run <agentwiki://run?...>`         | Process a launch URI.                                     |
| `probe-ack <agentwiki://probe?...>` | Ack the helper-detection probe.                           |
| `dispatch <agentwiki://...>`        | Route by URI action. The .app stub calls this.            |
| `install`                           | (Re)install `~/Applications/AgentWiki.app` + URL handler. |

## Layout

```
cmd/agentwiki-launcher/        — main, subcommand dispatch
internal/
  allowed/                     — RCE allow-list (claude, codex)
  endpoint/                    — pin / verify ~/.agentwiki/endpoint.url
  exchange/                    — POST /api/launch/exchange
  install/                     — osacompile .app + lsregister
  interpolate/                 — ${var} substitution
  machine/                     — stable per-machine UUID
  manifest/                    — pydantic-mirror DSL validator
  mcpconfig/                   — render claude JSON / codex TOML
  spawn/                       — assemble spawn command
  terminal/                    — write .command wrapper, open -a Terminal.app
  tmpfile/                     — secure tmpfile writer
  trust/                       — claude.json + codex config.toml writers
  uri/                         — parse agentwiki:// URIs
```

## Distribution (planned)

Homebrew tap (`onyx-dot-app/homebrew-wiki`) → `brew install onyx/wiki/agentwiki-launcher` → binary lands at `/opt/homebrew/bin/agentwiki-launcher`. Post-install runs `agentwiki-launcher install` to register the URL scheme. Signed with Developer ID + notarized via xcrun notarytool.
