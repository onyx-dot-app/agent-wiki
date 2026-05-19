/**
 * Inject the agent-wiki MCP server block into ``~/.codex/config.toml``.
 *
 * Codex reads its MCP server list ONLY from ``~/.codex/config.toml`` —
 * no per-session config flag — so the helper edits that file in place:
 *
 *   1. Read ``~/.codex/config.toml`` (treat missing as empty).
 *   2. Strip any agent-wiki block (marked or unmarked).
 *   3. Append a fresh marked block with this session's URL + bearer.
 *   4. Atomic tmp+rename write at mode 0600.
 *
 * The block is intentionally not torn down on wrapper exit — the next
 * launch overwrites it. The token rotates per session and the file
 * lives in the user's own home dir; manifest validation forbids
 * embedding the token in argv, hence the config-file detour.
 */
import { readFileSync, renameSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const START_MARKER = "# >>> agentwiki-launcher-managed (do not edit by hand)";
const END_MARKER = "# <<< agentwiki-launcher-managed";

export function writeCodexAgentWikiMcp(opts: {
  url: string;
  token: string;
}): void {
  const configPath = join(homedir(), ".codex", "config.toml");
  let raw: string;
  try {
    raw = readFileSync(configPath, "utf-8");
  } catch {
    raw = "";
  }
  const stripped = stripManagedBlock(raw);
  const block = renderBlock(opts);
  const next =
    (stripped.endsWith("\n") || stripped === "" ? stripped : stripped + "\n") +
    block;
  const tmp = `${configPath}.agw-tmp-${process.pid}`;
  try {
    writeFileSync(tmp, next, { mode: 0o600 });
    renameSync(tmp, configPath);
  } catch (e) {
    console.error(
      "[agentwiki-launcher] could not write ~/.codex/config.toml — codex won't see agent-wiki MCP:",
      e instanceof Error ? e.message : e,
    );
  }
}

function stripManagedBlock(raw: string): string {
  // Remove ANY ``[mcp_servers.agent-wiki]`` table (with its keys and
  // sub-tables) and any orphan START/END markers. Catches unmarked
  // blocks from partial writes too — TOML would otherwise reject the
  // appended marked block as a duplicate key.
  const lines = raw.split("\n");
  const out: string[] = [];
  const agentWikiTable = /^\[mcp_servers\.agent-wiki(?:\.[^\]]+)?\]\s*$/;
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line === START_MARKER || line === END_MARKER) {
      i++;
      continue;
    }
    if (agentWikiTable.test(line)) {
      // Skip table header + all its keys until the next ``[`` (next
      // table header) or EOF.
      i++;
      while (i < lines.length && !lines[i].startsWith("[")) {
        i++;
      }
      continue;
    }
    out.push(line);
    i++;
  }
  return out.join("\n");
}

function renderBlock(opts: { url: string; token: string }): string {
  // Codex's HTTP MCP config authenticates via
  // ``bearer_token_env_var = "<NAME>"`` — it reads the token from the
  // named env var at handshake. The matching env var is set on the
  // codex manifest's ``env`` block so the wrapper exports it before
  // launch. ``opts.token`` is unused in the file (env-driven) but kept
  // in the signature for symmetry with the claude path.
  void opts.token;
  const url = tomlString(opts.url);
  return (
    `${START_MARKER}\n` +
    `[mcp_servers.agent-wiki]\n` +
    `url = ${url}\n` +
    `bearer_token_env_var = "AGENTWIKI_MCP_TOKEN"\n` +
    `${END_MARKER}\n`
  );
}

function tomlString(s: string): string {
  return `"${s.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
}
