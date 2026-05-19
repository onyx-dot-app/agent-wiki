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
import { spawn } from "node:child_process";
import {
  accessSync,
  appendFileSync,
  chmodSync,
  mkdirSync,
  mkdtempSync,
  writeFileSync,
  constants as fsConstants,
} from "node:fs";
import { homedir, tmpdir } from "node:os";
import { isAbsolute, join, resolve } from "node:path";

import { assertValidEnvKeys } from "./select.js";
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

function expandHome(pathname: string): string {
  if (pathname.startsWith("~/")) {
    return join(homedir(), pathname.slice(2));
  }
  return pathname;
}

function isExecutablePath(pathname: string): boolean {
  const expanded = expandHome(pathname);
  const full = isAbsolute(expanded) ? expanded : resolve(expanded);
  try {
    accessSync(full, fsConstants.X_OK);
    return true;
  } catch {
    return false;
  }
}

function commandExists(bin: string): boolean {
  if (bin.includes("/")) {
    return isExecutablePath(bin);
  }
  const pathEnv = process.env.PATH;
  if (!pathEnv) return false;
  for (const rawDir of pathEnv.split(":")) {
    const dir = rawDir.length > 0 ? rawDir : process.cwd();
    const expandedDir = expandHome(dir);
    const baseDir = isAbsolute(expandedDir)
      ? expandedDir
      : resolve(expandedDir);
    const candidate = join(baseDir, bin);
    if (isExecutablePath(candidate)) {
      return true;
    }
  }
  return false;
}

function pickTerminal(): TerminalLaunch {
  const overrideRaw =
    process.env.AGENTWIKI_TERMINAL?.trim() || process.env.TERMINAL?.trim();
  const override = overrideRaw && overrideRaw.length > 0 ? overrideRaw : null;
  if (override) {
    const display = override.replace(/\s+/g, " ");
    if (!commandExists(override)) {
      throw new Error(
        `AGENTWIKI_TERMINAL=${display} not found; set it to a terminal binary name (no arguments).`,
      );
    }
    return { bin: override, argv: runFlagFor(override) };
  }
  const candidate = CANDIDATES.find(commandExists);
  if (!candidate) {
    throw new Error(
      `no supported terminal found (looked for ${CANDIDATES.join(
        ", ",
      )}); install one or set $AGENTWIKI_TERMINAL`,
    );
  }
  return { bin: candidate, argv: runFlagFor(candidate) };
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
  assertValidEnvKeys(opts.env);
  const { bin, argv } = pickTerminal();
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

  const cwdLog = shellQuote(opts.cwd.replace(/\r?\n/g, " "));
  const binaryLog = shellQuote(opts.binary.replace(/\r?\n/g, " "));

  const script = `#!/bin/bash
LOG="$HOME/.agentwiki/spawn.log"
mkdir -p "$HOME/.agentwiki"
printf '[%s] wrapper start cwd=%s bin=%s\n' "$(date)" ${cwdLog} ${binaryLog} >> "$LOG" 2>&1
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
printf '[%s] launching %s\n' "$(date)" ${binaryLog} >> "$LOG"
${shellQuote(opts.binary)} ${argvQuoted}
rc=$?
printf '[%s] %s exited code=%s\n' "$(date)" ${binaryLog} "$rc" >> "$LOG"
# Keep the window open so the user can read CLI output / error before
# the emulator closes the tab. Most users expect this; "read -r" is a
# light touch — they can hit enter to dismiss.
echo
echo "(session ended — press enter to close)"
read -r _
`;
  writeFileSync(wrapper, script, { mode: 0o700 });
  chmodSync(wrapper, 0o700);

  spawn(bin, [...argv, "bash", wrapper], {
    stdio: "ignore",
    detached: true,
  }).unref();
}

function shellQuote(s: string): string {
  return `'${s.replace(/'/g, `'\\''`)}'`;
}
