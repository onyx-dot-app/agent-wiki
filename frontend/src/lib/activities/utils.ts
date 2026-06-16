export const NEW_ACTIVITY_CUTOFF_MS = 24 * 60 * 60 * 1000;

// SQLite's datetime('now') omits the T and Z; treat as UTC.
export function toEventIso(ts: string): string {
  return ts.includes("T") ? ts : `${ts.replace(" ", "T")}Z`;
}

export function isNewActivity(ts: string): boolean {
  return (
    Date.now() - new Date(toEventIso(ts)).getTime() < NEW_ACTIVITY_CUTOFF_MS
  );
}
