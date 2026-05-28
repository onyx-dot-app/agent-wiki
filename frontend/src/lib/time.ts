// Relative-time formatting. Single source for "N minutes ago" style
// strings across the app — both the compact form used in dense rows
// (active-agents bar, document list) and the verbose form used in the
// history activity feed.

export type RelativeTimeStyle = "short" | "long";

const UNITS: { limit: number; secs: number; short: string; long: string }[] = [
  { limit: 90, secs: 60, short: "m", long: "minute" },
  { limit: 3600, secs: 60, short: "m", long: "minute" },
  { limit: 86400, secs: 3600, short: "h", long: "hour" },
  { limit: 2592000, secs: 86400, short: "d", long: "day" },
  { limit: 31536000, secs: 2592000, short: "mo", long: "month" },
  { limit: Infinity, secs: 31536000, short: "y", long: "year" },
];

/**
 * Format an ISO timestamp as a relative-time string.
 *
 * - `"short"` → `"5m ago"`, `"3h ago"`, `"2d ago"` (compact, for dense UI)
 * - `"long"`  → `"5 minutes ago"`, `"3 hours ago"` (verbose, for the
 *   history activity feed; matches the Onyx Wiki mock)
 *
 * Future timestamps render as `"in 5m"` / `"in 5 minutes"`. Anything
 * under 45s is `"just now"`. Invalid input echoes back the raw string.
 */
export function relativeTime(
  iso: string,
  style: RelativeTimeStyle = "long",
): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;

  const diffMs = d.getTime() - Date.now();
  const future = diffMs > 0;
  const sec = Math.round(Math.abs(diffMs) / 1000);

  if (sec < 45) return "just now";

  const unit = UNITS.find((u) => sec < u.limit) ?? UNITS[UNITS.length - 1];
  const value = Math.max(1, Math.round(sec / unit.secs));

  const phrase =
    style === "short"
      ? `${value}${unit.short}`
      : `${value} ${unit.long}${value === 1 ? "" : "s"}`;

  return future ? `in ${phrase}` : `${phrase} ago`;
}

/** Absolute local timestamp — for tooltips / precise hover detail. */
export function absoluteTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}
