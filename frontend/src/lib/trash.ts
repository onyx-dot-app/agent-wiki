import useSWR from "swr";

import { apiFetch } from "@/lib/api";

/** One soft-deleted item in the Trash (a page or a folder root). Mirrors the
 * backend `TrashEntryView`. `path` is where it lived; a restore moves it back
 * there. `can_restore` reflects the caller's write access at the trash path. */
export interface TrashEntry {
  trash_id: string;
  path: string;
  kind: "page" | "folder";
  trashed_by: string;
  trashed_at: string;
  can_restore: boolean;
}

/** The Trash list, newest-first. ACL-filtered server-side. */
export function useTrash() {
  const { data, error, isLoading, mutate } = useSWR<{ items: TrashEntry[] }>(
    "/wiki/trash",
  );
  return {
    items: data?.items ?? [],
    error: error as Error | undefined,
    isLoading,
    refresh: mutate,
  };
}

interface RestoreResponse {
  path: string;
  sha: string;
  restored: string[];
}

/** Move a trashed item back to its original path. Rejects (409) if that path
 * is now occupied by a recreated page. */
export async function restoreTrashed(
  trashId: string,
): Promise<RestoreResponse> {
  return apiFetch<RestoreResponse>("/wiki/file/restore", {
    method: "POST",
    body: JSON.stringify({ trash_id: trashId }),
  });
}

/** Permanently remove a trashed item now, ahead of the 30-day auto-purge.
 * Irreversible from the app (content remains in git history). Requires the
 * same write access as restore; 403 otherwise. */
export async function purgeTrashed(trashId: string): Promise<void> {
  await apiFetch<void>(`/wiki/trash/${encodeURIComponent(trashId)}`, {
    method: "DELETE",
  });
}

/** Tombstone info for a deleted page/folder by its original path — the
 * most-recent Trash entry, for the deleted-URL panel. Rejects (404) when the
 * path isn't in Trash. */
export async function getDeletedTombstone(path: string): Promise<TrashEntry> {
  return apiFetch<TrashEntry>(`/wiki/deleted?path=${encodeURIComponent(path)}`);
}
