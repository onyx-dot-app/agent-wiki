/**
 * Inject the agent-wiki MCP server block into ``~/.codex/config.toml``.
 *
 * Codex reads its MCP server list ONLY from ``~/.codex/config.toml`` —
 * there is no ``--mcp-config`` flag and no per-session config-file
 * override. Earlier versions of the helper rendered a ``codex_toml``
 * tmpfile but never wired it; codex launched with zero knowledge of
 * agent-wiki and fell back to local-filesystem edits when asked to
 * touch a wiki page.
 *
 * Approach:
 *   1. Read ``~/.codex/config.toml``.
 *   2. Strip any prior agent-wiki block our prior launches wrote (the
 *      marker comments below).
 *   3. Append a fresh marked block with the current session's URL +
 *      bearer token.
 *   4. Atomic write (tmp + rename).
 *
 * Cleanup: we don't strip the block on wrapper exit — the next launch
 * overwrites it. The token is short-lived (rotates per session) and
 * lives in the user's own home dir at 0600. ``token`` validation in the
 * manifest forbids putting it in argv, which is why we go through the
 * config file instead.
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
  // Aggressive cleanup: remove ANY ``[mcp_servers.agent-wiki]`` table
  // (with its keys and sub-tables) AND any orphan sentinel markers.
  // Earlier versions only paired START_MARKER + END_MARKER but a
  // partial write or older format could leave behind an unmarked
  // ``[mcp_servers.agent-wiki]`` block, which TOML rejects as a
  // duplicate key when we append our marked block at the bottom.
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
  // Codex's HTTP MCP config expects ``bearer_token_env_var = "<NAME>"``
  // — it reads the actual token from the named env var at handshake
  // time. The earlier ``[mcp_servers.<name>.headers]`` sub-table was
  // ignored, so codex sent the initialize request with no bearer and
  // the backend returned 401. The matching env var name is set on the
  // codex manifest's ``env`` block so the wrapper exports it before
  // launching codex. ``opts.token`` is unused in the file (env-driven)
  // but kept in the signature so callers stay symmetric with the
  // claude path.
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
