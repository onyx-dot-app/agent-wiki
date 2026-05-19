import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { randomBytes } from "node:crypto";

export function writeSecureTmpfile(data: string, suffix = ""): string {
  const dir = mkdtempSync(join(tmpdir(), "agw-"));
  const path = join(dir, randomBytes(8).toString("hex") + suffix);
  writeFileSync(path, data, { mode: 0o600 });
  return path;
}

export async function withSecureTmpfiles<K extends string, R>(
  files: Record<K, string>,
  fn: (paths: Record<K, string>) => Promise<R> | R,
): Promise<R> {
  const paths = {} as Record<K, string>;
  const dirs: string[] = [];
  for (const [k, data] of Object.entries(files) as [K, string][]) {
    const p = writeSecureTmpfile(data);
    paths[k] = p;
    const d = p.replace(/\/[^/]+$/, "");
    dirs.push(d);
  }
  try {
    return await fn(paths);
  } finally {
    for (const d of dirs) {
      try {
        rmSync(d, { recursive: true, force: true });
      } catch {
        // best-effort cleanup
      }
    }
  }
}
