"use client";

import { useMemo } from "react";
import { usePathname } from "next/navigation";

export type AppFocusType =
  | "wiki"
  | "triggers"
  | "agents-and-actions"
  | "chats"
  | "none";

const WIKI_PREFIX = "/app/wiki/";

export class AppFocus {
  constructor(
    public readonly type: AppFocusType,
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
    switch (href) {
      case "/app/wiki":
        return this.isWiki();
      case "/app/triggers":
        return this.isTriggers();
      case "/app/agents-and-actions":
        return this.isAgentsAndActions();
      case "/app/chats":
        return this.isChats();
      default:
        return false;
    }
  }
}

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

export function useAppFocus(): AppFocus {
  const pathname = usePathname();
  return useMemo(() => {
    if (pathname.startsWith(WIKI_PREFIX))
      return new AppFocus("wiki", decodeWikiPath(pathname.slice(WIKI_PREFIX.length)));
    if (pathname === "/app/wiki") return new AppFocus("wiki", null);
    if (pathname.startsWith("/app/triggers")) return new AppFocus("triggers");
    if (pathname.startsWith("/app/agents-and-actions"))
      return new AppFocus("agents-and-actions");
    if (pathname.startsWith("/app/chats")) return new AppFocus("chats");
    return new AppFocus("none");
  }, [pathname]);
}
