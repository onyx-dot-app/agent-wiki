// Tracks the last wiki path the user opened. Used by the
// "Last viewed" choice in personal settings to bring users back
// to where they were on next launch. Stored client-side only —
// not worth a DB write per page view.

export const LAST_WIKI_PATH_KEY = "agent-wiki:last-wiki-path";

export function rememberWikiPath(pathname: string) {
  if (typeof window === "undefined") return;
  if (!pathname.startsWith("/app/wiki")) return;
  try {
    window.localStorage.setItem(LAST_WIKI_PATH_KEY, pathname);
  } catch {
    /* ignore */
  }
}
