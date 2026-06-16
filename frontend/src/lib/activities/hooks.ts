import useSWR from "swr";
import type { AppEvent } from "./types";

function eventsPath(opts: { kind?: string; limit?: number }): string {
  const qs = new URLSearchParams();
  if (opts.kind) qs.set("kind", opts.kind);
  if (opts.limit) qs.set("limit", String(opts.limit));
  return `/events${qs.toString() ? `?${qs}` : ""}`;
}

export function useEvents(
  opts: { kind?: string; limit?: number } = {},
  swrConfig?: { refreshInterval?: number },
) {
  const { data, error, isLoading, isValidating, mutate } = useSWR<{
    events: AppEvent[];
  }>(eventsPath(opts), swrConfig);
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
