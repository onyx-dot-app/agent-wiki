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

/** One detection run — the sweep history. Mirrors
 * `app/models/automanage.py:DetectionRunView`. */
export interface DetectionRun {
  id: string;
  trigger: string;
  status: "running" | "completed" | "failed" | string;
  triggered_by_user_id: string | null;
  paths_scanned: number;
  proposals_emitted: number;
  error: string | null;
  started_at: string;
  finished_at: string | null;
}

/** Recent detection runs, newest first (admin only server-side). `poll` turns
 * on a short refresh interval — used while a sweep the admin just started is
 * still enqueued/running, so the outcome shows up without a reload. */
/** The sweep rows of a runs payload — the one definition of "is a sweep"
 * shared by the hook and the snapshot-before-trigger path. */
export function sweepRuns(runs: DetectionRun[]): DetectionRun[] {
  return runs.filter((r) => r.trigger === "sweep");
}

export function useDetectionRuns(poll: boolean) {
  const { data, error, isLoading, mutate } = useSWR<{ runs: DetectionRun[] }>(
    SWR_KEYS.automanageRuns,
    { refreshInterval: poll ? 2000 : 0 },
  );
  const runs = data?.runs ?? [];
  // The runs API is trigger-agnostic. Every admin surface here is about
  // *sweeps* (the outcome watcher and the history table both key off sweep
  // rows), so expose the filtered view once — a future on_create/on_write
  // run completing mid-watch must never masquerade as the sweep outcome.
  // Server-side filtering belongs with the focused-trigger work.
  const sweeps = sweepRuns(runs);
  return { sweeps, error, isLoading, refresh: mutate };
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
  /** When a sweep last asserted this finding against current wiki state —
   * every sweep re-stamps carried pendings, so this reads as "confirmed by
   * the last scan". UTC DB text ("YYYY-MM-DD HH:MM:SS"). */
  last_emitted_at: string | null;
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
    // Don't auto-revalidate on focus/reconnect: an acted-on proposal leaves
    // `pending`, so a background revalidation would drop its row and cancel the
    // in-flight applied/went-stale poll. The banner refreshes explicitly (via
    // `refresh`) once a row has shown its outcome.
    { revalidateOnFocus: false, revalidateOnReconnect: false },
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

/** Dismiss a pending proposal — clears the card without reject's durable
 * veto; it may return if the finding is still (or again) true at a later
 * sweep. Rejects with `ApiError` (409 if it's no longer pending). */
export function dismissProposal(id: number) {
  return apiFetch<{ status: string }>(`/automanage/proposals/${id}/dismiss`, {
    method: "POST",
  });
}
