# agentwiki-launcher (Go)

Cross-platform helper (macOS / Linux / Windows) for the agent-wiki
"Run Agent" flow. Owns the `agentwiki://` URL scheme, exchanges
launch codes with the wiki backend, materialises MCP config, spawns
Claude Code / Codex in a terminal.

Functional twin of the Node TypeScript helper in
`../agentwiki-launcher/` — ported to Go so distribution is a single
static binary instead of `npm install -g …` + Node runtime.

## Build

```
make build              # local arch
make dist               # darwin-arm64 + darwin-amd64
make release            # macOS — signed/notarized/stapled .app zip
make release-linux      # linux-amd64 + linux-arm64 tar.gz
make release-windows    # windows-amd64 zip
```

## Install (dev)

```
make build
./agentwiki-launcher set-endpoint http://127.0.0.1:8088
./agentwiki-launcher install   # registers agentwiki:// for the host OS
```

`install` branches by GOOS at build time:

- **macOS** — osacompile a stub `.app` into `~/Applications/AgentWiki.app`,
  patch `Info.plist` with `CFBundleURLTypes`, register with
  LaunchServices.
- **Linux** — write
  `~/.local/share/applications/agentwiki-launcher.desktop` with
  `MimeType=x-scheme-handler/agentwiki;` and run `xdg-mime default`
  - `update-desktop-database`.
- **Windows** — `reg add` `HKCU\Software\Classes\agentwiki` URL Protocol
  scaffolding + `\shell\open\command` pointing at the launcher .exe.

## Subcommands

| Subcommand                          | Purpose                                                    |
| ----------------------------------- | ---------------------------------------------------------- |
| `set-endpoint <wiki-url>`           | Pin the wiki backend URL (required once).                  |
| `run <agentwiki://run?...>`         | Process a launch URI.                                      |
| `probe-ack <agentwiki://probe?...>` | Ack the helper-detection probe.                            |
| `dispatch <agentwiki://...>`        | Route by URI action. The .app/registry/.desktop call this. |
| `install`                           | (Re)register the `agentwiki://` URL handler for this OS.   |

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

The wiki FE detects the user's OS and links to the matching backend
route. On first `agentwiki://run` URI the launcher prompts to pin the
wiki endpoint (`osascript` on mac, `zenity`/`kdialog` on Linux, `mshta`
MessageBox on Windows). Every subsequent run dispatches silently.

| OS      | Backend route                        | Artifact                                 | User action                                                                                          |
| ------- | ------------------------------------ | ---------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| macOS   | `/api/installer/mac`                 | `AgentWikiLauncher.zip`                  | Unzip, drag `AgentWikiLauncher.app` into `/Applications`. Signed + notarized → Gatekeeper silent.    |
| Linux   | `/api/installer/linux` (AppImage)    | `AgentWikiLauncher-x86_64.AppImage`      | `chmod +x` once, double-click to register the URL handler.                                           |
| Linux   | `/api/installer/linux?format=tar.gz` | `agentwiki-launcher-linux-<arch>.tar.gz` | Fallback for arm64 / non-AppImage workflows. Extract, run `./install.sh`.                            |
| Windows | `/api/installer/windows`             | `agentwiki-launcher-windows-amd64.exe`   | Double-click .exe. SmartScreen → "More info" → "Run anyway" (unsigned). MessageBox confirms install. |

`/api/installer/app` stays as a back-compat alias for the macOS bundle
so older frontend builds keep working.

The Windows .exe is built with `-ldflags="-H windowsgui"` so URL handler
dispatches are silent (no console flash on every `agentwiki://` click).
Errors get teed to `~/.agentwiki/stub.log` so silent failures are
diagnosable.

### macOS signing + notarization

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

Required env (read by `scripts/build-app.sh`):

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

In CI, `.github/workflows/release-agentwiki-launcher-go.yml` has three
parallel jobs:

- `darwin` (macos-14) — signs + notarizes via the AWS-Secrets-Manager
  OIDC flow. Apple secrets never copy into GitHub Actions secrets.
- `linux` (ubuntu-latest) — `./scripts/build-linux.sh`, no secrets.
- `windows` (ubuntu-latest) — `./scripts/build-windows.sh`, no secrets.

`docker-build-push.yml` + `nightly-build.yml` call this workflow as a
reusable sub-workflow, download all three artifacts, and bake them into
the backend image at `backend/static/installers/` so
`/api/installer/{mac,linux,windows}` serve them in production.

### Linux + Windows packaging

`scripts/build-linux.sh` cross-compiles amd64 + arm64 (`GOOS=linux
GOARCH=…`), wraps each binary in a tarball alongside an `install.sh`
that drops the binary into `~/.local/bin` and runs `agentwiki-launcher
install` (which writes the `.desktop` file + runs `xdg-mime`).

`scripts/build-windows.sh` cross-compiles `GOOS=windows GOARCH=amd64`,
zips it alongside an `install.bat` that copies the .exe into
`%LOCALAPPDATA%\AgentWikiLauncher` and runs `agentwiki-launcher.exe
install` (which writes the `HKCU\Software\Classes\agentwiki` URL handler
keys). No Authenticode signing yet — SmartScreen will warn on first run
until we set that up.
