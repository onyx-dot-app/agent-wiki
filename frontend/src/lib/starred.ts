// Per-user starred (pinned) wiki docs, stored server-side
// (/wiki/starred) with a user-chosen order: starring appends at the
// end, drag-reorder PUTs the full new ordering. All writes update the
// SWR cache optimistically so the sidebar responds instantly, then
// roll back if the server rejects.

import { mutate } from "swr";

import { apiFetch } from "@/lib/api";

export const STARRED_KEY = "/wiki/starred";

export interface StarredDocsResponse {
  paths: string[];
  items?: { path: string; id: string | null }[];
}

export async function starDoc(path: string): Promise<void> {
  try {
    await mutate<StarredDocsResponse>(
      STARRED_KEY,
      async (current) => {
        await apiFetch<void>(STARRED_KEY, {
          method: "POST",
          body: JSON.stringify({ path }),
        });
        return appended(current, path);
      },
      {
        optimisticData: (current) => appended(current, path),
        rollbackOnError: true,
      },
    );
  } catch {
    /* rolled back — starring is best-effort UI state */
  }
}

export async function unstarDoc(path: string): Promise<void> {
  try {
    await mutate<StarredDocsResponse>(
      STARRED_KEY,
      async (current) => {
        await apiFetch<void>(
          `${STARRED_KEY}?path=${encodeURIComponent(path)}`,
          {
            method: "DELETE",
          },
        );
        return removed(current, path);
      },
      {
        optimisticData: (current) => removed(current, path),
        rollbackOnError: true,
      },
    );
  } catch {
    /* rolled back */
  }
}

export async function reorderStarred(paths: string[]): Promise<void> {
  try {
    await mutate<StarredDocsResponse>(
      STARRED_KEY,
      async () => {
        await apiFetch<void>(STARRED_KEY, {
          method: "PUT",
          body: JSON.stringify({ paths }),
        });
        return { paths };
      },
      { optimisticData: { paths }, rollbackOnError: true, revalidate: false },
    );
  } catch {
    /* rolled back */
  }
}

function appended(
  current: StarredDocsResponse | undefined,
  path: string,
): StarredDocsResponse {
  const rest = (current?.paths ?? []).filter((p) => p !== path);
  return { paths: [...rest, path] };
}

function removed(
  current: StarredDocsResponse | undefined,
  path: string,
): StarredDocsResponse {
  return { paths: (current?.paths ?? []).filter((p) => p !== path) };
}
