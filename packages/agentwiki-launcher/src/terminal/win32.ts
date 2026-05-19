/**
 * Open a Windows terminal window running the agent CLI.
 *
 * Preference: Windows Terminal (``wt`` — modern, tabbed, default on
 * Win11) > ``cmd.exe`` fallback. Both invocations route through
 * ``cmd /K`` so the window stays open with the CLI's last output
 * visible after the agent exits; the user closes it themselves.
 *
 * Cleanup + close-on-exit run as cmd ``&&`` chains rather than the
 * bash trap pattern darwin/linux use. There's no portable equivalent
 * to ``trap EXIT`` in cmd, so a hard kill (closing the window via X)
 * skips the close beacon. The backend's 65-min idle sweep will catch
 * that orphan eventually; v2 can add a Windows-side service that
 * shadows the session row.
 */
import { spawn, spawnSync } from "node:child_process";
import { appendFileSync, mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";

import type { OpenOpts } from "./select.js";

function hasWt(): boolean {
  const r = spawnSync("where", ["wt"], { stdio: "ignore" });
  return r.status === 0;
}

export function openInWindowsTerminal(opts: OpenOpts): void {
  const dir = mkdtempSync(join(tmpdir(), "agw-wrap-"));
  const wrapper = join(dir, "run.cmd");

  const logDir = join(homedir(), ".agentwiki");
  mkdirSync(logDir, { recursive: true });
  const spawnLog = join(logDir, "spawn.log");
  try {
    appendFileSync(
      spawnLog,
      `[${new Date().toISOString()}] queued ${opts.binary} cwd=${
        opts.cwd
      } argc=${opts.argv.length}\r\n`,
    );
  } catch {
    // ignore — wrapper retries
  }

  // cmd-quote each env value (escape ``%`` and surround with quotes).
  const envLines = Object.entries(opts.env)
    .map(([k, v]) => `set "${k}=${cmdEscapeValue(v)}"`)
    .join("\r\n");
  const argvQuoted = opts.argv.map(cmdQuote).join(" ");
  const cleanLines = [...opts.tmpfilesToClean, wrapper, dir]
    .map((p) => `del /q ${cmdQuote(p)} 2>nul`)
    .join("\r\n");
  const rmdirLine = `rmdir /q /s ${cmdQuote(dir)} 2>nul`;

  // Close beacon: ``curl`` ships with Win 10 1803+. Fall back to
  // PowerShell if missing.
  const closeLine = opts.closeOnExit
    ? `where curl >nul 2>nul && curl -s -o nul -X POST ${cmdQuote(
        opts.closeOnExit.url,
      )} -H ${cmdQuote(
        `Authorization: Bearer ${opts.closeOnExit.token}`,
      )} -H "Content-Type: application/json" -d "{\\"reason\\":\\"helper_exit\\"}" || powershell -Command "try { Invoke-RestMethod -Method Post -Uri '${jsEscape(
        opts.closeOnExit.url,
      )}' -Headers @{Authorization='Bearer ${jsEscape(
        opts.closeOnExit.token,
      )}'; 'Content-Type'='application/json'} -Body '{\\"reason\\":\\"helper_exit\\"}' } catch {}"`
    : "";

  const script = [
    "@echo off",
    `set "LOG=%USERPROFILE%\\.agentwiki\\spawn.log"`,
    `if not exist "%USERPROFILE%\\.agentwiki" mkdir "%USERPROFILE%\\.agentwiki"`,
    `echo [%DATE% %TIME%] wrapper start cwd=${opts.cwd} bin=${opts.binary} >> "%LOG%"`,
    envLines,
    `cd /d ${cmdQuote(opts.cwd)} || (echo cd failed >> "%LOG%" & exit /b 1)`,
    `echo [%DATE% %TIME%] launching ${opts.binary} >> "%LOG%"`,
    `${cmdQuote(opts.binary)} ${argvQuoted}`,
    `echo [%DATE% %TIME%] ${opts.binary} exited code=%ERRORLEVEL% >> "%LOG%"`,
    closeLine,
    cleanLines,
    rmdirLine,
    `echo.`,
    `echo (session ended — press any key to close)`,
    `pause >nul`,
  ]
    .filter((line) => line.length > 0)
    .join("\r\n");

  writeFileSync(wrapper, script);

  if (hasWt()) {
    spawn("wt", ["new-tab", "cmd", "/c", wrapper], {
      stdio: "ignore",
      detached: true,
      shell: false,
    }).unref();
  } else {
    spawn("cmd", ["/c", "start", '""', "cmd", "/c", wrapper], {
      stdio: "ignore",
      detached: true,
      shell: false,
    }).unref();
  }
}

function cmdQuote(s: string): string {
  // Double-quote and escape inner double quotes by doubling them
  // (cmd convention).
  return `"${s.replace(/"/g, '""')}"`;
}

function cmdEscapeValue(s: string): string {
  // Escape characters cmd reads specially inside ``set "K=V"``.
  return s.replace(/"/g, "''").replace(/%/g, "%%");
}

function jsEscape(s: string): string {
  return s.replace(/\\/g, "\\\\").replace(/'/g, "''");
}
