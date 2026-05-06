export interface Document {
  id: string;
  path: string;
  title: string | null;
  updated_at: string;
}

export interface Trigger {
  id: string;
  owner_user_id: string;
  scope_path: string;
  kind: "delta" | "schedule";
  nl_description: string;
  action: { kind: "webhook" | "http" | "agent_message"; config: Record<string, unknown> };
  schedule_cron: string | null;
  enabled: boolean;
  created_at: string;
}

export interface Event {
  id: number;
  ts: string;
  kind: string;
  actor: string | null;
  target: string | null;
  payload: Record<string, unknown>;
}
