import { apiFetch } from "@/lib/api";

export interface AppEvent {
  id: number;
  ts: string;
  kind: string;
  actor: string | null;
  target: string | null;
  payload: Record<string, unknown>;
}

export async function listEvents(opts: { kind?: string; limit?: number } = {}): Promise<AppEvent[]> {
  const qs = new URLSearchParams();
  if (opts.kind) qs.set("kind", opts.kind);
  if (opts.limit) qs.set("limit", String(opts.limit));
  const path = `/events${qs.toString() ? `?${qs}` : ""}`;
  const r = await apiFetch<{ events: AppEvent[] }>(path);
  return r.events;
}
