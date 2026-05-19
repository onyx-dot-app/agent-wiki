/**
 * Helper pins endpoint at install time; URI's endpoint
 * param is IGNORED. An attacker-crafted `agentwiki://run?...&endpoint=https://attacker.com/...`
 * is refused if it doesn't match the pinned value.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";

const PIN_PATH = join(homedir(), ".agentwiki", "endpoint.url");

export function getPinnedEndpoint(): string | null {
  if (!existsSync(PIN_PATH)) return null;
  return readFileSync(PIN_PATH, "utf-8").trim();
}

export function setPinnedEndpoint(url: string): void {
  // Validate URL well-formedness.
  new URL(url);
  mkdirSync(dirname(PIN_PATH), { recursive: true, mode: 0o700 });
  writeFileSync(PIN_PATH, url, { mode: 0o600 });
}

export function endpointMatchesPinned(candidate: string): boolean {
  const pinned = getPinnedEndpoint();
  if (pinned === null) return false;
  // The URI's `endpoint` carries the wiki's MCP URL
  // (`<base>/api/mcp`); the pin is the wiki base. Accept candidate if
  // it starts with the pinned base (same scheme + host + port).
  const pinnedBase = pinned.replace(/\/$/, "");
  const candBase = candidate.replace(/\/$/, "");
  return candBase === pinnedBase || candBase.startsWith(pinnedBase + "/");
}
