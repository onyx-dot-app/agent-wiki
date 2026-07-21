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

/** One Auto Organize change proposal (a pending AI-initiated cleanup awaiting
 * human review). Mirrors `app/models/automanage.py:ProposalView`. */
export interface Proposal {
  id: number;
  op: string;
  status: string;
  source_paths: string[];
  target_paths: string[];
  summary: string;
  created_via: string;
  run_id: string | null;
  created_at: string;
}

/** Pending proposals touching `path` (a page or folder subtree) that the caller
 * can act on — the server write-scopes to edit access, so a read-only viewer
 * gets an empty list. Backs the Path-2 review banner on the page/folder.
 *
 * `enabled` (default true) gates the fetch — pass the caller's known write
 * capability to skip polling for viewers; the server remains the authority. */
export function useProposalsByPath(path: string, enabled = true) {
  const { data, error, isLoading, mutate } = useSWR<{ proposals: Proposal[] }>(
    enabled ? SWR_KEYS.automanageProposals(path) : null,
  );
  return {
    proposals: data?.proposals ?? [],
    error,
    isLoading,
    refresh: mutate,
  };
}

/** Fetch a single proposal's current state (used to surface the applied /
 * went-stale outcome after a human approves — execution is async). */
export function fetchProposal(id: number) {
  return apiFetch<Proposal>(`/automanage/proposals/${id}`);
}

/** Approve a pending proposal (the approver becomes the acting user; execution
 * is enqueued). Rejects with `ApiError` (409 if it's no longer pending). */
export function approveProposal(id: number) {
  return apiFetch<{ status: string }>(`/automanage/proposals/${id}/approve`, {
    method: "POST",
  });
}

/** Reject a pending proposal — a durable "don't propose this again". */
export function rejectProposal(id: number) {
  return apiFetch<{ status: string }>(`/automanage/proposals/${id}/reject`, {
    method: "POST",
  });
}
