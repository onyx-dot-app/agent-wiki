import useSWR from "swr";

import { apiFetch } from "@/lib/api";
import { SWR_KEYS } from "@/lib/swr-keys";

/** Recurring-sweep cadence. `off` disables the scheduled sweep (manual sweeps
 * still work); daily/weekly run at a fixed off-peak time (see the backend). */
export type AutoOrganizeSchedule = "off" | "daily" | "weekly";

/** Kick off a whole-space detection sweep (admin only server-side). It runs on
 * the automanage queue and emits change proposals; the request only enqueues. */
export function triggerSweep() {
  return apiFetch<{ status: string }>("/automanage/sweep", { method: "POST" });
}

/** Org-wide Auto Organize settings — the master kill switch + sweep schedule. */
export interface AutoOrganizeSettings {
  enabled: boolean;
  schedule: AutoOrganizeSchedule;
  updated_at: string | null;
}

/** The Auto Organize settings (admin only server-side). `refresh` re-reads
 * after a mutation. */
export function useAutoOrganizeSettings() {
  const { data, error, isLoading, mutate } = useSWR<AutoOrganizeSettings>(
    SWR_KEYS.autoOrganizeSettings,
  );
  return { settings: data, error, isLoading, refresh: mutate };
}

/** Patch the settings — only the provided fields change. Returns the resulting
 * settings so the caller can seed the SWR cache without a revalidating GET. */
export function updateAutoOrganizeSettings(patch: {
  enabled?: boolean;
  schedule?: AutoOrganizeSchedule;
}) {
  return apiFetch<AutoOrganizeSettings>("/automanage/settings", {
    method: "PUT",
    body: JSON.stringify(patch),
  });
}
