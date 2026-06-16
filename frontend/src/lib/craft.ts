"use client";

import useSWR from "swr";

import { apiFetch } from "@/lib/api";

// Mirrors app/models/craft.py:CraftConnectStatus.
export interface CraftConnectStatus {
  connected: boolean;
  onyx_user_email: string | null;
  token_display: string | null;
  expires_at: string | null;
  onyx_base_url: string | null;
  connect_url: string | null;
}

export interface CraftLaunchResponse {
  agent_session_id: string;
  status: string;
}

/** Connection status for the current user. `data` is undefined while the
 * feature is dark (the endpoint 404s) — callers treat that as "unavailable". */
export function useCraftConnect() {
  const { data, error, isLoading, mutate } = useSWR<CraftConnectStatus>(
    "/craft/connect",
  );
  return { status: data ?? null, error, isLoading, refresh: mutate };
}

/** Manual-PAT connect (v0): validate + store the user's pasted Onyx PAT. */
export function connectCraft(pat: string): Promise<CraftConnectStatus> {
  return apiFetch<CraftConnectStatus>("/craft/connect", {
    method: "POST",
    body: JSON.stringify({ pat }),
  });
}

export function disconnectCraft(): Promise<{ disconnected: boolean }> {
  return apiFetch<{ disconnected: boolean }>("/craft/connect", {
    method: "DELETE",
  });
}

export function craftLaunch(req: {
  wiki_path: string | null;
  message: string;
}): Promise<CraftLaunchResponse> {
  return apiFetch<CraftLaunchResponse>("/craft/launch", {
    method: "POST",
    body: JSON.stringify(req),
  });
}
