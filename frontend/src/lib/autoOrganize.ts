import { apiFetch } from "@/lib/api";

/** Kick off a whole-space detection sweep (admin only server-side). It runs on
 * the detection queue and emits change proposals; the request only enqueues. */
export function triggerSweep() {
  return apiFetch<{ status: string }>("/detection/sweep", { method: "POST" });
}
