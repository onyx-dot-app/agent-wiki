import { apiFetch } from "@/lib/api";

export interface Trigger {
  id: string;
  owner_user_id: string;
  scope_path: string;
  kind: "delta";
  nl_description: string;
  enabled: boolean;
  created_at: string;
}

export interface TriggerCreateInput {
  scope_path: string;
  nl_description: string;
  enabled?: boolean;
}

export interface TriggerUpdateInput {
  scope_path?: string;
  nl_description?: string;
  enabled?: boolean;
}

export async function listTriggers(): Promise<Trigger[]> {
  const r = await apiFetch<{ triggers: Trigger[] }>("/triggers");
  return r.triggers;
}

export function createTrigger(input: TriggerCreateInput): Promise<Trigger> {
  return apiFetch<Trigger>("/triggers", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function updateTrigger(id: string, patch: TriggerUpdateInput): Promise<Trigger> {
  return apiFetch<Trigger>(`/triggers/${id}`, {
    method: "PUT",
    body: JSON.stringify(patch),
  });
}

export function deleteTrigger(id: string): Promise<void> {
  return apiFetch<void>(`/triggers/${id}`, { method: "DELETE" });
}
