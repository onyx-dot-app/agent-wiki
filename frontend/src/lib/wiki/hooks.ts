import useSWR, { mutate } from "swr";

import { SWR_KEYS } from "@/lib/swr-keys";
import { getDeletedTombstone } from "@/lib/trash";
import { resolveDocId, resolveIds } from "@/lib/wikiHref";
import {
  patchUpdatePolicy,
  type UpdatePolicyPatch,
  type UpdatePolicyResponse,
} from "@/lib/updatePolicy";
import type { ListResponse, UpdateHealth } from "@/lib/wiki/types";

/** One cache entry per path, so a write reflects everywhere. `null` disables. */
export function useUpdatePolicy(path: string | null) {
  const key = path ? SWR_KEYS.updatePolicy(path) : null;
  const { data, error, isLoading } = useSWR<UpdatePolicyResponse>(key, {
    // Callers gate on a loaded policy, not isLoading, so keeping previous data
    // would PATCH one scope's values against another's path.
    keepPreviousData: false,
  });
  return { policy: data ?? null, error: error as Error | undefined, isLoading };
}

/** Only the toggles merge. `!= null` so a clear-to-null patch doesn't guess
 * the inherited value. */
function merged(
  current: UpdatePolicyResponse,
  patch: UpdatePolicyPatch,
): UpdatePolicyResponse {
  const effective = { ...current.effective };
  if (patch.ingestion_auto_update_disabled != null)
    effective.ingestion_auto_update_disabled =
      patch.ingestion_auto_update_disabled;
  if (patch.ai_management_allowed != null)
    effective.ai_management_allowed = patch.ai_management_allowed;
  return { ...current, effective };
}

/** PATCH through the shared cache. The toggle flips immediately and snaps back
 * if the request fails. */
export async function saveUpdatePolicy(
  path: string,
  patch: UpdatePolicyPatch,
  current: UpdatePolicyResponse,
): Promise<void> {
  await mutate<UpdatePolicyResponse>(
    SWR_KEYS.updatePolicy(path),
    () => patchUpdatePolicy(path, patch),
    {
      optimisticData: merged(current, patch),
      rollbackOnError: true,
      revalidate: false,
    },
  );
}

/** The full flat wiki listing — backs the folder Explorer and the New Doc
 * destination picker. */
export function useWikiTree() {
  const { data, error, isLoading, mutate } = useSWR<ListResponse>(
    SWR_KEYS.wikiTree,
  );
  return { entries: data?.entries ?? [], error, isLoading, mutate };
}

/** Tombstone info for a deleted page/folder by its original path, for the
 * deleted-URL panel. Enabled whenever `path` is non-null. */
export function useDeletedTombstone(path: string | null) {
  const { data, error, isLoading } = useSWR(
    path ? SWR_KEYS.deletedTombstone(path) : null,
    () => getDeletedTombstone(path as string),
    { revalidateOnFocus: false },
  );
  return { entry: data, error, isLoading };
}

/** Resolve a doc id to its current binding (path/kind/deleted state).
 * Enabled whenever `id` is non-null. */
export function useDocIdResolve(id: string | null) {
  const { data, error, isLoading } = useSWR(
    id ? SWR_KEYS.docIdResolve(id) : null,
    () => resolveDocId(id as string),
    { revalidateOnFocus: false },
  );
  return { resolved: data, error, isLoading };
}

/** Resolve a single path to its live id. Enabled whenever `path` is non-null.
 * `tag` namespaces the SWR cache key — callers resolving different concerns
 * off the same `resolveIds` endpoint pass distinct tags (e.g. "id-fallback"
 * vs "wiki-path-id"); keep any tag in sync with the key matcher in
 * `wikiHref.ts:revalidateWiki`. */
export function usePathToId(tag: string, path: string | null) {
  const { data, error, isLoading } = useSWR(
    path ? SWR_KEYS.pathToId(tag, path) : null,
    () => resolveIds([path as string]).then((m) => m[path as string] ?? null),
    { revalidateOnFocus: false },
  );
  return { id: data ?? null, error, isLoading };
}

/** Auto-update health as a live SWR subscription. Polls so the 24h count and
 * the too-frequent-update banner reflect ingestion writes without a manual
 * reload — the count moves slowly, so a coarser interval than the doc body's
 * is plenty. Pass `null` to disable (no path selected). */
export function useUpdateHealth(path: string | null) {
  const key = path ? SWR_KEYS.updateHealth(path) : null;
  const { data, error, isLoading, mutate } = useSWR<UpdateHealth>(key, {
    refreshInterval: 15_000,
  });
  return {
    health: data ?? null,
    error: error as Error | undefined,
    isLoading,
    refresh: mutate,
  };
}
