import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { randomUUID } from "node:crypto";

interface Opts {
  baseDir?: string;
}

export function getOrCreateMachineId(opts: Opts = {}): string {
  const base = opts.baseDir ?? join(homedir(), ".agentwiki");
  const path = join(base, "machine.id");
  if (existsSync(path)) {
    return readFileSync(path, "utf-8").trim();
  }
  mkdirSync(base, { recursive: true, mode: 0o700 });
  const id = randomUUID();
  writeFileSync(path, id, { mode: 0o600 });
  return id;
}
