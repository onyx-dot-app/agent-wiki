// Per-user recently-opened wiki docs, stored server-side
// (POST/GET /wiki/recents) so recents follow the user across
// browsers and devices. Only actual opens are recorded — docs
// updated by agents/triggers the user never visited don't appear.

import { mutate } from "swr";

import { apiFetch } from "@/lib/api";

export const RECENTS_KEY = "/wiki/recents";

export interface RecentDocsResponse {
  paths: string[];
}

/** Record that the user opened a wiki doc, then refresh any mounted
 * recents list (the sidebar) via SWR's global mutate. Best-effort —
 * a failed write must never break page viewing. */
export async function recordRecentDoc(path: string): Promise<void> {
  try {
    await apiFetch<void>(RECENTS_KEY, {
      method: "POST",
      body: JSON.stringify({ path }),
    });
    void mutate(RECENTS_KEY);
  } catch {
    /* ignore */
  }
}
