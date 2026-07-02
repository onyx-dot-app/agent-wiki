import useSWR from "swr";

import { apiFetch } from "@/lib/api";

export interface SlackConnectStatus {
  configured: boolean;
  connected: boolean;
  team_name: string | null;
  token_display: string | null;
  connect_url: string | null;
}

export interface SlackChannel {
  id: string;
  name: string;
  is_private: boolean;
}

export function useSlackConnectStatus() {
  const { data, error, isLoading, mutate } =
    useSWR<SlackConnectStatus>("/connectors/slack");
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
