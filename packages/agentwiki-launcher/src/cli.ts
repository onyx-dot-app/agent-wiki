import { parseLaunchUri } from "./uri.js";
import { getOrCreateMachineId } from "./machine_id.js";
import { exchange } from "./exchange.js";
import { parseManifest } from "./manifest.js";
import { writeSecureTmpfile } from "./tmpfile.js";
import { renderClaudeJson } from "./mcp_config/claude_json.js";
import { renderCodexToml } from "./mcp_config/codex_toml.js";
import { buildSpawnCommand } from "./spawn.js";
import { openInTerminalApp } from "./terminal/darwin.js";
import { markClaudeWorkspaceTrusted } from "./claude_trust.js";
import { writeCodexAgentWikiMcp } from "./codex_mcp_config.js";
import { markCodexProjectTrusted } from "./codex_trust.js";
import {
  endpointMatchesPinned,
  getPinnedEndpoint,
  setPinnedEndpoint,
} from "./endpoint_pin.js";

export async function main(argv: string[]): Promise<void> {
  const sub = argv[0];
  if (sub === "run") {
    await handleRun(argv[1] ?? "");
  } else if (sub === "probe-ack") {
    await handleProbeAck(argv[1] ?? "");
  } else if (sub === "dispatch") {
    // Called by the macOS .app stub. Routes by URI action so the stub
    // doesn't need to parse.
    const uri = argv[1] ?? "";
    if (uri.startsWith("agentwiki://probe")) {
      await handleProbeAck(uri);
    } else {
      await handleRun(uri);
    }
  } else if (sub === "set-endpoint") {
    handleSetEndpoint(argv[1] ?? "");
  } else {
    console.error(
      "usage: agentwiki-launcher (run <uri> | probe-ack <uri> | dispatch <uri> | set-endpoint <url>)",
    );
    process.exit(2);
  }
}

function handleSetEndpoint(url: string): void {
  if (!url) {
    console.error("usage: agentwiki-launcher set-endpoint <wiki-url>");
    process.exit(2);
  }
  setPinnedEndpoint(url);
  console.log(`agentwiki-launcher pinned to ${url}`);
}

async function handleRun(uri: string): Promise<void> {
  const parsed = parseLaunchUri(uri);
  if (parsed.action !== "run") throw new Error("expected run action");

  // Refuse if URI's endpoint doesn't match pinned.
  const pinned = getPinnedEndpoint();
  if (pinned === null) {
    console.error(
      "agentwiki-launcher not configured — run `agentwiki-launcher set-endpoint <wiki-url>` first.",
    );
    process.exit(2);
  }
  if (!endpointMatchesPinned(parsed.endpoint)) {
    console.error(
      `URI endpoint ${parsed.endpoint} does not match pinned ${pinned}; refusing.`,
    );
    process.exit(2);
  }

  const machineId = getOrCreateMachineId();
  const exchanged = await exchange(pinned, parsed.code, machineId);
  const manifest = parseManifest(exchanged.manifest);
  if (manifest.kind !== "local_cli") {
    throw new Error(`unsupported manifest kind ${manifest.kind}`);
  }

  const isResume = exchanged.payload.cli_session_id !== null;

  // MCP config URL is the MCP-server endpoint from the exchange
  // response (`<base>/api/mcp`), NOT the pinned wiki base URL. Using
  // the base would make claude try to talk MCP at the wrong path
  // and the connection would fail silently → claude exits immediately.
  const mcpUrl = exchanged.endpoint;
  let mcpConfigPath: string | null = null;
  if (manifest.mcp_config_format === "claude_json") {
    mcpConfigPath = writeSecureTmpfile(
      renderClaudeJson({ url: mcpUrl, token: exchanged.mcp_token }),
      ".json",
    );
  } else if (manifest.mcp_config_format === "codex_toml") {
    // Codex reads MCP servers only from ``~/.codex/config.toml`` — no
    // per-session config-file override. Write the agent-wiki block
    // directly into that file (marked with sentinel comments; prior
    // blocks are stripped first). ``writeSecureTmpfile`` is still kept
    // as a no-op for symmetry with the claude path, in case a future
    // codex version grows a ``--mcp-config`` flag.
    writeCodexAgentWikiMcp({ url: mcpUrl, token: exchanged.mcp_token });
    mcpConfigPath = writeSecureTmpfile(
      renderCodexToml({ url: mcpUrl, token: exchanged.mcp_token }),
      ".toml",
    );
  }

  let promptFilePath: string | null = null;
  let promptText: string | null = null;
  if (!isResume && exchanged.payload.first_turn_prompt !== null) {
    const method = manifest.first_turn_prompt_delivery?.method;
    if (method === "prompt_file_flag") {
      promptFilePath = writeSecureTmpfile(
        exchanged.payload.first_turn_prompt,
        ".txt",
      );
    } else if (method === "positional_arg") {
      promptText = exchanged.payload.first_turn_prompt;
    }
  }

  const cmd = buildSpawnCommand({
    manifest,
    token: exchanged.mcp_token,
    endpoint: pinned,
    sessionId: exchanged.payload.session_id,
    cliSessionId: exchanged.payload.cli_session_id,
    workingDir: exchanged.payload.working_dir,
    mcpConfigPath,
    promptFilePath,
    promptText,
    isResume,
  });

  // Claude's workspace-trust dialog is a separate gate from
  // ``--dangerously-skip-permissions``. When the user launched without
  // a working dir (and the manifest opted in via ``unscoped_workdir_argv``),
  // we pre-mark the fallback cwd as trusted so the agent doesn't block on
  // the prompt no one is at the keyboard to answer.
  if (
    exchanged.payload.working_dir === null &&
    (manifest.launch?.unscoped_workdir_argv?.length ?? 0) > 0
  ) {
    if (manifest.id === "claude-code") {
      markClaudeWorkspaceTrusted(cmd.cwd);
    } else if (manifest.id === "codex") {
      markCodexProjectTrusted(cmd.cwd);
    }
  }

  const tmpfiles: string[] = [];
  if (mcpConfigPath) tmpfiles.push(mcpConfigPath);
  if (promptFilePath) tmpfiles.push(promptFilePath);

  const closeUrl = new URL(
    `/api/agent-sessions/${exchanged.payload.session_id}/close`,
    pinned,
  ).toString();
  openInTerminalApp({
    ...cmd,
    tmpfilesToClean: tmpfiles,
    closeOnExit: { url: closeUrl, token: exchanged.mcp_token },
  });

  // Best-effort spawn-OK beacon so backend's 30s sweep
  // doesn't mark the session failed.
  await postSpawnOk(pinned, exchanged.payload.session_id, exchanged.mcp_token);
}

async function postSpawnOk(
  endpoint: string,
  sessionId: string,
  token: string,
): Promise<void> {
  try {
    await fetch(
      new URL(`/api/agent-sessions/${sessionId}/spawn-ok`, endpoint).toString(),
      {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      },
    );
  } catch {
    // best-effort
  }
}

async function handleProbeAck(uri: string): Promise<void> {
  const parsed = parseLaunchUri(uri);
  if (parsed.action !== "probe") throw new Error("expected probe action");

  // Helper posts probe-ack to the **pinned** backend endpoint. URI's
  // ``endpoint`` field carries the wiki page origin (in dev: frontend
  // dev-server URL; in prod: same as backend). Auto-pairing to the
  // URI's endpoint when nothing is pinned was a security hole — a
  // crafted ``agentwiki://probe?endpoint=https://attacker.com`` URI
  // would otherwise pin the attacker's host and POST the machine_id
  // there before any user confirmation. Require explicit
  // ``set-endpoint`` first.
  const pinned = getPinnedEndpoint();
  if (!pinned) {
    console.error(
      "agentwiki-launcher: no endpoint pinned. Run " +
        "`agentwiki-launcher set-endpoint <wiki-url>` first, then click " +
        "Test launcher manually in the wiki setup wizard again.",
    );
    process.exit(2);
  }

  await fetch(new URL("/api/launch/probe-ack", pinned).toString(), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      nonce: parsed.nonce,
      helper_port: 0,
      machine_id: getOrCreateMachineId(),
    }),
  });
}
