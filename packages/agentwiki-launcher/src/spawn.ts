import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

import { assertAllowed } from "./allowed_binaries.js";
import {
  interpolateArgv,
  interpolateEnv,
  type InterpolateContext,
} from "./interpolate.js";
import type { Manifest } from "./manifest.js";

interface BuildOpts {
  manifest: Manifest;
  token: string;
  endpoint: string;
  sessionId: string;
  cliSessionId?: string | null;
  workingDir: string | null;
  mcpConfigPath: string | null;
  promptFilePath: string | null;
  promptText: string | null;
  isResume?: boolean;
}

export interface SpawnCommand {
  binary: string;
  argv: string[];
  env: Record<string, string>;
  cwd: string;
}

/**
 * Note: claude's session dir uses cwd-with-slashes-replaced-by-dashes,
 * NOT sha256. Verified by inspecting ~/.claude/projects/ on a real install.
 */
function claudeDirhash(cwd: string): string {
  return cwd.replace(/\//g, "-");
}

function ensureScratchDir(sessionId: string): string {
  const dir = join(homedir(), "agent-wiki-runs", sessionId);
  mkdirSync(dir, { recursive: true, mode: 0o700 });
  return dir;
}

export function buildSpawnCommand(opts: BuildOpts): SpawnCommand {
  const block = opts.isResume ? opts.manifest.resume : opts.manifest.launch;
  if (!block) {
    throw new Error(
      opts.isResume ? "manifest has no resume" : "manifest has no launch",
    );
  }
  assertAllowed(block.binary);

  const unscoped = opts.workingDir === null;
  // When the user launches with no working dir, drop the agent into a
  // per-session scratch dir instead of $HOME. Two reasons:
  //   1. Anything under ``~/`` (e.g. ``~/.claude/``, ``~/.ssh``) is on
  //      claude's hardcoded sensitive-file list and prompts even with
  //      ``--permission-mode bypassPermissions`` — the agent can't write
  //      session memory without confirmation.
  //   2. Running from ``~/`` activates the user's global
  //      ``~/.claude/CLAUDE.md`` (PAI rules, etc.) which the wiki agent
  //      shouldn't inherit.
  // ``~/agent-wiki-runs/<session-id>/`` is created on demand; cleanup is
  // the user's call (leave for now — sessions are cheap directories).
  const cwd = unscoped
    ? ensureScratchDir(opts.sessionId)
    : (opts.workingDir as string);
  const dirhash = claudeDirhash(cwd);
  const ctx: InterpolateContext = {
    token: opts.token,
    endpoint: opts.endpoint,
    session_id: opts.sessionId,
    cli_session_id: opts.cliSessionId ?? null,
    working_dir: cwd,
    prompt_file_path: opts.promptFilePath,
    mcp_config_path: opts.mcpConfigPath,
    home: homedir(),
    dirhash,
  };

  let argv = interpolateArgv(block.argv, ctx);
  if (unscoped && block.unscoped_workdir_argv?.length) {
    argv = [...argv, ...block.unscoped_workdir_argv];
  }
  // First-turn-prompt delivery — three methods:
  //   - prompt_file_flag: append `<flag> <tmpfile-path>`
  //   - positional_arg:   append prompt text as the final argv element
  //   - stdin / none:     no argv mutation; wrapper handles stdin if needed
  if (!opts.isResume) {
    const delivery = opts.manifest.first_turn_prompt_delivery;
    if (delivery?.method === "prompt_file_flag" && opts.promptFilePath) {
      const flag = delivery.flag ?? "--prompt-file";
      argv = [...argv, flag, opts.promptFilePath];
    } else if (delivery?.method === "positional_arg" && opts.promptText) {
      argv = [...argv, opts.promptText];
    }
  }
  const env = interpolateEnv(block.env, ctx);
  return { binary: block.binary, argv, env, cwd };
}
