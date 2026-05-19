/**
 * Renders the agent-wiki MCP block in codex's TOML format as a
 * standalone tmpfile. The live path is ``codex_mcp_config.ts``, which
 * edits ``~/.codex/config.toml`` directly (codex reads only that
 * file); this tmpfile renders the same block so the auth shape stays
 * in lockstep if codex ever grows a ``--mcp-config`` flag.
 *
 * Auth uses ``bearer_token_env_var`` — do not switch to a
 * ``[mcp_servers.agent-wiki.headers]`` sub-table, codex ignores it.
 */
export function renderCodexToml(opts: { url: string; token: string }): string {
  // ``token`` isn't embedded — codex reads the actual value from the
  // env var named below. The wrapper script exports
  // ``AGENTWIKI_MCP_TOKEN`` from the manifest's ``env`` block.
  void opts.token;
  return [
    `[mcp_servers.agent-wiki]`,
    `url = "${opts.url}"`,
    `bearer_token_env_var = "AGENTWIKI_MCP_TOKEN"`,
    ``,
  ].join("\n");
}
