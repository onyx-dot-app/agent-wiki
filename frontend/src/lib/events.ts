import useSWR from "swr";

export const NEW_ACTIVITY_CUTOFF_MS = 24 * 60 * 60 * 1000;

// SQLite's datetime('now') omits the T and Z; treat as UTC.
export function toEventIso(ts: string): string {
  return ts.includes("T") ? ts : `${ts.replace(" ", "T")}Z`;
}

export function isNewActivity(ts: string): boolean {
  return Date.now() - new Date(toEventIso(ts)).getTime() < NEW_ACTIVITY_CUTOFF_MS;
}

export interface AppEvent {
  id: number;
  ts: string;
  kind: string;
  actor: string | null;
  target: string | null;
  payload: Record<string, unknown>;
}

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
