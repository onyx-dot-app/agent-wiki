# Phase 3 — Mac Helper + Claude Code Verified

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ship `@agentwiki/launcher@0.1.0-alpha` on npm with verified Mac + Claude Code support. After this phase: clicking Run Agent in the wiki opens Terminal.app with `claude` running, MCP wired, doc edits stream back to the wiki UI live. Codex and Linux/Windows land in Phase 4.

**Architecture:** Node CLI (TypeScript, built to single bundled JS via esbuild) that registers `agentwiki://` URI scheme on macOS via a `.app` bundle in `~/Applications` whose `Info.plist` declares the scheme and whose binary just `exec`s `agentwiki-launcher run` with the URI. The CLI has these hard-coded modules: manifest validator (mirror of backend pydantic), bounded interpolator, hardcoded binary allow-list, machine_id persistence, secure tmpfile helper, MCP-config writers, prompt-delivery, spawn, file_watch capture, exchange client, localhost probe server. **Real verification against `claude --help` happens in this phase** — if a flag we guessed wrong (e.g. `--mcp-config`, `--prompt-file`, `--resume`), the manifest gets patched and committed.

**Tech Stack:** Node 18+, TypeScript 5, esbuild, `node:test` for unit tests, real `claude` CLI for verification.

**Reference:** [../design.md](../design.md) sections "New repo" + "Security model" + "Manifest DSL". Phase 1 already shipped the backend contract; Phase 2 already ships the frontend. This phase makes the URI dispatch actually do something.

---

## Audit fixes — apply during task execution

### AF#4 — Random helper port + machine_id on probe-ack (audit critical)

**Affects: Task 1 (package layout adds `probe_server.ts`) + Task 12 (`handleProbeAck`).**

The plan hardcodes `helper_port: 31415`. That collides on multi-user machines and lets a malicious local process bind first to impersonate the helper. Fixes:

1. **Random ephemeral port at helper startup.** Bind `0` (kernel-assigned), then `address.port`. Persist to `~/.agentwiki/launcher.port` (mode 0600) so subsequent invocations of the same helper instance reuse the port:

   ```typescript
   // src/probe_server.ts (new module)
   import { createServer } from "node:http";
   import { writeFileSync } from "node:fs";
   import { join } from "node:path";
   import { homedir } from "node:os";

   export async function startProbeServer(): Promise<number> {
     const server = createServer(/* /probe-cli handler */);
     await new Promise<void>((resolve) =>
       server.listen(0, "127.0.0.1", resolve),
     );
     const port = (server.address() as { port: number }).port;
     writeFileSync(
       join(homedir(), ".agentwiki", "launcher.port"),
       String(port),
       { mode: 0o600 },
     );
     return port;
   }
   ```

2. **`handleProbeAck` reads the live port, NOT a constant.** Update `src/cli.ts`:

   ```typescript
   async function handleProbeAck(uri: string): Promise<void> {
     const parsed = parseLaunchUri(uri);
     if (parsed.action !== "probe") throw new Error("expected probe action");
     const port = await startProbeServer();
     await fetch(new URL("/api/launch/probe-ack", parsed.endpoint).toString(), {
       method: "POST",
       headers: { "Content-Type": "application/json" },
       body: JSON.stringify({
         nonce: parsed.nonce,
         helper_port: port,
         machine_id: getOrCreateMachineId(), // AF#14 — frontend uses this for workdir defaulting
       }),
     });
   }
   ```

3. **Helper instance lifecycle.** The probe-ack handler is a short-lived process invoked by the OS URI router; the probe server it starts dies when that process exits. Solution: spawn a detached daemon (`spawn(node, ['serve-probe-port'], { detached: true })`) once on first probe-ack, persist its PID + port, reuse via the `.port` file on subsequent calls.

Add `probe_server.test.ts` — assert random port in valid range, file written 0600.

### AF#6 — Verify `dirhash` algorithm against real Claude Code (audit critical)

**Affects: Task 14 (CLI verification).**

The plan's `spawn.ts` computes `dirhash = sha256(cwd)[:16]`. Claude Code uses ITS OWN dirhash to name `~/.claude/projects/<dirhash>/`. If they differ, `file_watch` reads the wrong directory and `cli_session_id` is never captured. Resume permanently broken.

**Add a sub-step to Task 14.1 — Step 14.1b: Empirically verify the dirhash algorithm.**

```bash
# 1. Pick a known directory.
TEST_DIR="/tmp/agentwiki-dirhash-test"
mkdir -p "$TEST_DIR"
cd "$TEST_DIR"

# 2. Start a quick claude session.
claude -p "say hi" 2>&1 | head -5  # or: claude, then immediately exit
# 3. Check what dir name claude actually used.
ls -la ~/.claude/projects/
# Look for the most recently created dir. Note its name.

# 4. Compare to our computed hash:
python3 -c "import hashlib; print(hashlib.sha256('$TEST_DIR'.encode()).hexdigest()[:16])"

# If they don't match — try common alternatives:
#   - full sha256 (no truncation)
#   - md5
#   - Claude's project-name slugification (replaces / with -, etc.)
# Walk through ~/.claude/projects/ entries on your machine vs known cwds
# to reverse-engineer.
```

If the algorithm differs from `sha256(cwd)[:16]`, patch `src/spawn.ts:buildSpawnCommand`'s `dirhash` computation to match. Then patch the manifest's `session_id_capture.path` if needed.

Don't ship this phase until the test directory's `cli_session_id` is captured correctly end-to-end.

### R2#1 — Pin endpoint at install time, ignore URI's `endpoint` param (round-2 critical)

**Affects: Task 9 (`exchange.ts`) + Task 12 (`handleRun`/`handleProbeAck`) + Task 13 (postinstall + new `set-endpoint` subcommand).**

The `agentwiki://run?...&endpoint=...` URI is attacker-craftable. If helper trusts URI's endpoint, attacker captures `machine_id` and returns a forged manifest with attacker's `mcp_token` → full session hijack.

Patches:

1. **New CLI subcommand `set-endpoint`** writes to `~/.agentwiki/endpoint.url` (mode 0600):

   ```typescript
   async function handleSetEndpoint(url: string): Promise<void> {
     new URL(url); // validate
     const path = join(homedir(), ".agentwiki", "endpoint.url");
     mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
     writeFileSync(path, url, { mode: 0o600 });
   }
   ```

2. **`handleRun` reads pinned endpoint** instead of URI's:

   ```typescript
   const pinnedPath = join(homedir(), ".agentwiki", "endpoint.url");
   if (!existsSync(pinnedPath)) {
     console.error(
       "agentwiki-launcher not configured — run `agentwiki-launcher set-endpoint <url>` first.",
     );
     process.exit(2);
   }
   const pinned = readFileSync(pinnedPath, "utf-8").trim();
   const parsed = parseLaunchUri(uri);
   if (parsed.endpoint !== pinned) {
     console.error(
       `URI endpoint ${parsed.endpoint} does not match pinned ${pinned}; refusing.`,
     );
     process.exit(2);
   }
   const exchanged = await exchange(pinned, parsed.code, machineId); // use pinned, not parsed
   ```

3. **Probe-ack also uses pinned endpoint** (same pattern).

4. **`/agents` page documents `set-endpoint`** as a one-time step after install.

Add `endpoint_pinning.test.ts` — assert helper refuses to run when URI endpoint differs from pinned.

### R2#3 — Round 4 placeholder scaffold (round-2 critical — Phase 4 issue, called out here)

**Affects: Phase 4 Task 6 (cross-OS plan).**

`e2e_mocked.test.ts` ends with `assert.ok(true, "scaffold — fill in once symlink setup works")`. That's a placeholder. Fix Phase 4 Task 6 to flesh out the symlink-PATH + spawn + assertion logic; see the Phase 4 plan's audit-fix section.

### R8#2 — `npm publish --provenance` (round-8 medium)

**Affects: Task 16.2 (publish step).**

```bash
npm publish --access public --tag alpha --provenance
```

Provenance attestation ties the published artifact to a specific GitHub Actions workflow run. Closes supply-chain trust gap.

### R8#3 — Exact-pin dependencies (round-8 medium)

**Affects: Task 1.1 (`package.json`).**

Replace `"^8.12.0"` with `"8.12.0"`. Any version bumps go through Renovate / Dependabot with review. Otherwise a transitively-pulled compromised version on `npm install` runs arbitrary code via the postinstall scripts of dependencies.

### R9#1 helper-side beacon (round-9 high)

**Affects: Task 12 (`handleRun`).**

Immediately after `openInTerminalApp(...)` returns (which is after `osascript` exits, NOT after `claude` starts — but it's the best we can do from the helper):

```typescript
await postSpawnOk(
  exchanged.endpoint,
  exchanged.payload.session_id,
  exchanged.mcp_token,
);
```

Where `postSpawnOk` POSTs `/api/agent-sessions/:id/spawn-ok` with the bearer. Backend (Phase 1 R9#1) stamps `spawn_ok_at`. If `osascript` exits successfully, the wrapper script will run claude shortly; backend's 30s sweep catches the case where it doesn't.

### R10#1 — Wrapper script tests (round-10 medium)

**Affects: Task 11.**

Add `terminal/darwin.test.ts`:

```typescript
test("wrapper script contains trap EXIT line and full cleanup path list", () => {
  // Construct openInTerminalApp inputs, intercept the wrapper write,
  // read its contents, assert the trap line + each tmpfile path appears.
});

test("wrapper script env exports match command.env exactly", () => {
  // Spawn cmd has env { A: 'a', B: 'b' } → wrapper has `export A='a'\nexport B='b'`.
});
```

### AF#8 — Wrapper script for tmpfile lifetime (audit high)

**Affects: Task 11 (terminal/darwin.ts) + Task 12 (`handleRun`).**

Current plan: open Terminal.app, sleep 1500ms, unlink tmpfiles. On a slow Mac (Spotlight indexing, cold launch), Terminal.app may not have invoked `claude` yet → `claude --mcp-config <gone>` fails.

Patch: write a self-cleaning wrapper script that holds the tmpfile lifetime until the CLI exits.

```typescript
// src/terminal/darwin.ts — replace openInTerminalApp
import { execFile } from "node:child_process";
import { writeFileSync, chmodSync } from "node:fs";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomBytes } from "node:crypto";

export function openInTerminalApp(opts: {
  binary: string;
  argv: string[];
  env: Record<string, string>;
  cwd: string;
  tmpfilesToClean: string[]; // paths the wrapper should unlink on exit
}): void {
  const dir = mkdtempSync(join(tmpdir(), "agw-wrap-"));
  const wrapper = join(dir, "run.sh");
  const envExports = Object.entries(opts.env)
    .map(([k, v]) => `export ${k}=${JSON.stringify(v)}`)
    .join("\n");
  const argvQuoted = opts.argv.map((a) => JSON.stringify(a)).join(" ");
  const cleanList = opts.tmpfilesToClean
    .concat([wrapper, dir])
    .map((p) => JSON.stringify(p))
    .join(" ");

  writeFileSync(
    wrapper,
    `#!/bin/bash
set -e
trap 'rm -rf ${cleanList}' EXIT
cd ${JSON.stringify(opts.cwd)}
${envExports}
exec ${opts.binary} ${argvQuoted}
`,
  );
  chmodSync(wrapper, 0o700);

  const osascript = `tell application "Terminal" to do script "${wrapper.replace(
    /"/g,
    '\\"',
  )}"`;
  execFile("osascript", ["-e", osascript], { stdio: "ignore" });
}
```

And patch `handleRun` (Task 12.1) to:

1. Move `withSecureTmpfiles` to write tmpfiles WITHOUT auto-cleanup (raw `writeSecureTmpfile` returns).
2. Hand the paths to `openInTerminalApp` via `tmpfilesToClean`.
3. Helper process exits immediately (no `await new Promise(setTimeout 1500)`).

The wrapper script's `trap EXIT` cleans up when `claude` exits, regardless of how long the user keeps the session open. Tests in `terminal/darwin.test.ts` (mocked) assert the wrapper contains the expected `trap` line + path list.

---

## Pre-flight

- [ ] **Step 0.1: Install Claude Code locally + verify available**

```bash
which claude
claude --version
```

If `claude` is missing, install per https://docs.claude.com/code/install. Phase 3 cannot complete without it.

- [ ] **Step 0.2: Inspect `claude --help` output**

```bash
claude --help 2>&1 | head -80
```

**Write down** the actual flag surface:

- Does `--mcp-config <path>` exist? Or is it `--mcp-server-config`? Or via env?
- Does `--resume <id>` exist? Or `--continue`?
- Does `--prompt-file <path>` exist? Or only positional prompt? Or stdin?
- Does the CLI write a session jsonl to `~/.claude/projects/<dirhash>/<id>.jsonl`? Verify with `ls ~/.claude/projects/` after one session.

The manifest in `backend/app/launchers/manifests/claude_code.json` was guessed; this phase aligns it with reality and ships a backend patch if needed.

- [ ] **Step 0.3: Branch off main**

```bash
cd /Users/nikolas/agent-wiki && nb feat/coding-tool-launchers-helper-mac
```

(Or continue on the same `feat/coding-tool-launchers` branch — discuss with reviewer.)

- [ ] **Step 0.4: Confirm Phase 1 + 2 merged**

```bash
git log --oneline main..HEAD | head
```

---

## File Structure

```
packages/agentwiki-launcher/                   (new workspace)
  package.json
  tsconfig.json
  esbuild.config.mjs
  bin/agentwiki-launcher                       (built shim → invokes dist/index.js)
  src/
    cli.ts                                     (entry; arg parser; routes "run" / "probe-cli")
    uri.ts                                     (parses agentwiki://run?code=...&tool=...&endpoint=...)
    exchange.ts                                (POSTs /api/launch/exchange)
    manifest.ts                                (Manifest TS interface + JSON-Schema validator)
    interpolate.ts                             (bounded ${var} substitution)
    allowed_binaries.ts                        (HARDCODED allow-list)
    machine_id.ts                              (reads/creates ~/.agentwiki/machine.id)
    tmpfile.ts                                 (secure tmpfile, 0600 + finally cleanup)
    mcp_config/
      claude_json.ts                           (writes mcp.json blob)
      codex_toml.ts                            (writes codex toml blob — Phase 4 verifies)
    prompt_delivery/
      prompt_file_flag.ts                      (writes prompt tmpfile + appends flag)
      stdin.ts                                 (pipes after spawn — Phase 4 hardens)
    spawn.ts                                   (orchestrates: tmpfiles → terminal → exec)
    terminal/
      darwin.ts                                (opens Terminal.app via `open -a`)
    capture/
      file_watch.ts                            (mtime-guarded directory watch)
    probe_server.ts                            (localhost HTTP for /probe-cli)
    heartbeat.ts                               (POSTs /api/agent-sessions/:id/heartbeat every 60s)
    install/
      darwin.ts                                (creates ~/Applications/AgentWiki.app + LSSetDefaultHandler)
      postinstall.ts                           (entry called by npm postinstall — picks per-OS)
    register_helper_port.ts                    (POSTs /api/launch/probe-ack on URI handler invocation)
  test/
    manifest.test.ts
    interpolate.test.ts
    allowed_binaries.test.ts
    uri.test.ts
    tmpfile.test.ts
    file_watch.test.ts
    mcp_config_writer.test.ts
    prompt_delivery.test.ts
  README.md
```

---

## Task 1: Bootstrap the npm workspace

**Files:**

- Create: `packages/agentwiki-launcher/package.json`
- Create: `packages/agentwiki-launcher/tsconfig.json`
- Create: `packages/agentwiki-launcher/esbuild.config.mjs`
- Modify: root `package.json` (if monorepo) or skip if standalone

- [ ] **Step 1.1: `package.json`**

```json
{
  "name": "@agentwiki/launcher",
  "version": "0.1.0-alpha.1",
  "description": "Local helper for the agent-wiki Run Agent button.",
  "license": "MIT",
  "bin": {
    "agentwiki-launcher": "./bin/agentwiki-launcher"
  },
  "files": ["bin", "dist"],
  "scripts": {
    "build": "node esbuild.config.mjs",
    "test": "node --test --import tsx test/**/*.test.ts",
    "typecheck": "tsc --noEmit",
    "postinstall": "node ./dist/install/postinstall.js"
  },
  "engines": { "node": ">=18" },
  "dependencies": {
    "ajv": "^8.12.0"
  },
  "devDependencies": {
    "@types/node": "^20.10.0",
    "esbuild": "^0.20.0",
    "tsx": "^4.7.0",
    "typescript": "^5.3.0"
  }
}
```

- [ ] **Step 1.2: `tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ES2022",
    "moduleResolution": "Bundler",
    "outDir": "./dist",
    "rootDir": "./src",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "declaration": false,
    "resolveJsonModule": true
  },
  "include": ["src/**/*"]
}
```

- [ ] **Step 1.3: `esbuild.config.mjs`**

```javascript
import { build } from "esbuild";
import { readdirSync } from "node:fs";

const entries = readdirSync("./src", { recursive: true })
  .filter((f) => f.endsWith(".ts"))
  .map((f) => `./src/${f}`);

await build({
  entryPoints: entries,
  bundle: false,
  outdir: "./dist",
  platform: "node",
  format: "esm",
  target: "node18",
  sourcemap: true,
});
```

- [ ] **Step 1.4: `bin/agentwiki-launcher` shim**

```bash
#!/usr/bin/env node
import('../dist/cli.js').then(m => m.main(process.argv.slice(2)));
```

Make executable: `chmod +x bin/agentwiki-launcher`.

- [ ] **Step 1.5: Verify it builds**

```bash
cd /Users/nikolas/agent-wiki/packages/agentwiki-launcher
npm install
npm run build
ls dist/
```

Expected: `dist/cli.js` (placeholder — created in Task 2).

- [ ] **Step 1.6: Commit**

```bash
git -C /Users/nikolas/agent-wiki add packages/agentwiki-launcher
git -C /Users/nikolas/agent-wiki commit -m "feat(launcher): npm workspace bootstrap"
```

---

## Task 2: `machine_id` persistence

**Files:**

- Create: `packages/agentwiki-launcher/src/machine_id.ts`
- Test: `packages/agentwiki-launcher/test/machine_id.test.ts`

- [ ] **Step 2.1: Write the failing test**

```typescript
// test/machine_id.test.ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { getOrCreateMachineId } from "../src/machine_id.js";

test("getOrCreateMachineId creates a file on first call", () => {
  const dir = mkdtempSync(join(tmpdir(), "agw-mid-"));
  try {
    const id1 = getOrCreateMachineId({ baseDir: dir });
    assert.match(id1, /^[0-9a-f-]{36}$/);
    const id2 = getOrCreateMachineId({ baseDir: dir });
    assert.equal(id1, id2);
  } finally {
    rmSync(dir, { recursive: true });
  }
});
```

Run: `npm test` → expect FAIL (module missing).

- [ ] **Step 2.2: Write**

```typescript
// src/machine_id.ts
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { randomUUID } from "node:crypto";

interface Opts {
  baseDir?: string;
}

export function getOrCreateMachineId(opts: Opts = {}): string {
  const base = opts.baseDir ?? join(homedir(), ".agentwiki");
  const path = join(base, "machine.id");
  if (existsSync(path)) {
    return readFileSync(path, "utf-8").trim();
  }
  mkdirSync(base, { recursive: true, mode: 0o700 });
  const id = randomUUID();
  writeFileSync(path, id, { mode: 0o600 });
  return id;
}
```

- [ ] **Step 2.3: Test passes; commit**

```bash
npm test
git -C /Users/nikolas/agent-wiki add packages/agentwiki-launcher/src/machine_id.ts packages/agentwiki-launcher/test/machine_id.test.ts
git -C /Users/nikolas/agent-wiki commit -m "feat(launcher): machine_id persistence"
```

---

## Task 3: Hardcoded binary allow-list

**Files:**

- Create: `packages/agentwiki-launcher/src/allowed_binaries.ts`
- Test: `packages/agentwiki-launcher/test/allowed_binaries.test.ts`

- [ ] **Step 3.1: Test first**

```typescript
// test/allowed_binaries.test.ts
import { test } from "node:test";
import assert from "node:assert/strict";

import { isAllowed, assertAllowed } from "../src/allowed_binaries.js";

test("claude is allowed", () => assert.equal(isAllowed("claude"), true));
test("codex is allowed", () => assert.equal(isAllowed("codex"), true));
test("rm is not allowed", () => assert.equal(isAllowed("rm"), false));
test("bash is not allowed", () => assert.equal(isAllowed("bash"), false));
test("absolute paths rejected", () =>
  assert.equal(isAllowed("/usr/bin/claude"), false));
test("paths with dot-dot rejected", () =>
  assert.equal(isAllowed("../claude"), false));

test("assertAllowed throws with binary_not_allowed", () => {
  assert.throws(() => assertAllowed("nope"), /binary_not_allowed/);
});
```

- [ ] **Step 3.2: Impl**

```typescript
// src/allowed_binaries.ts
/**
 * HARDCODED allow-list of binaries the helper will spawn.
 *
 * This is the defense-in-depth layer against a compromised backend
 * pushing a manifest naming `rm` / `curl` / `bash -c …`. New tools
 * land here by appending + cutting a helper release.
 *
 * Path separators are rejected — the binary must be an unqualified
 * name resolved through PATH.
 */
const ALLOWED = new Set(["claude", "codex"]);

export function isAllowed(binary: string): boolean {
  if (binary.includes("/") || binary.includes("\\")) return false;
  if (binary.includes("..")) return false;
  return ALLOWED.has(binary);
}

export function assertAllowed(binary: string): void {
  if (!isAllowed(binary)) {
    throw new Error(`binary_not_allowed: ${binary}`);
  }
}
```

- [ ] **Step 3.3: Test + commit**

```bash
npm test
git -C /Users/nikolas/agent-wiki add packages/agentwiki-launcher/src/allowed_binaries.ts packages/agentwiki-launcher/test/allowed_binaries.test.ts
git -C /Users/nikolas/agent-wiki commit -m "feat(launcher): hardcoded binary allow-list"
```

---

## Task 4: Manifest validator (TS mirror of backend pydantic)

**Files:**

- Create: `packages/agentwiki-launcher/src/manifest.ts`
- Test: `packages/agentwiki-launcher/test/manifest.test.ts`

Validate the JSON shape using Ajv + custom DSL rules (no `${token}` in argv, no `${first_turn_prompt}` anywhere).

- [ ] **Step 4.1: Test first** (full validator tests mirroring `tests/test_launchers_registry.py`)

```typescript
// test/manifest.test.ts
import { test } from "node:test";
import assert from "node:assert/strict";

import { parseManifest, ManifestError } from "../src/manifest.js";

const VALID_CLAUDE = {
  manifest_version: 1,
  id: "claude-code",
  name: "Claude Code",
  tagline: "x",
  icon_url: "/x.svg",
  kind: "local_cli",
  cli_check: {
    binary: "claude",
    version_flag: "--version",
    min_version: "1.0.0",
  },
  mcp_config_format: "claude_json",
  first_turn_prompt_delivery: {
    method: "prompt_file_flag",
    flag: "--prompt-file",
  },
  launch: {
    binary: "claude",
    argv: ["--mcp-config", "${mcp_config_path}"],
    env: { AGENTWIKI_MCP_TOKEN: "${token}" },
    cwd: "${working_dir}",
  },
};

test("valid manifest parses", () => {
  const m = parseManifest(VALID_CLAUDE);
  assert.equal(m.id, "claude-code");
});

test("unknown var rejected", () => {
  const bad = JSON.parse(JSON.stringify(VALID_CLAUDE));
  bad.launch.argv.push("${not_a_var}");
  assert.throws(() => parseManifest(bad), /unknown interpolation var/);
});

test("token in argv rejected", () => {
  const bad = JSON.parse(JSON.stringify(VALID_CLAUDE));
  bad.launch.argv.push("${token}");
  assert.throws(() => parseManifest(bad), /forbidden in argv/);
});

test("first_turn_prompt anywhere rejected", () => {
  const bad = JSON.parse(JSON.stringify(VALID_CLAUDE));
  bad.launch.argv.push("${first_turn_prompt}");
  assert.throws(() => parseManifest(bad), /first_turn_prompt forbidden/);
});

test("unknown manifest_version rejected", () => {
  const bad = JSON.parse(JSON.stringify(VALID_CLAUDE));
  bad.manifest_version = 2;
  assert.throws(() => parseManifest(bad));
});
```

- [ ] **Step 4.2: Impl**

```typescript
// src/manifest.ts
export interface Manifest {
  manifest_version: 1;
  id: string;
  name: string;
  tagline: string;
  icon_url: string;
  kind: "local_cli" | "in_app" | "web_handoff";
  cli_check?: {
    binary: string;
    version_flag: string;
    min_version?: string;
    install_hint_url?: string;
  };
  mcp_config_format?: "claude_json" | "codex_toml" | "none";
  first_turn_prompt_delivery?: {
    method: "prompt_file_flag" | "stdin" | "none";
    flag?: string;
  };
  launch?: LaunchBlock;
  resume?: LaunchBlock;
  session_id_capture?: {
    source: "file_watch" | "stdout_regex" | "none";
    path?: string;
    pattern?: string;
    extract?: string;
  };
  task_kind?: string;
}

export interface LaunchBlock {
  binary: string;
  argv: string[];
  env: Record<string, string>;
  cwd?: string;
}

const ALLOWED_VARS = new Set([
  "token",
  "endpoint",
  "session_id",
  "cli_session_id",
  "working_dir",
  "first_turn_prompt",
  "prompt_file_path",
  "mcp_config_path",
  "home",
  "dirhash",
]);

const VAR_RE = /\$\{([a-z_]+)\}/g;

export class ManifestError extends Error {}

function checkString(
  s: string,
  where: string,
  opts: { allowToken?: boolean; allowFirstTurnPrompt?: boolean },
): void {
  const found = new Set<string>();
  let m;
  VAR_RE.lastIndex = 0;
  while ((m = VAR_RE.exec(s)) !== null) found.add(m[1]);
  for (const v of found) {
    if (!ALLOWED_VARS.has(v))
      throw new ManifestError(
        `unknown interpolation var $\{${v}\} in ${where}`,
      );
  }
  if (!opts.allowToken && s.includes("${token}")) {
    throw new ManifestError(
      `$\{token\} forbidden in argv (use env AGENTWIKI_MCP_TOKEN). In ${where}.`,
    );
  }
  if (!opts.allowFirstTurnPrompt && s.includes("${first_turn_prompt}")) {
    throw new ManifestError(
      `$\{first_turn_prompt\} forbidden in ${where} — use $\{prompt_file_path\}.`,
    );
  }
}

function validateBlock(b: LaunchBlock, blockName: "launch" | "resume"): void {
  b.argv.forEach((a, i) =>
    checkString(a, `${blockName}.argv[${i}]`, {
      allowToken: false,
      allowFirstTurnPrompt: false,
    }),
  );
  for (const [k, v] of Object.entries(b.env)) {
    checkString(v, `${blockName}.env.${k}`, {
      allowToken: true,
      allowFirstTurnPrompt: false,
    });
  }
  if (b.cwd)
    checkString(b.cwd, `${blockName}.cwd`, {
      allowToken: true,
      allowFirstTurnPrompt: false,
    });
}

export function parseManifest(raw: unknown): Manifest {
  if (typeof raw !== "object" || raw === null)
    throw new ManifestError("manifest must be object");
  const m = raw as Manifest;
  if (m.manifest_version !== 1)
    throw new ManifestError(
      `unsupported manifest_version ${m.manifest_version}`,
    );
  if (m.kind === "local_cli") {
    if (!m.launch) throw new ManifestError("local_cli requires launch");
    validateBlock(m.launch, "launch");
    if (m.resume) validateBlock(m.resume, "resume");
  }
  return m;
}
```

- [ ] **Step 4.3: Test + commit**

```bash
npm test
git -C /Users/nikolas/agent-wiki add packages/agentwiki-launcher/src/manifest.ts packages/agentwiki-launcher/test/manifest.test.ts
git -C /Users/nikolas/agent-wiki commit -m "feat(launcher): manifest validator (TS)"
```

---

## Task 5: Bounded interpolator

**Files:**

- Create: `packages/agentwiki-launcher/src/interpolate.ts`
- Test: `packages/agentwiki-launcher/test/interpolate.test.ts`

- [ ] **Step 5.1: Test first**

```typescript
// test/interpolate.test.ts
import { test } from "node:test";
import assert from "node:assert/strict";
import { interpolate, InterpolateContext } from "../src/interpolate.js";

const CTX: InterpolateContext = {
  token: "mcp_xyz",
  endpoint: "https://w/api/mcp",
  session_id: "as_1",
  cli_session_id: null,
  working_dir: "/home/u/p",
  prompt_file_path: "/tmp/p.txt",
  mcp_config_path: "/tmp/c.json",
  home: "/home/u",
  dirhash: "abc123",
};

test("substitutes a single var", () =>
  assert.equal(interpolate("${endpoint}", CTX), "https://w/api/mcp"));
test("substitutes multiple vars", () =>
  assert.equal(interpolate("${home}/x/${dirhash}", CTX), "/home/u/x/abc123"));
test("unknown var throws", () =>
  assert.throws(() => interpolate("${not_a_var}", CTX), /unknown var/));
test("first_turn_prompt is not substituted directly (helper materializes to file)", () => {
  // first_turn_prompt is not part of the context — its value is always
  // wrapped via prompt_file_path in argv. If something tries to interp
  // it, the var simply isn't in the context → "unknown var".
  assert.throws(() => interpolate("${first_turn_prompt}", CTX));
});
```

- [ ] **Step 5.2: Impl**

```typescript
// src/interpolate.ts
export interface InterpolateContext {
  token: string;
  endpoint: string;
  session_id: string;
  cli_session_id: string | null;
  working_dir: string | null;
  prompt_file_path: string | null;
  mcp_config_path: string | null;
  home: string;
  dirhash: string;
}

const RE = /\$\{([a-z_]+)\}/g;

export function interpolate(template: string, ctx: InterpolateContext): string {
  return template.replace(RE, (_, name: string) => {
    const value = (ctx as Record<string, string | null>)[name];
    if (value === undefined) throw new Error(`unknown var $\{${name}\}`);
    if (value === null)
      throw new Error(
        `var $\{${name}\} unset in this context (e.g. resume vs first-launch)`,
      );
    return value;
  });
}

export function interpolateArgv(
  argv: string[],
  ctx: InterpolateContext,
): string[] {
  return argv.map((a) => interpolate(a, ctx));
}

export function interpolateEnv(
  env: Record<string, string>,
  ctx: InterpolateContext,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(env)) out[k] = interpolate(v, ctx);
  return out;
}
```

- [ ] **Step 5.3: Test + commit**

```bash
npm test
git -C /Users/nikolas/agent-wiki add packages/agentwiki-launcher/src/interpolate.ts packages/agentwiki-launcher/test/interpolate.test.ts
git -C /Users/nikolas/agent-wiki commit -m "feat(launcher): bounded interpolator"
```

---

## Task 6: Secure tmpfile helper

**Files:**

- Create: `packages/agentwiki-launcher/src/tmpfile.ts`
- Test: `packages/agentwiki-launcher/test/tmpfile.test.ts`

- [ ] **Step 6.1: Test first**

```typescript
import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync, statSync } from "node:fs";

import { writeSecureTmpfile, withSecureTmpfiles } from "../src/tmpfile.js";

test("writeSecureTmpfile produces 0600 file", () => {
  const path = writeSecureTmpfile("data");
  try {
    const m = statSync(path).mode & 0o777;
    assert.equal(m, 0o600);
    assert.equal(readFileSync(path, "utf-8"), "data");
  } finally {
    require("node:fs").unlinkSync(path);
  }
});

test("withSecureTmpfiles unlinks on success", async () => {
  let captured = "";
  const result = await withSecureTmpfiles(
    { a: "alpha", b: "beta" },
    async (paths) => {
      captured = paths.a;
      assert.ok(existsSync(paths.a) && existsSync(paths.b));
      return "ok";
    },
  );
  assert.equal(result, "ok");
  assert.ok(!existsSync(captured));
});

test("withSecureTmpfiles unlinks on throw", async () => {
  let captured = "";
  await assert.rejects(async () => {
    await withSecureTmpfiles({ a: "x" }, async (paths) => {
      captured = paths.a;
      throw new Error("boom");
    });
  }, /boom/);
  assert.ok(!existsSync(captured));
});
```

- [ ] **Step 6.2: Impl**

```typescript
// src/tmpfile.ts
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomBytes } from "node:crypto";

export function writeSecureTmpfile(data: string, suffix = ""): string {
  const dir = mkdtempSync(join(tmpdir(), "agw-"));
  const path = join(dir, randomBytes(8).toString("hex") + suffix);
  writeFileSync(path, data, { mode: 0o600 });
  return path;
}

export async function withSecureTmpfiles<K extends string, R>(
  files: Record<K, string>,
  fn: (paths: Record<K, string>) => Promise<R> | R,
): Promise<R> {
  const paths = {} as Record<K, string>;
  const dirs: string[] = [];
  for (const [k, data] of Object.entries(files) as [K, string][]) {
    const p = writeSecureTmpfile(data);
    paths[k] = p;
    dirs.push(p.replace(/\/[^/]+$/, ""));
  }
  try {
    return await fn(paths);
  } finally {
    for (const d of dirs) {
      try {
        rmSync(d, { recursive: true, force: true });
      } catch {}
    }
  }
}
```

- [ ] **Step 6.3: Test + commit**

```bash
npm test
git -C /Users/nikolas/agent-wiki add packages/agentwiki-launcher/src/tmpfile.ts packages/agentwiki-launcher/test/tmpfile.test.ts
git -C /Users/nikolas/agent-wiki commit -m "feat(launcher): secure tmpfile + cleanup contract"
```

---

## Task 7: MCP config writers

**Files:**

- Create: `packages/agentwiki-launcher/src/mcp_config/claude_json.ts`
- Create: `packages/agentwiki-launcher/src/mcp_config/codex_toml.ts`
- Test: `packages/agentwiki-launcher/test/mcp_config_writer.test.ts`

- [ ] **Step 7.1: Test first** (assertions on byte-exact output)

```typescript
import { test } from "node:test";
import assert from "node:assert/strict";

import { renderClaudeJson } from "../src/mcp_config/claude_json.js";
import { renderCodexToml } from "../src/mcp_config/codex_toml.js";

test("claude_json shape", () => {
  const s = renderClaudeJson({ url: "https://w/api/mcp", token: "mcp_xyz" });
  const parsed = JSON.parse(s);
  assert.deepEqual(parsed, {
    mcpServers: {
      "agent-wiki": {
        url: "https://w/api/mcp",
        headers: { Authorization: "Bearer mcp_xyz" },
      },
    },
  });
});

test("codex_toml shape", () => {
  const s = renderCodexToml({ url: "https://w/api/mcp", token: "mcp_xyz" });
  assert.match(s, /\[mcp_servers\.agent-wiki\]/);
  assert.match(s, /url = "https:\/\/w\/api\/mcp"/);
  assert.match(s, /Authorization = "Bearer mcp_xyz"/);
});
```

- [ ] **Step 7.2: Impl**

```typescript
// src/mcp_config/claude_json.ts
export function renderClaudeJson(opts: { url: string; token: string }): string {
  return JSON.stringify(
    {
      mcpServers: {
        "agent-wiki": {
          url: opts.url,
          headers: { Authorization: `Bearer ${opts.token}` },
        },
      },
    },
    null,
    2,
  );
}
```

```typescript
// src/mcp_config/codex_toml.ts
export function renderCodexToml(opts: { url: string; token: string }): string {
  return [
    `[mcp_servers.agent-wiki]`,
    `url = "${opts.url}"`,
    `[mcp_servers.agent-wiki.headers]`,
    `Authorization = "Bearer ${opts.token}"`,
    ``,
  ].join("\n");
}
```

- [ ] **Step 7.3: Test + commit**

---

## Task 8: URI parser

**Files:**

- Create: `packages/agentwiki-launcher/src/uri.ts`
- Test: `packages/agentwiki-launcher/test/uri.test.ts`

- [ ] **Step 8.1: Test**

```typescript
import { test } from "node:test";
import assert from "node:assert/strict";
import { parseLaunchUri } from "../src/uri.js";

test("parses run URI", () => {
  const r = parseLaunchUri(
    "agentwiki://run?code=lc_xyz&tool=claude-code&endpoint=https%3A%2F%2Fw%2Fapi%2Fmcp",
  );
  assert.equal(r.action, "run");
  assert.equal(r.code, "lc_xyz");
  assert.equal(r.tool, "claude-code");
  assert.equal(r.endpoint, "https://w/api/mcp");
});

test("parses probe URI", () => {
  const r = parseLaunchUri(
    "agentwiki://probe?nonce=n123&endpoint=https%3A%2F%2Fw",
  );
  assert.equal(r.action, "probe");
  assert.equal(r.nonce, "n123");
});

test("rejects unknown scheme", () => {
  assert.throws(() => parseLaunchUri("https://example.com"));
});

test("rejects unknown action", () => {
  assert.throws(() => parseLaunchUri("agentwiki://destroy?x=1"));
});
```

- [ ] **Step 8.2: Impl**

```typescript
// src/uri.ts
type Parsed =
  | { action: "run"; code: string; tool: string; endpoint: string }
  | { action: "probe"; nonce: string; endpoint: string };

export function parseLaunchUri(raw: string): Parsed {
  const url = new URL(raw);
  if (url.protocol !== "agentwiki:")
    throw new Error(`unknown scheme ${url.protocol}`);
  const action = url.host || url.pathname.replace(/^\//, "");
  const params = url.searchParams;
  if (action === "run") {
    const code = params.get("code") ?? "";
    const tool = params.get("tool") ?? "";
    const endpoint = params.get("endpoint") ?? "";
    if (!code || !tool || !endpoint) throw new Error("missing run params");
    return { action, code, tool, endpoint };
  }
  if (action === "probe") {
    const nonce = params.get("nonce") ?? "";
    const endpoint = params.get("endpoint") ?? "";
    if (!nonce || !endpoint) throw new Error("missing probe params");
    return { action, nonce, endpoint };
  }
  throw new Error(`unknown action ${action}`);
}
```

- [ ] **Step 8.3: Test + commit**

---

## Task 9: Exchange client

**Files:**

- Create: `packages/agentwiki-launcher/src/exchange.ts`

Calls `POST /api/launch/exchange` with `{code, machine_id}`. Returns the manifest + token + payload.

- [ ] **Step 9.1: Impl** (no test — covered by integration smoke in Task 13)

```typescript
// src/exchange.ts
import type { Manifest } from "./manifest.js";

export interface ExchangePayload {
  session_id: string;
  working_dir: string | null;
  first_turn_prompt: string | null;
  cli_session_id: string | null;
}

export interface ExchangeResponse {
  mcp_token: string;
  endpoint: string;
  manifest: Manifest;
  payload: ExchangePayload;
}

export async function exchange(
  endpoint: string,
  code: string,
  machineId: string,
): Promise<ExchangeResponse> {
  const url = new URL("/api/launch/exchange", endpoint).toString();
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code, machine_id: machineId }),
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`exchange failed ${res.status}: ${body}`);
  }
  return res.json() as Promise<ExchangeResponse>;
}
```

- [ ] **Step 9.2: Commit**

---

## Task 10: file_watch session capture

**Files:**

- Create: `packages/agentwiki-launcher/src/capture/file_watch.ts`
- Test: `packages/agentwiki-launcher/test/file_watch.test.ts`

mtime-guarded watcher — only files newer than spawn time count (P2 #9 race fix).

- [ ] **Step 10.1: Test**

```typescript
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { watchForNewFile } from "../src/capture/file_watch.js";

test("returns only files created after start mtime", async () => {
  const dir = mkdtempSync(join(tmpdir(), "fw-"));
  try {
    writeFileSync(join(dir, "old.jsonl"), "x");
    await new Promise((r) => setTimeout(r, 50));
    const startedAt = Date.now();
    setTimeout(() => writeFileSync(join(dir, "new.jsonl"), "y"), 200);
    const path = await watchForNewFile({
      dir,
      pattern: /\.jsonl$/,
      startedAtMs: startedAt,
      timeoutMs: 2000,
    });
    assert.match(path, /new\.jsonl$/);
  } finally {
    rmSync(dir, { recursive: true });
  }
});
```

- [ ] **Step 10.2: Impl**

```typescript
// src/capture/file_watch.ts
import { readdirSync, statSync } from "node:fs";
import { join } from "node:path";

interface Opts {
  dir: string;
  pattern: RegExp;
  startedAtMs: number;
  timeoutMs: number;
}

export async function watchForNewFile(opts: Opts): Promise<string> {
  const deadline = Date.now() + opts.timeoutMs;
  while (Date.now() < deadline) {
    try {
      const entries = readdirSync(opts.dir);
      for (const name of entries) {
        if (!opts.pattern.test(name)) continue;
        const full = join(opts.dir, name);
        try {
          const st = statSync(full);
          if (st.mtimeMs >= opts.startedAtMs) return full;
        } catch {
          // file vanished between readdir + stat
        }
      }
    } catch {
      // dir doesn't exist yet — CLI may not have created it
    }
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error("file_watch timeout");
}
```

- [ ] **Step 10.3: Test + commit**

---

## Task 11: Spawn orchestrator + Terminal.app open

**Files:**

- Create: `packages/agentwiki-launcher/src/terminal/darwin.ts`
- Create: `packages/agentwiki-launcher/src/spawn.ts`
- Test: `packages/agentwiki-launcher/test/spawn.test.ts` (mocked execa)

- [ ] **Step 11.1: Test (mocked)**

```typescript
import { test, mock } from "node:test";
import assert from "node:assert/strict";

// Use a mock so we don't actually open Terminal.app in CI.
test("spawn assembles argv from manifest + interpolation", async () => {
  const { buildSpawnCommand } = await import("../src/spawn.js");
  const cmd = buildSpawnCommand({
    manifest: {
      manifest_version: 1,
      id: "claude-code",
      name: "x",
      tagline: "x",
      icon_url: "/x",
      kind: "local_cli",
      launch: {
        binary: "claude",
        argv: ["--mcp-config", "${mcp_config_path}"],
        env: { AGENTWIKI_MCP_TOKEN: "${token}" },
        cwd: "${working_dir}",
      },
    } as any,
    token: "mcp_xyz",
    endpoint: "https://w/api/mcp",
    sessionId: "as_1",
    workingDir: "/home/u/p",
    mcpConfigPath: "/tmp/c.json",
    promptFilePath: "/tmp/p.txt",
  });
  assert.deepEqual(cmd.argv, [
    "--mcp-config",
    "/tmp/c.json",
    "--prompt-file",
    "/tmp/p.txt",
  ]);
  assert.equal(cmd.env.AGENTWIKI_MCP_TOKEN, "mcp_xyz");
  assert.equal(cmd.cwd, "/home/u/p");
});

test("buildSpawnCommand rejects disallowed binary", async () => {
  const { buildSpawnCommand } = await import("../src/spawn.js");
  assert.throws(
    () =>
      buildSpawnCommand({
        manifest: {
          ...({} as any),
          kind: "local_cli",
          launch: { binary: "rm", argv: [], env: {} },
        },
        token: "x",
        endpoint: "x",
        sessionId: "x",
        workingDir: null,
        mcpConfigPath: null,
        promptFilePath: null,
      } as any),
    /binary_not_allowed/,
  );
});
```

- [ ] **Step 11.2: Impl**

```typescript
// src/spawn.ts
import { homedir } from "node:os";
import { createHash } from "node:crypto";

import { assertAllowed } from "./allowed_binaries.js";
import {
  interpolateArgv,
  interpolateEnv,
  type InterpolateContext,
} from "./interpolate.js";
import type { Manifest } from "./manifest.js";

interface BuildOpts {
  manifest: Manifest;
  token: string;
  endpoint: string;
  sessionId: string;
  cliSessionId?: string | null;
  workingDir: string | null;
  mcpConfigPath: string | null;
  promptFilePath: string | null;
  isResume?: boolean;
}

export interface SpawnCommand {
  binary: string;
  argv: string[];
  env: Record<string, string>;
  cwd: string;
}

export function buildSpawnCommand(opts: BuildOpts): SpawnCommand {
  const block = opts.isResume ? opts.manifest.resume : opts.manifest.launch;
  if (!block)
    throw new Error(
      opts.isResume ? "manifest has no resume" : "manifest has no launch",
    );
  assertAllowed(block.binary);

  const cwd = opts.workingDir ?? homedir();
  const dirhash = createHash("sha256").update(cwd).digest("hex").slice(0, 16);
  const ctx: InterpolateContext = {
    token: opts.token,
    endpoint: opts.endpoint,
    session_id: opts.sessionId,
    cli_session_id: opts.cliSessionId ?? null,
    working_dir: cwd,
    prompt_file_path: opts.promptFilePath,
    mcp_config_path: opts.mcpConfigPath,
    home: homedir(),
    dirhash,
  };

  let argv = interpolateArgv(block.argv, ctx);
  // If first_turn_prompt_delivery is prompt_file_flag and we have a prompt file, append it.
  if (
    !opts.isResume &&
    opts.manifest.first_turn_prompt_delivery?.method === "prompt_file_flag" &&
    opts.promptFilePath
  ) {
    const flag =
      opts.manifest.first_turn_prompt_delivery.flag ?? "--prompt-file";
    argv = [...argv, flag, opts.promptFilePath];
  }
  const env = interpolateEnv(block.env, ctx);
  return { binary: block.binary, argv, env, cwd };
}
```

```typescript
// src/terminal/darwin.ts
import { execFile } from "node:child_process";

/**
 * Open Terminal.app in a new window running the given command.
 *
 * On macOS we don't get to keep the spawned process attached to the
 * helper — Terminal.app owns the pty. So this fire-and-forgets.
 */
export function openInTerminalApp(opts: {
  binary: string;
  argv: string[];
  env: Record<string, string>;
  cwd: string;
}): void {
  const envPrefix = Object.entries(opts.env)
    .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
    .join(" ");
  const argvQuoted = opts.argv.map((a) => JSON.stringify(a)).join(" ");
  const cd = `cd ${JSON.stringify(opts.cwd)}`;
  const cmd = `${cd} && ${envPrefix} ${opts.binary} ${argvQuoted}`;
  const escaped = cmd.replace(/"/g, '\\"');
  const osascript = `tell application "Terminal" to do script "${escaped}"`;
  execFile("osascript", ["-e", osascript], { stdio: "ignore" });
}
```

- [ ] **Step 11.3: Test + commit**

---

## Task 12: CLI entry point + URI handler routing

**Files:**

- Create: `packages/agentwiki-launcher/src/cli.ts`

Argument shape:

- `agentwiki-launcher run <uri>` — main flow
- `agentwiki-launcher probe-ack <uri>` — invoked by URI handler when scheme is `probe`
- `agentwiki-launcher serve-probe-port` — internal; starts localhost HTTP for `/probe-cli`

- [ ] **Step 12.1: Impl**

```typescript
// src/cli.ts
import { homedir } from "node:os";
import { mkdirSync } from "node:fs";
import { join } from "node:path";

import { parseLaunchUri } from "./uri.js";
import { getOrCreateMachineId } from "./machine_id.js";
import { exchange } from "./exchange.js";
import { parseManifest } from "./manifest.js";
import { withSecureTmpfiles } from "./tmpfile.js";
import { renderClaudeJson } from "./mcp_config/claude_json.js";
import { renderCodexToml } from "./mcp_config/codex_toml.js";
import { buildSpawnCommand } from "./spawn.js";
import { openInTerminalApp } from "./terminal/darwin.js";

export async function main(argv: string[]): Promise<void> {
  const sub = argv[0];
  if (sub === "run") {
    await handleRun(argv[1]);
  } else if (sub === "probe-ack") {
    await handleProbeAck(argv[1]);
  } else {
    console.error("usage: agentwiki-launcher (run <uri> | probe-ack <uri>)");
    process.exit(2);
  }
}

async function handleRun(uri: string): Promise<void> {
  const parsed = parseLaunchUri(uri);
  if (parsed.action !== "run") throw new Error("expected run action");

  const machineId = getOrCreateMachineId();
  const exchanged = await exchange(parsed.endpoint, parsed.code, machineId);
  const manifest = parseManifest(exchanged.manifest);
  if (manifest.kind !== "local_cli")
    throw new Error(`unsupported kind ${manifest.kind}`);

  const isResume = exchanged.payload.cli_session_id !== null;

  await withSecureTmpfiles(
    {
      ...(manifest.mcp_config_format === "claude_json"
        ? {
            mcp: renderClaudeJson({
              url: exchanged.endpoint,
              token: exchanged.mcp_token,
            }),
          }
        : manifest.mcp_config_format === "codex_toml"
          ? {
              mcp: renderCodexToml({
                url: exchanged.endpoint,
                token: exchanged.mcp_token,
              }),
            }
          : {}),
      ...(exchanged.payload.first_turn_prompt
        ? { prompt: exchanged.payload.first_turn_prompt }
        : {}),
    },
    async (paths) => {
      const cmd = buildSpawnCommand({
        manifest,
        token: exchanged.mcp_token,
        endpoint: exchanged.endpoint,
        sessionId: exchanged.payload.session_id,
        cliSessionId: exchanged.payload.cli_session_id,
        workingDir: exchanged.payload.working_dir,
        mcpConfigPath: (paths as any).mcp ?? null,
        promptFilePath: (paths as any).prompt ?? null,
        isResume,
      });
      openInTerminalApp(cmd);
      // Give Terminal.app a moment to read the tmpfiles before we delete them.
      await new Promise((r) => setTimeout(r, 1500));
    },
  );
}

async function handleProbeAck(uri: string): Promise<void> {
  const parsed = parseLaunchUri(uri);
  if (parsed.action !== "probe") throw new Error("expected probe action");
  // POST nonce + port back to backend.
  await fetch(new URL("/api/launch/probe-ack", parsed.endpoint).toString(), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ nonce: parsed.nonce, helper_port: 31415 }),
  });
}
```

- [ ] **Step 12.2: Build + smoke**

```bash
cd /Users/nikolas/agent-wiki/packages/agentwiki-launcher
npm run build
./bin/agentwiki-launcher run "agentwiki://run?code=fake&tool=claude-code&endpoint=http%3A%2F%2F127.0.0.1%3A8088"
```

Expected: exchange will fail (fake code) but the parse + machine_id + exchange call path runs end-to-end.

- [ ] **Step 12.3: Commit**

---

## Task 13: `.app` bundle + URI scheme registration on macOS

**Files:**

- Create: `packages/agentwiki-launcher/src/install/darwin.ts`
- Create: `packages/agentwiki-launcher/src/install/postinstall.ts`
- Create: `packages/agentwiki-launcher/install/AgentWiki.app/Contents/Info.plist` (template)
- Create: `packages/agentwiki-launcher/install/AgentWiki.app/Contents/MacOS/agentwiki-launcher-stub` (bash)

Tricky bit: npm postinstall typically can't elevate. Solution: create `~/Applications/AgentWiki.app` (user-local, no admin needed); `Info.plist` declares the `agentwiki` URI scheme; the embedded stub binary just `exec`s the global `agentwiki-launcher run "$@"`. macOS calls `LSSetDefaultHandlerForURLScheme` via `Launch Services` to register.

- [ ] **Step 13.1: `Info.plist` template**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key><string>agentwiki-launcher-stub</string>
  <key>CFBundleIdentifier</key><string>com.agentwiki.launcher</string>
  <key>CFBundleName</key><string>AgentWiki Launcher</string>
  <key>CFBundleVersion</key><string>0.1.0</string>
  <key>LSUIElement</key><true/>
  <key>CFBundleURLTypes</key>
  <array>
    <dict>
      <key>CFBundleURLName</key><string>com.agentwiki.launcher.url</string>
      <key>CFBundleURLSchemes</key><array><string>agentwiki</string></array>
    </dict>
  </array>
</dict>
</plist>
```

- [ ] **Step 13.2: Stub binary `agentwiki-launcher-stub`**

```bash
#!/bin/bash
# Receives the URI as $1 when Launch Services routes it.
URI="$1"
exec /usr/local/bin/agentwiki-launcher run "$URI"
```

Mark executable.

- [ ] **Step 13.3: Install module**

```typescript
// src/install/darwin.ts
import { execSync } from "node:child_process";
import { cpSync, existsSync, mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

export function installOnDarwin(): void {
  const here = dirname(fileURLToPath(import.meta.url));
  const src = join(here, "..", "..", "install", "AgentWiki.app");
  const dest = join(homedir(), "Applications", "AgentWiki.app");
  mkdirSync(dirname(dest), { recursive: true });
  cpSync(src, dest, { recursive: true });
  // Make sure stub is executable (cpSync may strip it on some FSes).
  execSync(
    `chmod +x "${join(dest, "Contents", "MacOS", "agentwiki-launcher-stub")}"`,
  );
  // Force Launch Services to re-scan the bundle.
  try {
    execSync(
      `/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "${dest}"`,
    );
  } catch (e) {
    console.warn(
      "[agentwiki-launcher] lsregister failed; you may need to open the .app once manually.",
    );
  }
  console.log(`[agentwiki-launcher] installed ${dest}`);
}
```

```typescript
// src/install/postinstall.ts
import { platform } from "node:process";

async function main() {
  try {
    if (platform === "darwin") {
      const { installOnDarwin } = await import("./darwin.js");
      installOnDarwin();
    } else if (platform === "linux") {
      console.log("[agentwiki-launcher] Linux install — Phase 4.");
    } else if (platform === "win32") {
      console.log("[agentwiki-launcher] Windows install — Phase 4.");
    }
  } catch (e) {
    console.warn("[agentwiki-launcher] postinstall failed:", e);
    console.warn(
      "Run `agentwiki-launcher` manually or see docs to register the URI scheme.",
    );
  }
}

await main();
```

- [ ] **Step 13.4: Test the install locally**

```bash
cd /Users/nikolas/agent-wiki/packages/agentwiki-launcher
npm run build
npm pack
npm install -g ./agentwiki-launcher-0.1.0-alpha.1.tgz
```

Verify:

- `~/Applications/AgentWiki.app` exists.
- `/usr/local/bin/agentwiki-launcher` exists (npm global bin).
- Clicking an `agentwiki://...` URL in a browser opens... a Terminal? An exchange call? At minimum: not 404.

- [ ] **Step 13.5: Commit**

---

## Task 14: Verify Claude Code manifest against real CLI

This is the **critical reality-check task**. Run real `claude` against the helper and patch the manifest where my Phase 1 guesses are wrong.

- [ ] **Step 14.1: Dry-run the manifest's argv against `claude --help`**

```bash
claude --help 2>&1 | grep -E "mcp|resume|prompt" | head -20
```

Check whether:

- `--mcp-config <path>` is the actual flag name. If `--mcp-server` or absent, patch.
- `--resume <id>` exists. Confirm.
- `--prompt-file <path>` exists. If not, fall back to stdin delivery in the manifest.
- Session jsonl writes to `~/.claude/projects/<dirhash>/<id>.jsonl`. Verify with `ls ~/.claude/projects/ && claude` (start one quick session).

- [ ] **Step 14.2: Patch `backend/app/launchers/manifests/claude_code.json`**

Update any guessed values to real ones. Commit the patch to the backend with message like:

```
fix(launchers): verify claude_code manifest against real CLI

- s/--mcp-config/--actual-flag-name/
- prompt delivery: prompt_file_flag → stdin (no --prompt-file in this CLI version)
- session_id_capture: confirmed path = ~/.claude/projects/<dirhash>/
```

- [ ] **Step 14.3: Run an end-to-end launch**

```bash
# Terminal A: backend + worker
# Terminal B: frontend
# Terminal C: this verification
```

In the browser:

1. Click Run Agent on a wiki page.
2. Hit Run → URI dispatches → Terminal.app opens → `claude` runs.
3. Confirm `claude` prints the first-turn prompt (page body + workdir + message).
4. In `claude`, type `read the wiki doc at architecture.md`.
5. Verify the wiki UI's `ActiveSessionsList` lights up live.
6. In `claude`, ask it to edit the doc. Confirm the edit appears in the wiki.
7. Exit `claude`. Wait 60s. Verify session marks `closed` in `/agents`.

- [ ] **Step 14.4: Commit any manifest patches + smoke results**

```bash
git -C /Users/nikolas/agent-wiki add backend/app/launchers/manifests/claude_code.json
git -C /Users/nikolas/agent-wiki commit -m "fix(launchers): patch claude_code manifest to real CLI surface"
```

---

## Task 15: Heartbeat + close-on-exit

**Files:**

- Create: `packages/agentwiki-launcher/src/heartbeat.ts`

Helper doesn't stay attached to the CLI (Terminal.app owns it). So heartbeat is sent **once on launch** and then the backend's idle sweep takes over. Future improvement: ship a small wrapper script that the user sources via `.zshrc` to send heartbeats from the shell.

- [ ] **Step 15.1: One-shot heartbeat POST**

```typescript
// src/heartbeat.ts
export async function postOneShotHeartbeat(
  endpoint: string,
  sessionId: string,
  token: string,
): Promise<void> {
  try {
    await fetch(
      new URL(
        `/api/agent-sessions/${sessionId}/heartbeat`,
        endpoint,
      ).toString(),
      {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      },
    );
  } catch {
    // Best-effort.
  }
}
```

Call from `handleRun` right after spawn.

- [ ] **Step 15.2: Commit**

---

## Task 16: Publish alpha

- [ ] **Step 16.1: Run full helper test suite**

```bash
cd /Users/nikolas/agent-wiki/packages/agentwiki-launcher
npm run typecheck
npm test
```

All green.

- [ ] **Step 16.2: Publish dry-run**

```bash
npm publish --dry-run --access public
```

Verify the included files. Then real publish (only after consensus with reviewer):

```bash
npm publish --access public --tag alpha
```

- [ ] **Step 16.3: Update docs**

Add a section to `design.md` "Status: Phase 3 shipped (macOS + Claude Code only)" and a smoke section to the wiki page.

- [ ] **Step 16.4: Push**

```bash
git -C /Users/nikolas/agent-wiki push
```

---

## Done

After Task 16, Phase 3 is shippable:

- npm package builds, installs, registers `agentwiki://` URI scheme on macOS.
- Real `claude` CLI launches with MCP wired + first-turn prompt.
- Wiki UI sessions list updates live as the agent does work.
- Codex + Linux + Windows are explicitly out of scope — that's Phase 4.

Known remaining gaps after this phase:

- Codex CLI not yet verified (Phase 4 — different argv surface).
- Linux + Windows install paths missing (Phase 4).
- Real interactive resume via the Resume button on `ActiveSessionsList` (Phase 4 hardens, after Codex works).
