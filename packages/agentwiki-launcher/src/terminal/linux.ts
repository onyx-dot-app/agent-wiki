/**
 * Open a Linux terminal emulator running the agent CLI.
 *
 * Preference order:
 *   1. ``$AGENTWIKI_TERMINAL`` — explicit override (path or basename).
 *   2. ``$TERMINAL``           — common XDG-ish env var.
 *   3. ``gnome-terminal``      — GNOME default.
 *   4. ``konsole``             — KDE default.
 *   5. ``xfce4-terminal``      — XFCE.
 *   6. ``x-terminal-emulator`` — Debian alternatives shim.
 *   7. ``xterm``               — last resort, ships with X.org.
 *
 * Each emulator has a different "run this command" flag. We write a
 * wrapper script that owns env exports, cwd, the close-on-exit curl,
 * and the tmpfile cleanup, then hand the wrapper path to the emulator.
 * The wrapper itself is ``chmod 0700`` and uses the same bash trap
 * pattern as the darwin opener so a wedged or crashed CLI still closes
 * the backend session row.
 */
import { spawn, spawnSync } from "node:child_process";
import {
  appendFileSync,
  chmodSync,
  mkdirSync,
  mkdtempSync,
  writeFileSync,
} from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";

import type { OpenOpts } from "./select.js";

const CANDIDATES = [
  "gnome-terminal",
  "konsole",
  "xfce4-terminal",
  "x-terminal-emulator",
  "xterm",
];

interface TerminalLaunch {
  bin: string;
  argv: string[]; // argv to pass BEFORE the wrapper path
}

function which(bin: string): boolean {
  const r = spawnSync("which", [bin], { stdio: "ignore" });
  return r.status === 0;
}

function pickTerminal(): TerminalLaunch {
  const override =
    process.env.AGENTWIKI_TERMINAL?.trim() || process.env.TERMINAL?.trim();
  const bin = override || CANDIDATES.find(which) || "xterm";
  return { bin, argv: runFlagFor(bin) };
}

/**
 * Map a terminal binary to the flags that mean "run THIS command".
 * gnome-terminal famously uses ``--`` (everything after is argv);
 * konsole + xfce4-terminal use ``-e``; x-terminal-emulator (the Debian
 * shim) follows gnome-terminal semantics. xterm uses ``-e``.
 */
function runFlagFor(bin: string): string[] {
  const base = bin.split("/").pop() ?? bin;
  if (base.includes("gnome-terminal") || base === "x-terminal-emulator") {
    return ["--"];
  }
  return ["-e"];
}

export function openInLinuxTerminal(opts: OpenOpts): void {
  const dir = mkdtempSync(join(tmpdir(), "agw-wrap-"));
  const wrapper = join(dir, "run.sh");
  const envExports = Object.entries(opts.env)
    .map(([k, v]) => `export ${k}=${shellQuote(v)}`)
    .join("\n");
  const argvQuoted = opts.argv.map(shellQuote).join(" ");
  const cleanList = [...opts.tmpfilesToClean, wrapper, dir]
    .map(shellQuote)
    .join(" ");

  const logDir = join(homedir(), ".agentwiki");
  mkdirSync(logDir, { recursive: true, mode: 0o700 });
  const spawnLog = join(logDir, "spawn.log");
  try {
    appendFileSync(
      spawnLog,
      `[${new Date().toString()}] queued ${opts.binary} cwd=${opts.cwd} argc=${
        opts.argv.length
      }\n`,
    );
  } catch {
    // ignore — wrapper retries
  }

  let closeLine = "";
  if (opts.closeOnExit) {
    const urlQ = shellQuote(opts.closeOnExit.url);
    const authQ = shellQuote(`Authorization: Bearer ${opts.closeOnExit.token}`);
    closeLine = `curl -s -o /dev/null -X POST ${urlQ} -H ${authQ} -H 'Content-Type: application/json' -d '{"reason":"helper_exit"}' || true`;
  }

  const script = `#!/bin/bash
LOG="$HOME/.agentwiki/spawn.log"
mkdir -p "$HOME/.agentwiki"
echo "[$(date)] wrapper start cwd=${opts.cwd} bin=${opts.binary}" >> "$LOG" 2>&1
__agentwiki_on_exit() {
  local rc=$?
  echo "[$(date)] wrapper exit code=$rc" >> "$LOG"
  ${closeLine}
  rm -rf ${cleanList}
}
trap __agentwiki_on_exit EXIT
cd ${shellQuote(opts.cwd)} 2>>"$LOG" || { echo "cd failed" >> "$LOG"; exit 1; }
${envExports}
echo "[$(date)] PATH=$PATH" >> "$LOG"
echo "[$(date)] which: $(command -v ${shellQuote(
    opts.binary,
  )} 2>&1 || echo NOT_FOUND)" >> "$LOG"
echo "[$(date)] launching ${opts.binary}" >> "$LOG"
${shellQuote(opts.binary)} ${argvQuoted}
rc=$?
echo "[$(date)] ${opts.binary} exited code=$rc" >> "$LOG"
# Keep the window open so the user can read CLI output / error before
# the emulator closes the tab. Most users expect this; "read -r" is a
# light touch — they can hit enter to dismiss.
echo
echo "(session ended — press enter to close)"
read -r _
`;
  writeFileSync(wrapper, script);
  chmodSync(wrapper, 0o700);

  const { bin, argv } = pickTerminal();
  spawn(bin, [...argv, "bash", wrapper], {
    stdio: "ignore",
    detached: true,
  }).unref();
}

function shellQuote(s: string): string {
  return `'${s.replace(/'/g, `'\\''`)}'`;
}
