import { apiFetch } from "./api";

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

export function fetchHealth(): Promise<HealthResponse> {
  return apiFetch<HealthResponse>("/health");
}
