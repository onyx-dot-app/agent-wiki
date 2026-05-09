import useSWR from "swr";

import { apiFetch } from "@/lib/api";

/** Display form of a trigger scope path.
 *
 * Files (ending in `.md`) → `/full/path/to/file.md`.
 * Directories → `/full/path/to/dir/` (trailing slash signals dir-scope).
 * Root dir → `/`.
 *
 * If the result exceeds `maxLen`, the middle is replaced with `...` while
 * keeping the leading anchor segment and the final segment intact, e.g.
 * `/somepath/.../somefile.md`.
 */
export function formatScopePath(scope_path: string, maxLen = 60): string {
  const trimmed = scope_path.trim().replace(/^\/+|\/+$/g, "");
  if (trimmed === "" || trimmed === ".") return "/";
  const isFile = trimmed.endsWith(".md");
  const full = isFile ? `/${trimmed}` : `/${trimmed}/`;
  if (full.length <= maxLen) return full;

  const segs = trimmed.split("/");
  if (segs.length <= 2) return full;

  const first = segs[0];
  const last = segs[segs.length - 1];
  const candidate = isFile ? `/${first}/.../${last}` : `/${first}/.../${last}/`;
  if (candidate.length <= maxLen) return candidate;
  return isFile ? `/.../${last}` : `/.../${last}/`;
}

export interface Trigger {
  id: string;
  owner_user_id: string;
  scope_path: string;
  kind: "delta";
  nl_description: string;
  message: string | null;
  destination: string | null;
  enabled: boolean;
  created_at: string;
  last_edited_at: string;
  file_path: string | null;
}

export interface TriggerCommit {
  sha: string;
  author: string;
  ts: string;
  message: string;
  body?: string;
}

export interface TriggerCreateInput {
  scope_path: string;
  nl_description: string;
  message: string;
  destination?: string | null;
  enabled?: boolean;
}

export interface TriggerUpdateInput {
  scope_path?: string;
  nl_description?: string;
  message?: string;
  destination?: string | null;
  enabled?: boolean;
}

export function useTriggers() {
  const { data, error, isLoading, mutate } = useSWR<{ triggers: Trigger[] }>("/triggers");
  return {
    triggers: data?.triggers ?? [],
    error: error as Error | undefined,
    isLoading,
    refresh: mutate,
  };
}

export function createTrigger(input: TriggerCreateInput): Promise<Trigger> {
  return apiFetch<Trigger>("/triggers", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateTrigger(id: string, patch: TriggerUpdateInput): Promise<Trigger> {
  return apiFetch<Trigger>(`/triggers/${id}`, {
    method: "PUT",
    body: JSON.stringify(patch),
  });
}

export function deleteTrigger(id: string): Promise<void> {
  return apiFetch<void>(`/triggers/${id}`, { method: "DELETE" });
}

export async function getTriggerHistory(id: string): Promise<TriggerCommit[]> {
  const r = await apiFetch<{ commits: TriggerCommit[] }>(`/triggers/${id}/history`);
  return r.commits;
}

export interface TriggerVersion {
  scope_path: string;
  nl_description: string;
  message: string | null;
  destination: string | null;
  enabled: boolean;
  sha: string;
  path: string;
}

export function getTriggerVersion(id: string, sha: string): Promise<TriggerVersion> {
  return apiFetch<TriggerVersion>(`/triggers/${id}/version/${sha}`);
}
