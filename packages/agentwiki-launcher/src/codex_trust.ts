/**
 * Pre-mark a directory as trusted in codex's per-project config so the
 * launched session doesn't block on the "Allow Codex to work in this
 * folder" prompt.
 *
 * Codex tracks per-folder trust in ``~/.codex/config.toml`` as a
 * top-level table per path:
 *
 *   [projects."/Users/nikolas/agent-wiki-runs/as_xxx"]
 *   trust_level = "trusted"
 *
 * ``--dangerously-bypass-approvals-and-sandbox`` bypasses per-command
 * approvals during execution but does NOT skip the initial folder-trust
 * prompt — that gate runs before any sandboxing decision. We flip the
 * trust entry ourselves before spawn.
 *
 * Best-effort + idempotent: if the block already exists, no-op; if the
 * config doesn't exist or isn't writable, log and continue (codex
 * will prompt and the user can hit "1" manually).
 */
import { readFileSync, renameSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function markCodexProjectTrusted(cwd: string): void {
  const configPath = join(homedir(), ".codex", "config.toml");
  let raw: string;
  try {
    raw = readFileSync(configPath, "utf-8");
  } catch {
    return;
  }
  const header = `[projects."${cwd}"]`;
  const headerRe = new RegExp(`^${escapeRegex(header)}\\b`, "m");
  if (headerRe.test(raw)) return;
  const block = `\n${header}\ntrust_level = "trusted"\n`;
  const next = raw.endsWith("\n") ? raw + block : raw + "\n" + block;
  const tmp = `${configPath}.agw-tmp-${process.pid}`;
  try {
    writeFileSync(tmp, next, { mode: 0o600 });
    renameSync(tmp, configPath);
  } catch (e) {
    console.error(
      "[agentwiki-launcher] could not write ~/.codex/config.toml — codex will prompt:",
      e instanceof Error ? e.message : e,
    );
  }
}
