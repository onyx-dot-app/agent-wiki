"use client";

import useSWR from "swr";

import { apiFetch, ApiError } from "@/lib/api";
import {
  createDestinationConfig,
  type DestinationConfig,
} from "@/lib/triggers";

// Mirrors app/models/craft.py:CraftConnectStatus.
export interface CraftConnectStatus {
  connected: boolean;
  onyx_user_email: string | null;
  // Redacted display hint only — never the raw PAT.
  token_hint: string | null;
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
  const { data, error, isLoading, mutate } =
    useSWR<CraftConnectStatus>("/craft/connect");
  // The endpoint 404s when Craft is dark; expose that as a typed flag so
  // callers don't have to cast `error` and check the status themselves.
  const isUnavailable = error instanceof ApiError && error.status === 404;
  return {
    status: data ?? null,
    error,
    isUnavailable,
    isLoading,
    refresh: mutate,
  };
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

/** Find-or-create the user's single craft destination config, returning its
 * id. The backend keeps craft configs one-per-user, so re-creation is safe. */
export async function ensureCraftDestination(
  configs: DestinationConfig[],
): Promise<string> {
  const existing = configs.find((c) => c.type === "craft");
  if (existing) return existing.id;
  const created = await createDestinationConfig({
    type: "craft",
    name: "Onyx Craft",
    config: {},
  });
  return created.id;
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

/** Map a craft session's `failure_reason` to a user-facing message. */
export function craftFailureMessage(reason: string | null): string {
  switch (reason) {
    case "auth_expired":
      return "Your Onyx connection expired — reconnect under Agents → Onyx Craft.";
    case "org_at_capacity":
      return "Onyx is at capacity — try again shortly.";
    case "rate_limited":
      return "Too many Craft launches — wait a moment.";
    case "onyx_unreachable":
      return "Couldn't reach Onyx — try again.";
    default:
      return "Couldn't start the Craft build.";
  }
}
