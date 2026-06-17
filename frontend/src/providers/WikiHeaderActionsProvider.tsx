"use client";

import { createContext, useContext, useState, type ReactNode } from "react";

// Lets a wiki route render page-level chrome into the shared app shell instead
// of duplicating it inside the scrollable content:
//
//   - header actions  → the single pinned header (`WikiHeader`)
//   - side panels     → a permanent right column (sibling of the main content)
//
// Each surface exposes a host DOM element through context; routes portal their
// content into it (see `useHeaderActionsHost` / `useRightPanelHost`). A portal —
// rather than passing the nodes up as context state — keeps the content
// re-rendering live with the route's own state without bouncing updates back
// through the provider (which would loop).
interface ChromeHost {
  /** The host element, or null before it mounts. */
  el: HTMLElement | null;
  /** Ref callback the shell passes to its host element. */
  setEl: (el: HTMLElement | null) => void;
}

const HeaderActionsContext = createContext<ChromeHost | null>(null);
const RightPanelContext = createContext<ChromeHost | null>(null);

export function WikiHeaderActionsProvider({
  children,
}: {
  children: ReactNode;
}) {
  const [headerEl, setHeaderEl] = useState<HTMLElement | null>(null);
  const [rightEl, setRightEl] = useState<HTMLElement | null>(null);
  return (
    <HeaderActionsContext.Provider value={{ el: headerEl, setEl: setHeaderEl }}>
      <RightPanelContext.Provider value={{ el: rightEl, setEl: setRightEl }}>
        {children}
      </RightPanelContext.Provider>
    </HeaderActionsContext.Provider>
  );
}

/** Read the pinned-header actions host. Null outside the provider. */
export function useHeaderActionsHost(): ChromeHost | null {
  return useContext(HeaderActionsContext);
}

/** Read the right-column panel host. Null outside the provider. */
export function useRightPanelHost(): ChromeHost | null {
  return useContext(RightPanelContext);
}
