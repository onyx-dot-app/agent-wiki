import { test } from "node:test";
import assert from "node:assert/strict";

import { buildSpawnCommand } from "../src/spawn.ts";
import type { Manifest } from "../src/manifest.ts";

function manifest(): Manifest {
  return {
    manifest_version: 1,
    id: "claude-code",
    name: "x",
    tagline: "x",
    icon_url: "/x",
    kind: "local_cli",
    cli_check: { binary: "claude" },
    mcp_config_format: "claude_json",
    first_turn_prompt_delivery: {
      method: "prompt_file_flag",
      flag: "--prompt-file",
    },
    launch: {
      binary: "claude",
      argv: ["--mcp-config", "${mcp_config_path}"],
      env: { AGENTWIKI_SESSION_ID: "${session_id}" },
      cwd: "${working_dir}",
    },
  };
}

test("argv interpolated + prompt-file flag appended", () => {
  const cmd = buildSpawnCommand({
    manifest: manifest(),
    token: "mcp_t",
    endpoint: "https://w",
    sessionId: "as_1",
    workingDir: "/home/u/p",
    mcpConfigPath: "/tmp/c.json",
    promptFilePath: "/tmp/p.txt",
    promptText: null,
  });
  assert.deepEqual(cmd.argv, [
    "--mcp-config",
    "/tmp/c.json",
    "--prompt-file",
    "/tmp/p.txt",
  ]);
  assert.equal(cmd.env.AGENTWIKI_SESSION_ID, "as_1");
  assert.equal(cmd.cwd, "/home/u/p");
});

test("disallowed binary rejected", () => {
  const m = manifest();
  m.launch!.binary = "rm";
  assert.throws(
    () =>
      buildSpawnCommand({
        manifest: m,
        token: "x",
        endpoint: "x",
        sessionId: "x",
        workingDir: null,
        mcpConfigPath: null,
        promptFilePath: null,
        promptText: null,
      }),
    /binary_not_allowed/,
  );
});

test("dirhash matches claude's slash-to-dash format", () => {
  const m = manifest();
  m.launch!.argv = ["--path", "${dirhash}"];
  const cmd = buildSpawnCommand({
    manifest: m,
    token: "x",
    endpoint: "x",
    sessionId: "x",
    workingDir: "/Users/nikolas/agent-wiki",
    mcpConfigPath: null,
    promptFilePath: null,
    promptText: null,
  });
  assert.equal(cmd.argv[1], "-Users-nikolas-agent-wiki");
});
