"use client";

import { SWRConfig } from "swr";
import type { ReactNode } from "react";

import { apiFetch } from "@/lib/api";

/** Default fetcher: SWR keys are API paths (e.g. "/events?kind=trigger.fire").
 * Pass a tuple `[path, init]` to send a non-GET. */
function fetcher(key: string | readonly [string, RequestInit?]): Promise<unknown> {
  if (typeof key === "string") return apiFetch(key);
  const [path, init] = key;
  return apiFetch(path, init);
}

/** App-wide SWR defaults.
 *
 * - `revalidateOnFocus` + `revalidateOnReconnect`: refetch when the user
 *   returns to the tab or regains network. Also refetches on mount when
 *   the cached entry is older than `dedupingInterval`.
 * - `keepPreviousData`: when the key changes (filters, pagination), keep
 *   showing the previous payload until the new one resolves. Combined
 *   with cache survival across navigations, this is what kills the
 *   empty-state flash.
 * - `dedupingInterval`: collapse duplicate requests fired within 2s
 *   (e.g. two components mounting at once).
 * - `errorRetryCount`: 3 is enough for transient blips; we don't want a
 *   broken endpoint hammered on every focus.
 */
export function SWRProvider({ children }: { children: ReactNode }) {
  return (
    <SWRConfig
      value={{
        fetcher,
        revalidateOnFocus: true,
        revalidateOnReconnect: true,
        keepPreviousData: true,
        dedupingInterval: 2000,
        errorRetryCount: 3,
      }}
    >
      {children}
    </SWRConfig>
  );
}
