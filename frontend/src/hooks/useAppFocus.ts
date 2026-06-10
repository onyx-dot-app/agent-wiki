"use client";

import { useMemo } from "react";
import { usePathname } from "next/navigation";

export type AppFocusType =
  | "wiki"
  | "triggers"
  | "agents-and-actions"
  | "chats"
  | "none";

export class AppFocus {
  constructor(public readonly type: AppFocusType) {}

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

export function useAppFocus(): AppFocus {
  const pathname = usePathname();
  return useMemo(() => {
    if (pathname.startsWith("/app/wiki")) return new AppFocus("wiki");
    if (pathname.startsWith("/app/triggers")) return new AppFocus("triggers");
    if (pathname.startsWith("/app/agents-and-actions"))
      return new AppFocus("agents-and-actions");
    if (pathname.startsWith("/app/chats")) return new AppFocus("chats");
    return new AppFocus("none");
  }, [pathname]);
}
