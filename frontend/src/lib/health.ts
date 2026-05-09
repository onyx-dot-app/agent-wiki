import useSWR from "swr";

export interface QueueHealth {
  name: string;
  size: number | null;
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
