/**
 * Open Terminal.app via a `.command` file routed through LaunchServices.
 *
 * Earlier version used ``osascript -e 'tell application "Terminal" to
 * do script "..."'`` (Apple Events automation). TCC refuses to grant
 * the AgentWiki.app stub kTCCServiceAppleEvents because the unsigned
 * osacompile-produced bundle has no stable designated-requirement —
 * the Apple Event is dropped silently. We switched to writing a
 * ``run.command`` file and shelling out to ``open``, which uses
 * LaunchServices (no AppleEvents permission needed) and Terminal.app
 * runs the file because macOS associates the ``.command`` extension
 * with it by default.
 *
 * Wrapper still holds the lifetime of the spawn tmpfiles (audit
 * fix) via ``trap EXIT`` — cleanup fires when the launched binary
 * exits, not when the helper does.
 */
import { spawn } from "node:child_process";
import { appendFileSync, chmodSync, mkdtempSync, writeFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";

import type { SpawnCommand } from "../spawn.js";

interface OpenOpts extends SpawnCommand {
  tmpfilesToClean: string[];
  // POST to this URL with the bearer token after the launched binary
  // exits, so the backend's session row flips to ``closed`` immediately
  // instead of waiting for the 65-min idle sweep.
  closeOnExit?: { url: string; token: string };
}

export function openInTerminalApp(opts: OpenOpts): void {
  const dir = mkdtempSync(join(tmpdir(), "agw-wrap-"));
  const wrapper = join(dir, "run.command");
  const envExports = Object.entries(opts.env)
    .map(([k, v]) => `export ${k}=${shellQuote(v)}`)
    .join("\n");
  const argvQuoted = opts.argv.map(shellQuote).join(" ");
  const cleanList = [...opts.tmpfilesToClean, wrapper, dir]
    .map(shellQuote)
    .join(" ");

  // Log the full argv from node so the bash script never has to render
  // user-controlled prompt content. Earlier version did
  // ``echo "argv: ${argvQuoted}"`` inside double quotes; if the prompt
  // contained backticks (e.g. markdown code spans like `onyx-cli ask`),
  // bash treated them as command substitution and hung the wrapper.
  const spawnLog = join(homedir(), ".agentwiki", "spawn.log");
  try {
    appendFileSync(
      spawnLog,
      `[${new Date().toString()}] queued ${opts.binary} cwd=${opts.cwd} argc=${
        opts.argv.length
      } mcp_config=${opts.argv[1] ?? "-"}\n`,
    );
  } catch {
    // ignore — wrapper will retry the mkdir + log
  }

  // Build close-on-exit + cleanup as a bash *function* (not a single-
  // quoted trap body). Earlier version put the curl + shellQuote'd
  // URL/token directly INSIDE the trap's outer single-quoted body —
  // bash's nested-quote rules made the inner ``'...'`` segments break
  // out of the outer quoting context, so the curl never actually ran
  // (or ran malformed) and the session row stayed ``active``. Function
  // bodies use plain bash quoting, no collision.
  const closeLine = opts.closeOnExit
    ? `curl -s -o /dev/null -X POST ${shellQuote(
        opts.closeOnExit.url,
      )} -H ${shellQuote(
        `Authorization: Bearer ${opts.closeOnExit.token}`,
      )} -H 'Content-Type: application/json' -d '{"reason":"helper_exit"}' || true`
    : "";

  const script = `#!/bin/bash
LOG="$HOME/.agentwiki/spawn.log"
mkdir -p "$HOME/.agentwiki"
echo "[$(date)] wrapper start cwd=${shellQuote(opts.cwd)} bin=${shellQuote(
    opts.binary,
  )}" >> "$LOG" 2>&1
__agentwiki_on_exit() {
  local rc=$?
  echo "[$(date)] wrapper exit code=$rc" >> "$LOG"
  ${closeLine}
  rm -rf ${cleanList}
}
trap __agentwiki_on_exit EXIT
cd ${shellQuote(opts.cwd)} 2>>"$LOG" || { echo "cd failed" >> "$LOG"; exit 1; }
${envExports}
# PATH note: 'open -a Terminal.app run.command' invokes the file inside
# Terminal, which has already started a login shell and sourced the
# user's zsh init. The bash wrapper inherits that PATH. Earlier versions
# also did 'source ~/.zshrc' from inside bash, but some zsh init scripts
# spawn long-running children (e.g. onyx-cli's interactive hook) that
# hang the wrapper indefinitely — bash sourcing zsh is fragile in
# general, so we drop it.
echo "[$(date)] PATH=$PATH" >> "$LOG"
echo "[$(date)] which: $(command -v ${shellQuote(
    opts.binary,
  )} 2>&1 || echo NOT_FOUND)" >> "$LOG"
echo "[$(date)] launching ${opts.binary}" >> "$LOG"
# Run binary inheriting stdin/stdout/stderr from Terminal's TTY.
# Earlier version piped stderr through \`tee\` (process substitution)
# which can confuse interactive CLIs about TTY state — claude detected
# non-TTY stderr and exited clean (code=0) without showing UI.
${shellQuote(opts.binary)} ${argvQuoted}
echo "[$(date)] ${opts.binary} exited code=$?" >> "$LOG"
`;
  writeFileSync(wrapper, script);
  chmodSync(wrapper, 0o700);

  // ``open -a Terminal.app <wrapper>`` goes through LaunchServices —
  // no AppleEvents permission required. Terminal.app receives the
  // ``.command`` file as its open document and runs it in a new tab.
  spawn("open", ["-a", "Terminal.app", wrapper], {
    stdio: "ignore",
    detached: true,
  }).unref();
}

function shellQuote(s: string): string {
  // Single-quote with internal single-quote escape.
  return `'${s.replace(/'/g, `'\\''`)}'`;
}
