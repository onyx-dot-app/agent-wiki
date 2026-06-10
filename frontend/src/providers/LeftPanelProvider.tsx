"use client";

import {
  createContext,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useAppFocus } from "@/hooks/useAppFocus";

export type LeftPanelView = "wiki-tree" | "activities" | null;

interface LeftPanelContextValue {
  /** What is currently rendered in the left panel. Mutual exclusion is
   *  enforced here — consumers never need to check both sources. */
  view: LeftPanelView;
  /** True when the user has explicitly opened the Activities panel. */
  isActivitiesOpen: boolean;
  /** True when the current route is a wiki route (drives WikiItemActionsProvider). */
  isOnWikiRoute: boolean;
  /** Toggle the Activities panel open/closed. */
  toggleActivities: () => void;
}

const LeftPanelContext = createContext<LeftPanelContextValue | null>(null);

export function useLeftPanel(): LeftPanelContextValue {
  const ctx = useContext(LeftPanelContext);
  if (!ctx)
    throw new Error("useLeftPanel must be used within LeftPanelProvider");
  return ctx;
}

interface LeftPanelProviderProps {
  children: ReactNode;
}

export function LeftPanelProvider({ children }: LeftPanelProviderProps) {
  const focus = useAppFocus();
  const [activitiesOpen, setActivitiesOpen] = useState(false);

  const isOnWikiRoute = focus.isWiki();

  // Invariant: activities takes priority; wiki-tree shows only when on a wiki
  // route and activities is not open. Exactly one thing shows at a time.
  const view: LeftPanelView = activitiesOpen
    ? "activities"
    : isOnWikiRoute
      ? "wiki-tree"
      : null;

  const value = useMemo<LeftPanelContextValue>(
    () => ({
      view,
      isActivitiesOpen: activitiesOpen,
      isOnWikiRoute,
      toggleActivities: () => setActivitiesOpen((v) => !v),
    }),
    [view, activitiesOpen, isOnWikiRoute],
  );

  return (
    <LeftPanelContext.Provider value={value}>
      {children}
    </LeftPanelContext.Provider>
  );
}
