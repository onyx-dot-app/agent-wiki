/**
 * Pre-mark a directory as trusted in claude's per-project config so the
 * launched session doesn't block on the "Do you trust the files in this
 * folder?" dialog.
 *
 * Claude maintains a ``hasTrustDialogAccepted: boolean`` flag per
 * directory inside ``~/.claude.json`` under
 * ``projects.<absolute-path>``. ``--dangerously-skip-permissions``
 * bypasses tool/permission gates but NOT the workspace trust dialog —
 * that's a separate gate. When the helper launches claude in unscoped
 * (no-workdir) mode, the agent has no human at the keyboard for the
 * trust prompt, so we flip the flag ourselves.
 *
 * Atomic write: read → modify → write tmp → rename. JSON formatting is
 * preserved as 2-space pretty-printed object (matches existing file).
 *
 * Best-effort: if ``~/.claude.json`` doesn't exist or isn't writable,
 * we log and continue — claude will prompt and the user can hit "1" to
 * proceed manually.
 */
import { readFileSync, renameSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

interface ClaudeConfig {
  projects?: Record<string, Record<string, unknown> | undefined>;
  [k: string]: unknown;
}

export function markClaudeWorkspaceTrusted(cwd: string): void {
  const configPath = join(homedir(), ".claude.json");
  let raw: string;
  try {
    raw = readFileSync(configPath, "utf-8");
  } catch {
    return;
  }
  let parsed: ClaudeConfig;
  try {
    parsed = JSON.parse(raw) as ClaudeConfig;
  } catch (e) {
    console.error(
      "[agentwiki-launcher] could not parse ~/.claude.json — skipping trust pre-mark:",
      e instanceof Error ? e.message : e,
    );
    return;
  }
  parsed.projects ??= {};
  const project = parsed.projects[cwd] ?? {};
  if (project.hasTrustDialogAccepted === true) return;
  project.hasTrustDialogAccepted = true;
  parsed.projects[cwd] = project;
  const next = JSON.stringify(parsed, null, 2);
  const tmp = `${configPath}.agw-tmp-${process.pid}`;
  try {
    writeFileSync(tmp, next, { mode: 0o600 });
    renameSync(tmp, configPath);
  } catch (e) {
    console.error(
      "[agentwiki-launcher] could not write ~/.claude.json — claude will prompt:",
      e instanceof Error ? e.message : e,
    );
  }
}
