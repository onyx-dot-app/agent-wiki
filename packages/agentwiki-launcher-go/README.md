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

## Distribution

Users download `AgentWikiLauncher.zip` from the wiki UI
(`/api/installer/app`), unzip, and drag `AgentWikiLauncher.app` into
`/Applications`. The .app is a Developer-ID-signed + notarized + stapled
bundle so Gatekeeper passes on first open without "Open anyway"
friction. On the first `agentwiki://run` URI the bundle prompts to pin
the wiki endpoint, then dispatches every subsequent run silently.

### Signing + notarization

`./scripts/build-app.sh` does the full chain:

1. `make dist` — cross-compile arm64 + amd64 Mach-O binaries.
2. `lipo -create` — combine into one universal binary.
3. `osacompile` an AppleScript stub bundle, drop the universal binary in
   `Contents/Resources/`, patch `Info.plist` with `CFBundleURLTypes`
   for the `agentwiki://` scheme + `LSUIElement`.
4. `codesign --options runtime --timestamp` the inner binary, the
   applet, and the bundle in that order.
5. `xcrun notarytool submit --wait` against an ad-hoc keychain.
6. `xcrun stapler staple` the bundle so notarization is offline-checkable.
7. Re-zip post-staple → `dist/AgentWikiLauncher.zip`.

Required env (read by `scripts/build-app.sh` + `scripts/release-mac.sh`):

| Variable              | Purpose                                        |
| --------------------- | ---------------------------------------------- |
| `APPLE_ID`            | Apple developer account email                  |
| `APPLE_TEAM_ID`       | Apple team ID                                  |
| `APPLE_APP_PASSWORD`  | App-specific password for `notarytool`         |
| `APPLE_CERT_BASE64`   | Base64-encoded Developer ID Application `.p12` |
| `APPLE_CERT_PASSWORD` | Password for the `.p12`                        |

Locally (pulls the five values from AWS Secrets Manager `deploy/apple-*`):

```
source scripts/load-secrets-aws.sh
./scripts/build-app.sh
```

In CI, `.github/workflows/release-agentwiki-launcher-go.yml` runs the
same script. It assumes the `AWS_OIDC_ROLE_ARN` IAM role via OIDC and
pulls the same secrets from AWS Secrets Manager — secrets never copy
into GitHub Actions secrets. `docker-build-push.yml` + `nightly-build.yml`
call this workflow as a reusable sub-workflow, download the
`AgentWikiLauncher.zip` artifact, and bake it into the backend image at
`backend/static/installers/` so `/api/installer/app` serves it in
production.
