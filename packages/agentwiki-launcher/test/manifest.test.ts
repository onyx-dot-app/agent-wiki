import { test } from "node:test";
import assert from "node:assert/strict";

import { parseManifest } from "../src/manifest.ts";

function valid() {
  return {
    manifest_version: 1 as const,
    id: "claude-code",
    name: "x",
    tagline: "x",
    icon_url: "/x.svg",
    kind: "local_cli" as const,
    cli_check: { binary: "claude" },
    mcp_config_format: "claude_json" as const,
    first_turn_prompt_delivery: { method: "stdin" as const },
    launch: {
      binary: "claude",
      argv: ["--mcp-config", "${mcp_config_path}"],
      env: { AGENTWIKI_SESSION_ID: "${session_id}" },
      cwd: "${working_dir}",
    },
  };
}

test("valid manifest parses", () => {
  const m = parseManifest(valid());
  assert.equal(m.id, "claude-code");
});

test("unknown var rejected", () => {
  const bad = valid();
  bad.launch.argv.push("${not_a_var}");
  assert.throws(() => parseManifest(bad), /unknown interpolation var/);
});

test("token in argv rejected", () => {
  const bad = valid();
  bad.launch.argv.push("Bearer ${token}");
  assert.throws(() => parseManifest(bad), /token.*forbidden/);
});

test("first_turn_prompt anywhere rejected", () => {
  const bad = valid();
  bad.launch.argv.push("${first_turn_prompt}");
  assert.throws(() => parseManifest(bad), /first_turn_prompt.*forbidden/);
});

test("prompt_file_path in resume rejected", () => {
  const bad = valid() as ReturnType<typeof valid> & {
    resume: { binary: string; argv: string[]; env: Record<string, string> };
  };
  bad.resume = {
    binary: "claude",
    argv: ["--resume", "${prompt_file_path}"],
    env: {},
  };
  assert.throws(
    () => parseManifest(bad),
    /prompt_file_path.*forbidden in resume/,
  );
});

test("unknown manifest version rejected", () => {
  const bad = valid() as { manifest_version: number };
  bad.manifest_version = 2;
  assert.throws(() => parseManifest(bad), /unsupported manifest_version/);
});
