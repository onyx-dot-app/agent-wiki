/**
 * Renders the agent-wiki MCP block in codex's TOML format.
 *
 * Codex's current shipped version (as of phase 3) reads MCP servers
 * only from ``~/.codex/config.toml`` and authenticates via
 * ``bearer_token_env_var`` — the helper writes that file via
 * ``codex_mcp_config.ts``. This module renders the same block as a
 * standalone tmpfile for two reasons:
 *
 *   1. If codex ever grows a ``--mcp-config`` flag, the helper can
 *      pass the tmpfile path and the auth shape will already match
 *      what codex expects.
 *   2. The cli routes ``mcp_config_format === "codex_toml"`` through
 *      both ``writeCodexAgentWikiMcp`` (real auth via env) and
 *      ``writeSecureTmpfile(renderCodexToml(...))`` (forward-compat
 *      tmpfile). Keeping them in lockstep avoids a divergence where
 *      the tmpfile would 401 if codex ever picked it up.
 *
 * The earlier ``[mcp_servers.agent-wiki.headers]`` sub-table was a
 * dead end — codex ignored it and the backend returned 401 because
 * no bearer reached the initialize call. Don't reintroduce it.
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
