export interface AppEvent {
  id: number;
  ts: string;
  kind: string;
  actor: string | null;
  target: string | null;
  payload: Record<string, unknown>;
}
