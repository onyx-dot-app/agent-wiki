export interface AppEvent {
  id: number;
  ts: string;
  kind: string;
  actor: string | null;
  /** Resolved display name for `actor`, absent for system actors. */
  actor_display?: string | null;
  target: string | null;
  payload: Record<string, unknown>;
}
