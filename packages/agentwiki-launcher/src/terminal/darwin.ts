/**
 * Open Terminal.app via a `.command` file routed through LaunchServices.
 *
 * Writes a ``run.command`` wrapper script and shells out to ``open``;
 * macOS associates ``.command`` with Terminal.app by default, and
 * LaunchServices needs no AppleEvents permission. The wrapper owns the
 * lifetime of the spawn tmpfiles via ``trap EXIT`` — cleanup fires
 * when the launched binary exits, not when this helper process does.
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

  // Log the full argv from node so the bash script never has to
  // re-render user-controlled prompt content (backticks in the prompt
  // would otherwise trigger command substitution inside double quotes).
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

  // Build close-on-exit + cleanup as a bash function (not a single-
  // quoted trap body) so the inner shellQuote'd URL/token don't have
  // to nest inside another quoting context.
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
# PATH inherits from Terminal.app's login shell (zsh init has already
# run); the bash wrapper does not source ~/.zshrc itself.
echo "[$(date)] PATH=$PATH" >> "$LOG"
echo "[$(date)] which: $(command -v ${shellQuote(
    opts.binary,
  )} 2>&1 || echo NOT_FOUND)" >> "$LOG"
echo "[$(date)] launching ${opts.binary}" >> "$LOG"
# Run binary inheriting stdin/stdout/stderr from Terminal's TTY so
# interactive CLIs (claude/codex) keep TTY semantics.
${shellQuote(opts.binary)} ${argvQuoted}
echo "[$(date)] ${opts.binary} exited code=$?" >> "$LOG"
`;
  writeFileSync(wrapper, script);
  chmodSync(wrapper, 0o700);

  // ``open -a Terminal.app <wrapper>`` routes through LaunchServices
  // (no AppleEvents permission needed); Terminal.app opens the
  // ``.command`` file in a new tab.
  spawn("open", ["-a", "Terminal.app", wrapper], {
    stdio: "ignore",
    detached: true,
  }).unref();
}

function shellQuote(s: string): string {
  // Single-quote with internal single-quote escape.
  return `'${s.replace(/'/g, `'\\''`)}'`;
}
