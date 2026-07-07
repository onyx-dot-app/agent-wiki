// Format helpers honoring the per-user timezone preference.
//
// Pull the active timezone from auth context (``useAuth().user?.settings.timezone``)
// and pass it in. We don't read auth here so this helper stays a pure
// function — easier to test and reuses cleanly in non-React contexts.

const DATE_FMT_CACHE: Record<string, Intl.DateTimeFormat> = {};

function formatter(timezone: string): Intl.DateTimeFormat {
  let f = DATE_FMT_CACHE[timezone];
  if (!f) {
    try {
      f = new Intl.DateTimeFormat(undefined, {
        timeZone: timezone,
        year: "numeric",
        month: "short",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      });
    } catch {
      // Fallback for an unknown zone — should not happen because the
      // backend validates on PUT, but keep the UI rendering anyway.
      f = new Intl.DateTimeFormat(undefined, {
        year: "numeric",
        month: "short",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      });
    }
    DATE_FMT_CACHE[timezone] = f;
  }
  return f;
}

export function formatInTimezone(
  value: string | Date | number,
  timezone: string,
): string {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return formatter(timezone).format(date);
}

/** Display form of a wiki/trigger scope path.
 *
 * Files (ending in `.md`) → `/full/path/to/file.md`.
 * Directories → `/full/path/to/dir/` (trailing slash signals dir-scope).
 * Root dir → `/`.
 *
 * If the result exceeds `maxLen`, the middle is replaced with `...` while
 * keeping the leading anchor segment and the final segment intact, e.g.
 * `/somepath/.../somefile.md`.
 */
export function formatScopePath(scope_path: string, maxLen = 60): string {
  const trimmed = scope_path.trim().replace(/^\/+|\/+$/g, "");
  if (trimmed === "" || trimmed === ".") return "/";
  const isFile = trimmed.endsWith(".md");
  const full = isFile ? `/${trimmed}` : `/${trimmed}/`;
  if (full.length <= maxLen) return full;

  const segs = trimmed.split("/");
  if (segs.length <= 2) return full;

  const first = segs[0];
  const last = segs[segs.length - 1];
  const candidate = isFile ? `/${first}/.../${last}` : `/${first}/.../${last}/`;
  if (candidate.length <= maxLen) return candidate;
  return isFile ? `/.../${last}` : `/.../${last}/`;
}

/** Compact relative time for fire lines and activity rows. */
export function formatRelative(iso: string | null | undefined): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const sec = Math.round((Date.now() - t) / 1000);
  if (sec < 60) return "just now";
  const min = Math.round(sec / 60);
  if (min < 60) return `${min} min ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr} hr ago`;
  const day = Math.round(hr / 24);
  if (day < 30) return `${day} day${day === 1 ? "" : "s"} ago`;
  return new Date(iso).toLocaleDateString();
}
