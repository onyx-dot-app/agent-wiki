import useSWR from "swr";

export interface QueueHealth {
  name: string;
  // Per-state breakdown. `ready` is what a worker can pick up right now;
  // `delayed` is scheduled for a future fire time; `in_flight` is held
  // by a worker. `ready + delayed` is the figure the cap gates on. All
  // three are null when the per-queue read failed (see `error`).
  ready: number | null;
  delayed: number | null;
  in_flight: number | null;
  limit: number;
  ok: boolean;
  error: string | null;
}

export interface HealthResponse {
  status: "ok" | "degraded";
  queues: QueueHealth[];
}

export function useHealth(opts: { refreshIntervalMs?: number } = {}) {
  const { data, error, isLoading, isValidating, mutate } = useSWR<HealthResponse>("/health", {
    refreshInterval: opts.refreshIntervalMs,
  });
  return {
    health: data,
    error: error as Error | undefined,
    isLoading,
    isValidating,
    refresh: mutate,
  };
}
