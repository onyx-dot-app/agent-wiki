import useSWR from "swr";

import { apiFetch } from "@/lib/api";

export type TriggerKind = "delta" | "schedule";

export interface TriggerAction {
  destination_config_id: string | null;
  message: string | null;
}

export interface TriggerActionInput {
  destination_config_id?: string | null;
  message: string;
}

export interface Trigger {
  id: string;
  owner_user_id: string;
  scope_path: string;
  kind: TriggerKind;
  nl_description: string;
  actions: TriggerAction[];
  enabled: boolean;
  created_at: string;
  last_edited_at: string;
  file_path: string | null;
  schedule_cron: string | null;
  schedule_timezone: string | null;
  schedule_start_at: string | null;
  schedule_last_fired_at: string | null;
}

export interface TriggerCommit {
  sha: string;
  author: string;
  ts: string;
  message: string;
  body?: string;
}

export interface TriggerCreateInput {
  scope_path: string;
  nl_description: string;
  actions: TriggerActionInput[];
  enabled?: boolean;
  kind?: TriggerKind;
  schedule_cron?: string | null;
  schedule_timezone?: string | null;
  schedule_start_at?: string | null;
}

export interface TriggerUpdateInput {
  scope_path?: string;
  nl_description?: string;
  actions?: TriggerActionInput[];
  enabled?: boolean;
  schedule_cron?: string | null;
  schedule_timezone?: string | null;
  schedule_start_at?: string | null;
}

export function useTriggers() {
  const { data, error, isLoading, mutate } = useSWR<{ triggers: Trigger[] }>(
    "/triggers",
  );
  return {
    triggers: data?.triggers ?? [],
    error: error as Error | undefined,
    isLoading,
    refresh: mutate,
  };
}

export function createTrigger(input: TriggerCreateInput): Promise<Trigger> {
  return apiFetch<Trigger>("/triggers", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateTrigger(
  id: string,
  patch: TriggerUpdateInput,
): Promise<Trigger> {
  return apiFetch<Trigger>(`/triggers/${id}`, {
    method: "PUT",
    body: JSON.stringify(patch),
  });
}

export function deleteTrigger(id: string): Promise<void> {
  return apiFetch<void>(`/triggers/${id}`, { method: "DELETE" });
}

export async function getTriggerHistory(id: string): Promise<TriggerCommit[]> {
  const r = await apiFetch<{ commits: TriggerCommit[] }>(
    `/triggers/${id}/history`,
  );
  return r.commits;
}

export interface TriggerVersion {
  scope_path: string;
  nl_description: string;
  actions: TriggerAction[];
  enabled: boolean;
  sha: string;
  path: string;
  kind: TriggerKind | null;
  schedule_cron: string | null;
  schedule_timezone: string | null;
  schedule_start_at: string | null;
}

export function getTriggerVersion(
  id: string,
  sha: string,
): Promise<TriggerVersion> {
  return apiFetch<TriggerVersion>(`/triggers/${id}/version/${sha}`);
}

export interface TriggerDestination {
  id: string;
  name: string;
  description: string;
}

export async function getTriggerDestinations(): Promise<TriggerDestination[]> {
  const r = await apiFetch<{ destinations: TriggerDestination[] }>(
    "/triggers/destinations",
  );
  return r.destinations;
}

export function useTriggerDestinations() {
  const { data } = useSWR<{ destinations: TriggerDestination[] }>(
    "/triggers/destinations",
  );
  return data?.destinations ?? [];
}

// ---- Destination configs (per-user typed delivery targets) ----

export interface DestinationConfig {
  id: string;
  type: string;
  name: string;
  has_secret: boolean;
  created_at: string | null;
}

export function useDestinationConfigs() {
  const { data, error, isLoading, mutate } = useSWR<{
    configs: DestinationConfig[];
  }>("/triggers/destination-configs");
  return {
    configs: data?.configs ?? [],
    error: error as Error | undefined,
    isLoading,
    refresh: mutate,
  };
}

export function createDestinationConfig(input: {
  type: string;
  name: string;
  secret?: string | null;
  config?: Record<string, unknown>;
}): Promise<DestinationConfig> {
  return apiFetch<DestinationConfig>("/triggers/destination-configs", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function deleteDestinationConfig(id: string): Promise<void> {
  return apiFetch<void>(`/triggers/destination-configs/${id}`, {
    method: "DELETE",
  });
}
