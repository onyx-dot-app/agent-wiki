import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { renderClaudeJson } from "../src/mcp_config/claude_json.ts";
import { renderCodexToml } from "../src/mcp_config/codex_toml.ts";
import { writeCodexAgentWikiMcp } from "../src/codex_mcp_config.ts";

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

test(
  "codex config writer creates dir when missing",
  { skip: process.platform === "win32" },
  () => {
    const tmpHome = mkdtempSync(join(tmpdir(), "codex-home-"));
    const prevHome = process.env.HOME;
    try {
      process.env.HOME = tmpHome;
      writeCodexAgentWikiMcp({
        url: "https://w/api/mcp",
        token: "mcp_xyz",
      });
      const configPath = join(tmpHome, ".codex", "config.toml");
      const contents = readFileSync(configPath, "utf-8");
      assert.match(contents, /\[mcp_servers\.agent-wiki\]/);
    } finally {
      if (prevHome === undefined) {
        delete process.env.HOME;
      } else {
        process.env.HOME = prevHome;
      }
      rmSync(tmpHome, { recursive: true, force: true });
    }
  },
);
