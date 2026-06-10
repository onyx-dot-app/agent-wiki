"use client";

import { useMemo } from "react";
import { usePathname } from "next/navigation";

export type AppFocusType =
  | "wiki"
  | "triggers"
  | "agents-and-actions"
  | "chats"
  | "none";

// Single source of truth for route → focus type mapping.
// To add a new nav entry: add it here + to NAV_ENTRIES. That's it.
const ROUTES: ReadonlyArray<{
  href: string;
  type: Exclude<AppFocusType, "none">;
}> = [
  { href: "/app/wiki", type: "wiki" },
  { href: "/app/triggers", type: "triggers" },
  { href: "/app/agents-and-actions", type: "agents-and-actions" },
  { href: "/app/chats", type: "chats" },
];

function decodeWikiPath(raw: string): string {
  return raw
    .split("/")
    .map((segment) => {
      try {
        return decodeURIComponent(segment);
      } catch {
        return segment;
      }
    })
    .join("/");
}

export class AppFocus {
  constructor(
    public readonly type: AppFocusType,
    /** Root href of the matched route (e.g. "/app/wiki"), null for "none". */
    public readonly rootHref: string | null,
    /** The wiki doc path (e.g. "folder/doc.md") when type === "wiki", null otherwise. */
    public readonly wikiPath: string | null = null,
  ) {}

  isWiki(): boolean {
    return this.type === "wiki";
  }

  isTriggers(): boolean {
    return this.type === "triggers";
  }

  isAgentsAndActions(): boolean {
    return this.type === "agents-and-actions";
  }

  isChats(): boolean {
    return this.type === "chats";
  }

  /** True when the focus is on a specific wiki doc path (used for recents highlighting). */
  matchesWikiPath(path: string): boolean {
    return this.isWiki() && this.wikiPath === path;
  }

  /** True when the given nav href matches the current focus (used for top nav tabs). */
  matchesHref(href: string): boolean {
    return this.rootHref === href;
  }
}

export function useAppFocus(): AppFocus {
  const pathname = usePathname();
  return useMemo(() => {
    for (const { href, type } of ROUTES) {
      if (pathname.startsWith(href)) {
        const wikiPath =
          type === "wiki" && pathname.length > href.length
            ? decodeWikiPath(pathname.slice(href.length + 1))
            : null;
        return new AppFocus(type, href, wikiPath);
      }
    }
    return new AppFocus("none", null);
  }, [pathname]);
}
