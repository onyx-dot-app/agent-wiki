/**
 * Open a Windows terminal window running the agent CLI.
 *
 * Preference: Windows Terminal (``wt`` — modern, tabbed, default on
 * Win11) > ``cmd.exe`` fallback. We route through a PowerShell wrapper
 * script so env/argv values round-trip without cmd's quoting limits
 * (notably newline-bearing prompt text). The wrapper owns cleanup,
 * close beacons, and the end-of-session pause so the user can read
 * output before the window closes.
 */
import { spawn, spawnSync } from "node:child_process";
import {
  appendFileSync,
  mkdirSync,
  mkdtempSync,
  writeFileSync,
} from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";

import { assertValidEnvKeys } from "./select.js";
import type { OpenOpts } from "./select.js";

function hasWt(): boolean {
  const r = spawnSync("where", ["wt"], { stdio: "ignore" });
  return r.status === 0;
}

export function openInWindowsTerminal(opts: OpenOpts): void {
  assertValidEnvKeys(opts.env);
  const dir = mkdtempSync(join(tmpdir(), "agw-wrap-"));
  const scriptPath = join(dir, "run.ps1");
  const payloadPath = join(dir, "payload.json");

  const logDir = join(homedir(), ".agentwiki");
  mkdirSync(logDir, { recursive: true, mode: 0o700 });
  const spawnLog = join(logDir, "spawn.log");
  try {
    appendFileSync(
      spawnLog,
      `[${new Date().toISOString()}] queued ${sanitizeForLog(opts.binary)} cwd=${sanitizeForLog(
        opts.cwd,
      )} argc=${opts.argv.length}\r\n`,
    );
  } catch {
    // ignore — wrapper retries
  }

  const payload = {
    binary: opts.binary,
    cwd: opts.cwd,
    argv: opts.argv,
    env: opts.env,
    tmpfilesToClean: opts.tmpfilesToClean,
    closeOnExit: opts.closeOnExit ?? null,
  } satisfies {
    binary: string;
    cwd: string;
    argv: string[];
    env: Record<string, string>;
    tmpfilesToClean: string[];
    closeOnExit: { url: string; token: string } | null;
  };
  writeFileSync(payloadPath, JSON.stringify(payload), { mode: 0o600 });

  const script = renderPowerShellWrapper();
  writeFileSync(scriptPath, script, { mode: 0o600 });

  const launchEnv = { ...process.env, AGENTWIKI_PAYLOAD: payloadPath };

  if (hasWt()) {
    spawn(
      "wt",
      [
        "new-tab",
        "powershell",
        "-NoExit",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        scriptPath,
      ],
      {
        stdio: "ignore",
        detached: true,
        shell: false,
        env: launchEnv,
      },
    ).unref();
  } else {
    spawn(
      "cmd",
      [
        "/c",
        "start",
        "",
        "powershell",
        "-NoExit",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        scriptPath,
      ],
      {
        stdio: "ignore",
        detached: true,
        shell: false,
        env: launchEnv,
      },
    ).unref();
  }
}

function renderPowerShellWrapper(): string {
  return [
    "param()",
    "$ErrorActionPreference = 'Stop'",
    "Set-StrictMode -Version 3",
    "",
    "$scriptPath = $PSCommandPath",
    "$scriptDir = Split-Path -Parent $scriptPath",
    "$payloadPath = $env:AGENTWIKI_PAYLOAD",
    "if (-not $payloadPath) {",
    "  $payloadPath = Join-Path $scriptDir 'payload.json'",
    "}",
    "",
    "$payload = Get-Content -LiteralPath $payloadPath -Raw | ConvertFrom-Json",
    "$binary = [string]$payload.binary",
    "$cwd = [string]$payload.cwd",
    "$argv = @()",
    "foreach ($arg in $payload.argv) { $argv += [string]$arg }",
    "$envMap = $payload.env",
    "$tmpfiles = @()",
    "foreach ($tmp in $payload.tmpfilesToClean) { $tmpfiles += [string]$tmp }",
    "$close = $payload.closeOnExit",
    "",
    "$logDir = Join-Path $env:USERPROFILE '.agentwiki'",
    "if (-not (Test-Path -LiteralPath $logDir)) {",
    "  New-Item -ItemType Directory -Path $logDir -Force | Out-Null",
    "}",
    "$logPath = Join-Path $logDir 'spawn.log'",
    "",
    "function Sanitize([string]$s) {",
    "  if ($null -eq $s) { return '' }",
    "  return ($s -replace '[\\r\\n]+', ' ')",
    "}",
    "",
    "function Write-SpawnLog([string]$message) {",
    "  $safe = Sanitize(\"[{0}] {1}\" -f (Get-Date).ToString('s'), $message)",
    "  try { Add-Content -LiteralPath $logPath -Value $safe } catch {}",
    "}",
    "",
    "if ($null -ne $envMap) {",
    "  foreach ($entry in $envMap.PSObject.Properties) {",
    "    $env[$entry.Name] = [string]$entry.Value",
    "  }",
    "}",
    "",
    "Write-SpawnLog(\"wrapper start cwd=$cwd bin=$binary\")",
    "$exitCode = 0",
    "try {",
    "  Set-Location -LiteralPath $cwd",
    "  Write-SpawnLog(\"launching $binary\")",
    "  & $binary @argv",
    "  $exitCode = $LASTEXITCODE",
    "  Write-SpawnLog(\"$binary exited code=$exitCode\")",
    "} catch {",
    "  $exitCode = 1",
    "  Write-SpawnLog(\"wrapper error: $_\")",
    "} finally {",
    "  if ($close) {",
    "    try {",
    "      $closeUrl = [string]$close.url",
    "      $closeToken = [string]$close.token",
    "      Invoke-RestMethod -Method Post -Uri $closeUrl -Headers @{",
    "        Authorization = 'Bearer ' + $closeToken;",
    "        'Content-Type' = 'application/json'",
    "      } -Body '{\"reason\":\"helper_exit\"}' | Out-Null",
    "    } catch {}",
    "  }",
    "  foreach ($tmp in $tmpfiles) {",
    "    try { Remove-Item -LiteralPath $tmp -Force } catch {}",
    "  }",
    "  try { Remove-Item -LiteralPath $payloadPath -Force } catch {}",
    "}",
    "",
    "Write-Host ''",
    "Write-Host '(session ended — press enter to close)'",
    "[void][System.Console]::ReadLine()",
    "",
    "try { Remove-Item -LiteralPath $scriptPath -Force } catch {}",
    "try {",
    "  Set-Location -LiteralPath $env:TEMP",
    "  Remove-Item -LiteralPath $scriptDir -Recurse -Force",
    "} catch {}",
    "exit $exitCode",
    "",
  ].join("\r\n");
}

function sanitizeForLog(input: string): string {
  return input.replace(/\r?\n/g, " ");
}
