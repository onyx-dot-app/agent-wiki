import useSWR from "swr";

import { SWR_KEYS } from "@/lib/swr-keys";
import type { AppEvent } from "./types";

export function useEvents(
  opts: { kind?: string; limit?: number } = {},
  swrConfig?: { refreshInterval?: number },
) {
  const { data, error, isLoading, isValidating, mutate } = useSWR<{
    events: AppEvent[];
  }>(SWR_KEYS.events(opts), swrConfig);
  return {
    events: data?.events ?? [],
    error: error as Error | undefined,
    // True only on the very first load (no cached data yet). Background
    // revalidations show as `isValidating` but don't blank the list.
    isLoading,
    isValidating,
    refresh: mutate,
  };
}
