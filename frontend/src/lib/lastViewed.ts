// Tracks the last wiki path the user opened. Used by the
// "Last viewed" choice in personal settings to bring users back
// to where they were on next launch. Stored client-side only so
// the landing redirect can resolve instantly, before any API call.
// (The sidebar "Recents" list is server-side — see lib/recents.ts.)

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
