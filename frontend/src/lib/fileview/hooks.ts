"use client";

/** SWR hooks backing the wiki route views (`WikiPage`, `Explorer`, `NewDocView`,
 * `WikiTombstone`) — each wraps one `/wiki*` endpoint so its key, fetcher, and
 * shaped return live in one place instead of an inline `useSWR` call. */
import useSWR from "swr";

import { getDeletedTombstone } from "@/lib/trash";
import type { ListResponse } from "@/lib/fileview/types";
import { resolveDocId, resolveIds } from "@/lib/wikiHref";

/** The full flat wiki listing — backs the folder Explorer and the New Doc
 * destination picker. */
export function useWikiTree() {
  const { data, error, isLoading, mutate } = useSWR<ListResponse>("/wiki");
  return { entries: data?.entries ?? [], error, isLoading, mutate };
}

/** Tombstone info for a deleted page/folder by its original path, for the
 * deleted-URL panel. Enabled whenever `path` is non-null. */
export function useDeletedTombstone(path: string | null) {
  const { data, error, isLoading } = useSWR(
    path ? `/wiki/deleted?path=${encodeURIComponent(path)}` : null,
    () => getDeletedTombstone(path as string),
    { revalidateOnFocus: false },
  );
  return { entry: data, error, isLoading };
}

/** Resolve a doc id to its current binding (path/kind/deleted state).
 * Enabled whenever `id` is non-null. */
export function useDocIdResolve(id: string | null) {
  const { data, error } = useSWR(
    id ? `/wiki/id/${id}` : null,
    () => resolveDocId(id as string),
    { revalidateOnFocus: false },
  );
  return { resolved: data, error };
}

/** Resolve a single path to its live id. Enabled whenever `path` is non-null.
 * `tag` namespaces the SWR cache key — callers resolving different concerns
 * off the same `resolveIds` endpoint pass distinct tags (e.g. "id-fallback"
 * vs "wiki-path-id"); keep any tag in sync with the key matcher in
 * `wikiHref.ts:revalidateWiki`. */
export function usePathToId(tag: string, path: string | null) {
  const { data, error, isLoading } = useSWR(
    path ? [tag, path] : null,
    () => resolveIds([path as string]).then((m) => m[path as string] ?? null),
    { revalidateOnFocus: false },
  );
  return { id: data ?? null, error, isLoading };
}
