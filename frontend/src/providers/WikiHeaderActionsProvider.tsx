"use client";

import { createContext, useContext, useState, type ReactNode } from "react";

// Lets a wiki route render page-level chrome into the shared app shell instead
// of duplicating it inside the scrollable content:
//
//   - header actions  → the single pinned header (`WikiHeader`)
//   - agents bar      → a shrink-0 row between the header and main content
//   - side panels     → a permanent right column (sibling of the main content)
//
// Each surface exposes a host DOM element through context; routes portal their
// content into it (see `useHeaderActionsHost` / `useAgentsBarHost` /
// `useRightPanelHost`). A portal — rather than passing the nodes up as context
// state — keeps the content re-rendering live with the route's own state
// without bouncing updates back through the provider (which would loop).
interface ChromeHost {
  /** The host element, or null before it mounts. */
  el: HTMLElement | null;
  /** Ref callback the shell passes to its host element. */
  setEl: (el: HTMLElement | null) => void;
}

const HeaderActionsContext = createContext<ChromeHost | null>(null);
const HeaderCrumbContext = createContext<ChromeHost | null>(null);
const AgentsBarContext = createContext<ChromeHost | null>(null);
const RightPanelContext = createContext<ChromeHost | null>(null);

export function WikiHeaderActionsProvider({
  children,
}: {
  children: ReactNode;
}) {
  const [headerEl, setHeaderEl] = useState<HTMLElement | null>(null);
  const [crumbEl, setCrumbEl] = useState<HTMLElement | null>(null);
  const [agentsEl, setAgentsEl] = useState<HTMLElement | null>(null);
  const [rightEl, setRightEl] = useState<HTMLElement | null>(null);
  return (
    <HeaderActionsContext.Provider value={{ el: headerEl, setEl: setHeaderEl }}>
      <HeaderCrumbContext.Provider value={{ el: crumbEl, setEl: setCrumbEl }}>
        <AgentsBarContext.Provider value={{ el: agentsEl, setEl: setAgentsEl }}>
          <RightPanelContext.Provider
            value={{ el: rightEl, setEl: setRightEl }}
          >
            {children}
          </RightPanelContext.Provider>
        </AgentsBarContext.Provider>
      </HeaderCrumbContext.Provider>
    </HeaderActionsContext.Provider>
  );
}

/** Read the pinned-header actions host. Null outside the provider. */
export function useHeaderActionsHost(): ChromeHost | null {
  return useContext(HeaderActionsContext);
}

/** Read the host sitting after the breadcrumb trail (version chip slot).
 * Null outside the provider. */
export function useHeaderCrumbHost(): ChromeHost | null {
  return useContext(HeaderCrumbContext);
}

/** Read the sub-header agents bar host. Null outside the provider. */
export function useAgentsBarHost(): ChromeHost | null {
  return useContext(AgentsBarContext);
}

/** Read the right-column panel host. Null outside the provider. */
export function useRightPanelHost(): ChromeHost | null {
  return useContext(RightPanelContext);
}
