/**
 * HARDCODED allow-list of binaries the helper will spawn.
 *
 * Defense-in-depth against a compromised backend pushing a manifest
 * naming `rm` / `curl` / `bash -c …`. New tools land here by appending +
 * cutting a helper release.
 *
 * Path separators / parent-dir traversal rejected — binary must be an
 * unqualified name resolved through PATH.
 */
const ALLOWED = new Set(["claude", "codex"]);

export function isAllowed(binary: string): boolean {
  if (binary.includes("/") || binary.includes("\\")) return false;
  if (binary.includes("..")) return false;
  return ALLOWED.has(binary);
}

export function assertAllowed(binary: string): void {
  if (!isAllowed(binary)) {
    throw new Error(`binary_not_allowed: ${binary}`);
  }
}
