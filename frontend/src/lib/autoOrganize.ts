import useSWR from "swr";

import { apiFetch } from "@/lib/api";
import { SWR_KEYS } from "@/lib/swr-keys";

/** Kick off a whole-space detection sweep (admin only server-side). It runs on
 * the detection queue and emits change proposals; the request only enqueues. */
export function triggerSweep() {
  return apiFetch<{ status: string }>("/detection/sweep", { method: "POST" });
}

/** Org-wide Auto Organize settings — the master kill switch. */
export interface AutoOrganizeSettings {
  enabled: boolean;
  updated_at: string | null;
}

/** The kill-switch settings (admin only server-side). `refresh` re-reads after
 * a mutation. */
export function useAutoOrganizeSettings() {
  const { data, error, isLoading, mutate } = useSWR<AutoOrganizeSettings>(
    SWR_KEYS.autoOrganizeSettings,
  );
  return { settings: data, error, isLoading, refresh: mutate };
}

/** Flip the master kill switch. Returns the resulting settings. */
export function updateAutoOrganizeEnabled(enabled: boolean) {
  return apiFetch<AutoOrganizeSettings>("/detection/settings", {
    method: "PUT",
    body: JSON.stringify({ enabled }),
  });
}
