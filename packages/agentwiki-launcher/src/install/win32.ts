/**
 * Register ``agentwiki://`` as a URL handler on Windows via HKCU
 * registry edits.
 *
 * HKCU is per-user; no UAC prompt. The chain we write is:
 *
 *   HKCU\Software\Classes\agentwiki
 *     (Default)        = "URL:AgentWiki Protocol"
 *     URL Protocol     = ""
 *   HKCU\Software\Classes\agentwiki\shell\open\command
 *     (Default)        = ""<launcher>" dispatch "%1""
 *
 * The launcher path is resolved at install time from
 * ``npm config get prefix`` so the entry survives non-default npm
 * locations (nvm-windows, scoop, volta).
 *
 * If a step fails (read-only registry, locked-down policy), we surface
 * the failure via ``postinstall-status.json`` so the wizard can show
 * the manual command.
 */
import { execSync } from "node:child_process";
import { mkdirSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

interface PostinstallStatus {
  ok: boolean;
  reason?: string;
  manual_command?: string;
}

function writeStatus(home: string, status: PostinstallStatus): void {
  const dir = join(home, ".agentwiki");
  mkdirSync(dir, { recursive: true, mode: 0o700 });
  const statusPath = join(dir, "postinstall-status.json");
  const tmp = `${statusPath}.agw-tmp-${nextTmpSuffix()}`;
  try {
    writeFileSync(tmp, JSON.stringify(status), { mode: 0o600 });
    renameSync(tmp, statusPath);
  } catch (e) {
    rmSync(tmp, { force: true });
    throw e;
  }
}

function resolveLauncherPath(): string {
  // npm shim lives at ``<prefix>\agentwiki-launcher.cmd``. ``npm config
  // get prefix`` returns whatever the user's tool decided (default
  // ``%APPDATA%\npm``; scoop/volta/nvm-windows put it elsewhere).
  try {
    const prefix = execSync("npm config get prefix", {
      encoding: "utf-8",
    }).trim();
    return join(prefix, "agentwiki-launcher.cmd");
  } catch {
    return join(
      process.env.APPDATA ?? "%APPDATA%",
      "npm",
      "agentwiki-launcher.cmd",
    );
  }
}

export function installOnWin32(): void {
  const home = homedir();
  const launcherCmd = resolveLauncherPath();
  // Registry strings need quote-escaping inside REG ADD's /d value.
  const launcherRegEscaped = launcherCmd.replace(/"/g, '\\"');
  const cmdValue = `"\\"${launcherRegEscaped}\\" dispatch \\"%1\\""`;

  const regCmds = [
    `REG ADD "HKCU\\Software\\Classes\\agentwiki" /ve /d "URL:AgentWiki Protocol" /f`,
    `REG ADD "HKCU\\Software\\Classes\\agentwiki" /v "URL Protocol" /d "" /f`,
    `REG ADD "HKCU\\Software\\Classes\\agentwiki\\shell\\open\\command" /ve /d ${cmdValue} /f`,
  ];

  const failures: string[] = [];
  for (const cmd of regCmds) {
    try {
      execSync(cmd, { stdio: "ignore" });
    } catch (e) {
      failures.push(`${cmd}\n  ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  mkdirSync(join(home, ".agentwiki"), { recursive: true, mode: 0o700 });

  if (failures.length === 0) {
    writeStatus(home, { ok: true });
    console.log(
      `[agentwiki-launcher] registered agentwiki:// handler -> ${launcherCmd}`,
    );
    return;
  }
  const manual = regCmds.join("\n");
  writeStatus(home, {
    ok: false,
    reason: failures.join("\n"),
    manual_command: manual,
  });
  console.warn(
    "[agentwiki-launcher] registry edits failed — run manually as the same user:",
    `\n${manual}`,
  );
}

function nextTmpSuffix(): string {
  return `${process.pid}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
