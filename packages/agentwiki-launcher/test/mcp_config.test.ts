import { test } from "node:test";
import assert from "node:assert/strict";

import { renderClaudeJson } from "../src/mcp_config/claude_json.ts";
import { renderCodexToml } from "../src/mcp_config/codex_toml.ts";

test("claude_json structure", () => {
  const s = renderClaudeJson({ url: "https://w/api/mcp", token: "mcp_xyz" });
  const parsed = JSON.parse(s);
  assert.deepEqual(parsed, {
    mcpServers: {
      "agent-wiki": {
        type: "http",
        url: "https://w/api/mcp",
        headers: { Authorization: "Bearer mcp_xyz" },
      },
    },
  });
});

test("codex_toml shape", () => {
  const s = renderCodexToml({ url: "https://w/api/mcp", token: "mcp_xyz" });
  assert.match(s, /\[mcp_servers\.agent-wiki\]/);
  assert.match(s, /url = "https:\/\/w\/api\/mcp"/);
  // Codex reads bearer from env var, not an inline Authorization
  // header sub-table (that path returned 401 from the backend).
  assert.match(s, /bearer_token_env_var = "AGENTWIKI_MCP_TOKEN"/);
  assert.doesNotMatch(s, /\[mcp_servers\.agent-wiki\.headers\]/);
});
