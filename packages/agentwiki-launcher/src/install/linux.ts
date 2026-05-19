/**
 * Register ``agentwiki://`` as a URL handler on Linux desktops via the
 * XDG ``.desktop`` + MIME-default mechanism.
 *
 * Writes ``~/.local/share/applications/agentwiki-launcher.desktop``
 * pointing at the installed launcher binary, then asks the desktop
 * cache to re-scan (``update-desktop-database``) and sets the
 * agentwiki scheme handler (``xdg-mime default``).
 *
 * If either tool is missing or fails, we drop a postinstall-status
 * record so the wizard can surface the manual command.
 */
import { execSync } from "node:child_process";
import { mkdirSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

function resolveLauncherPath(): string {
  try {
    return execSync("command -v agentwiki-launcher", {
      encoding: "utf-8",
    }).trim();
  } catch {
    // Fallback: assume sibling to node we're running under.
    return "/usr/local/bin/agentwiki-launcher";
  }
}

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

export function installOnLinux(): void {
  const home = homedir();
  const appsDir = join(home, ".local", "share", "applications");
  mkdirSync(appsDir, { recursive: true });

  const launcherPath = resolveLauncherPath();
  const desktopPath = join(appsDir, "agentwiki-launcher.desktop");
  const desktopContents = [
    "[Desktop Entry]",
    "Type=Application",
    "Name=AgentWiki Launcher",
    // ``%u`` expands to the agentwiki:// URI the browser dispatched.
    // The launcher's ``run`` subcommand parses it.
    `Exec=${desktopExecQuote(launcherPath)} dispatch %u`,
    "NoDisplay=true",
    "MimeType=x-scheme-handler/agentwiki;",
    "",
  ].join("\n");
  const desktopTmp = `${desktopPath}.agw-tmp-${nextTmpSuffix()}`;
  try {
    writeFileSync(desktopTmp, desktopContents, { mode: 0o644 });
    renameSync(desktopTmp, desktopPath);
  } catch (e) {
    rmSync(desktopTmp, { force: true });
    throw e;
  }

  try {
    execSync(`update-desktop-database ${shellQuote(appsDir)}`, {
      stdio: "ignore",
    });
    execSync(
      "xdg-mime default agentwiki-launcher.desktop x-scheme-handler/agentwiki",
      { stdio: "ignore" },
    );
    writeStatus(home, { ok: true });
    console.log(`[agentwiki-launcher] installed ${desktopPath}`);
  } catch (e) {
    const reason = e instanceof Error ? e.message : String(e);
    const manual =
      "xdg-mime default agentwiki-launcher.desktop x-scheme-handler/agentwiki";
    writeStatus(home, { ok: false, reason, manual_command: manual });
    console.warn(
      "[agentwiki-launcher] xdg registration failed — run manually:",
      `\n  ${manual}\nreason: ${reason}`,
    );
  }

  mkdirSync(join(home, ".agentwiki"), { recursive: true, mode: 0o700 });
}

function shellQuote(s: string): string {
  return `'${s.replace(/'/g, `'\\''`)}'`;
}

function desktopExecQuote(s: string): string {
  const escaped = s.replace(/(["\\])/g, "\\$1").replace(/%/g, "%%");
  return `"${escaped}"`;
}

function nextTmpSuffix(): string {
  return `${process.pid}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
