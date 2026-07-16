import useSWR from "swr";

import { apiFetch } from "@/lib/api";
import { SWR_KEYS } from "@/lib/swr-keys";
import {
  createDestinationConfig,
  type DestinationConfig,
} from "@/lib/triggers";

export interface SlackConnectStatus {
  configured: boolean;
  connected: boolean;
  team_name: string | null;
  token_display: string | null;
  connect_url: string | null;
  muted: boolean;
  team_id: string | null;
}

export interface SlackChannel {
  id: string;
  name: string;
  is_private: boolean;
}

export function useSlackConnectStatus() {
  const { data, error, isLoading, mutate } = useSWR<SlackConnectStatus>(
    SWR_KEYS.slackConnectStatus,
  );
  return {
    status: data ?? null,
    error: error as Error | undefined,
    isLoading,
    refresh: mutate,
  };
}

export async function getSlackChannels(): Promise<SlackChannel[]> {
  const r = await apiFetch<{ channels: SlackChannel[] }>(
    "/connectors/slack/channels",
  );
  return r.channels;
}

export function disconnectSlack(): Promise<{ disconnected: boolean }> {
  return apiFetch<{ disconnected: boolean }>("/connectors/slack", {
    method: "DELETE",
  });
}

export type SlackTarget =
  | { kind: "channel"; id: string; name: string }
  | { kind: "dm" };

/** Reuse the matching destination config or create it, returning its id. */
export async function ensureSlackDestination(
  configs: DestinationConfig[],
  target: SlackTarget,
): Promise<{ id: string; created: boolean }> {
  const existing = configs.find((c) =>
    target.kind === "dm"
      ? c.config.dm === true
      : c.config.channel_id === target.id,
  );
  if (existing) return { id: existing.id, created: false };
  const created =
    target.kind === "dm"
      ? await createDestinationConfig({
          type: "slack",
          name: "DM me",
          config: { dm: true },
        })
      : await createDestinationConfig({
          type: "slack",
          name: `#${target.name}`,
          config: { channel_id: target.id, channel_name: target.name },
        });
  return { id: created.id, created: true };
}

export function setSlackMuted(
  muted: boolean,
  teamId?: string | null,
): Promise<SlackConnectStatus> {
  return apiFetch<SlackConnectStatus>("/connectors/slack/mute", {
    method: "PUT",
    body: JSON.stringify({ muted, team_id: teamId ?? undefined }),
  });
}
