"use client";

import {
  createContext,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

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
const AgentsBarContext = createContext<ChromeHost | null>(null);
const RightPanelContext = createContext<ChromeHost | null>(null);

/** Who currently owns the right rail. The rail fits one occupant (a
 *  doc-owned panel or the docked chat), so claiming it tells the other to
 *  yield. */
export type RailOwner = "panel" | "chat" | null;

interface RailOwnerState {
  owner: RailOwner;
  claim: (owner: RailOwner) => void;
}

const RailOwnerContext = createContext<RailOwnerState | null>(null);

export function WikiHeaderActionsProvider({
  children,
}: {
  children: ReactNode;
}) {
  const [headerEl, setHeaderEl] = useState<HTMLElement | null>(null);
  const [agentsEl, setAgentsEl] = useState<HTMLElement | null>(null);
  const [rightEl, setRightEl] = useState<HTMLElement | null>(null);
  const [railOwner, setRailOwner] = useState<RailOwner>(null);
  // Memoized so owner consumers don't re-render when a host element mounts.
  const railValue = useMemo<RailOwnerState>(
    () => ({ owner: railOwner, claim: setRailOwner }),
    [railOwner],
  );
  return (
    <HeaderActionsContext.Provider value={{ el: headerEl, setEl: setHeaderEl }}>
      <AgentsBarContext.Provider value={{ el: agentsEl, setEl: setAgentsEl }}>
        <RightPanelContext.Provider value={{ el: rightEl, setEl: setRightEl }}>
          <RailOwnerContext.Provider value={railValue}>
            {children}
          </RailOwnerContext.Provider>
        </RightPanelContext.Provider>
      </AgentsBarContext.Provider>
    </HeaderActionsContext.Provider>
  );
}

/** Read/claim the right rail. Null outside the provider. */
export function useRailOwner(): RailOwnerState | null {
  return useContext(RailOwnerContext);
}

/** Read the pinned-header actions host. Null outside the provider. */
export function useHeaderActionsHost(): ChromeHost | null {
  return useContext(HeaderActionsContext);
}

/** Read the sub-header agents bar host. Null outside the provider. */
export function useAgentsBarHost(): ChromeHost | null {
  return useContext(AgentsBarContext);
}

/** Read the right-column panel host. Null outside the provider. */
export function useRightPanelHost(): ChromeHost | null {
  return useContext(RightPanelContext);
}
