// Helpers for converting between the schedule-trigger UI form state and
// the canonical 5-field cron string stored on the trigger row.
//
// The friendly presets cover the cases users typically want; "custom"
// drops into the Advanced raw-cron field. ``describeCron`` is used in
// the trigger list to render schedule timing in human form.

export type FrequencyPreset =
  | "every_15_min"
  | "every_30_min"
  | "hourly"
  | "every_6_hours"
  | "daily"
  | "weekly"
  | "monthly"
  | "custom";

export interface ScheduleParts {
  preset: FrequencyPreset;
  hour: number; // 0-23, used for daily/weekly/monthly
  minute: number; // 0-59, used for daily/weekly/monthly
  dayOfWeek: number; // 0=Sun … 6=Sat, used for weekly
  dayOfMonth: number; // 1-31, used for monthly
}

export const PRESET_OPTIONS: { value: FrequencyPreset; label: string }[] = [
  { value: "every_15_min", label: "Every 15 minutes" },
  { value: "every_30_min", label: "Every 30 minutes" },
  { value: "hourly", label: "Every hour, on the hour" },
  { value: "every_6_hours", label: "Every 6 hours" },
  { value: "daily", label: "Daily at a specific time" },
  { value: "weekly", label: "Weekly" },
  { value: "monthly", label: "Monthly" },
  { value: "custom", label: "Custom (advanced)" },
];

export const WEEKDAY_NAMES = [
  "Sunday",
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
];

export function defaultScheduleParts(): ScheduleParts {
  return {
    preset: "every_15_min",
    hour: 9,
    minute: 0,
    dayOfWeek: 1,
    dayOfMonth: 1,
  };
}

export function partsToCron(p: ScheduleParts, customCron: string): string {
  const m = String(p.minute);
  const h = String(p.hour);
  const d = String(p.dayOfMonth);
  const dow = String(p.dayOfWeek);
  switch (p.preset) {
    case "every_15_min":
      return "*/15 * * * *";
    case "every_30_min":
      return "*/30 * * * *";
    case "hourly":
      return "0 * * * *";
    case "every_6_hours":
      return "0 */6 * * *";
    case "daily":
      return `${m} ${h} * * *`;
    case "weekly":
      return `${m} ${h} * * ${dow}`;
    case "monthly":
      return `${m} ${h} ${d} * *`;
    case "custom":
      return customCron.trim();
  }
}

const DAILY_RE = /^(\d+)\s+(\d+)\s+\*\s+\*\s+\*$/;
const WEEKLY_RE = /^(\d+)\s+(\d+)\s+\*\s+\*\s+(\d+)$/;
const MONTHLY_RE = /^(\d+)\s+(\d+)\s+(\d+)\s+\*\s+\*$/;

export function cronToParts(cron: string | null): ScheduleParts {
  const fallback = defaultScheduleParts();
  if (!cron) return fallback;
  const trimmed = cron.trim();
  if (trimmed === "*/15 * * * *")
    return { ...fallback, preset: "every_15_min" };
  if (trimmed === "*/30 * * * *")
    return { ...fallback, preset: "every_30_min" };
  if (trimmed === "0 * * * *") return { ...fallback, preset: "hourly" };
  if (trimmed === "0 */6 * * *")
    return { ...fallback, preset: "every_6_hours" };
  const daily = DAILY_RE.exec(trimmed);
  if (daily) {
    return { ...fallback, preset: "daily", minute: +daily[1], hour: +daily[2] };
  }
  const weekly = WEEKLY_RE.exec(trimmed);
  if (weekly) {
    return {
      ...fallback,
      preset: "weekly",
      minute: +weekly[1],
      hour: +weekly[2],
      dayOfWeek: +weekly[3] % 7,
    };
  }
  const monthly = MONTHLY_RE.exec(trimmed);
  if (monthly) {
    return {
      ...fallback,
      preset: "monthly",
      minute: +monthly[1],
      hour: +monthly[2],
      dayOfMonth: +monthly[3],
    };
  }
  return { ...fallback, preset: "custom" };
}

function pad2(n: number): string {
  return n.toString().padStart(2, "0");
}

export function describeCron(cron: string | null, tz?: string | null): string {
  if (!cron) return "—";
  const trimmed = cron.trim();
  const tzSuffix = tz ? ` (${tz})` : "";
  if (trimmed === "*/15 * * * *") return `Every 15 minutes${tzSuffix}`;
  if (trimmed === "*/30 * * * *") return `Every 30 minutes${tzSuffix}`;
  if (trimmed === "0 * * * *") return `Every hour, on the hour${tzSuffix}`;
  if (trimmed === "0 */6 * * *") return `Every 6 hours${tzSuffix}`;
  const daily = DAILY_RE.exec(trimmed);
  if (daily) return `Daily at ${pad2(+daily[2])}:${pad2(+daily[1])}${tzSuffix}`;
  const weekly = WEEKLY_RE.exec(trimmed);
  if (weekly) {
    const day = WEEKDAY_NAMES[+weekly[3] % 7];
    return `Weekly on ${day} at ${pad2(+weekly[2])}:${pad2(+weekly[1])}${tzSuffix}`;
  }
  const monthly = MONTHLY_RE.exec(trimmed);
  if (monthly) {
    return `Monthly on day ${+monthly[3]} at ${pad2(+monthly[2])}:${pad2(+monthly[1])}${tzSuffix}`;
  }
  return `${trimmed}${tzSuffix}`;
}

// Convert a UTC ISO string to a value suitable for ``<input type="datetime-local">``
// (which interprets values as the browser's local time). Returns "" when the
// input is null or unparseable.
export function utcIsoToLocalInput(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return (
    `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}` +
    `T${pad2(d.getHours())}:${pad2(d.getMinutes())}`
  );
}

// Convert a ``<input type="datetime-local">`` value (browser-local clock
// time) to an ISO 8601 UTC string. Returns null when empty.
export function localInputToUtcIso(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const d = new Date(trimmed);
  if (Number.isNaN(d.getTime())) return null;
  return d.toISOString();
}

export function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone;
  } catch {
    return "UTC";
  }
}

/** The timezone to render times in: the user's explicit choice, else their
 * browser's local zone. `null`/undefined means "not chosen" → local. */
export function effectiveTimezone(tz: string | null | undefined): string {
  return tz ?? browserTimezone();
}

export function listTimezones(): string[] {
  try {
    if (typeof Intl.supportedValuesOf === "function") {
      const tzs = Intl.supportedValuesOf("timeZone");
      return [...tzs].sort();
    }
  } catch {
    // fall through
  }
  return [browserTimezone(), "UTC"];
}
