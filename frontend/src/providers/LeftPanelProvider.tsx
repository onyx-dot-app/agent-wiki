"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useAppFocus } from "@/hooks/useAppFocus";

export type LeftPanelView = "wiki-tree" | "activities" | null;

const TREE_OPEN_KEY = "agent-wiki:wiki-tree-open";

interface LeftPanelContextValue {
  /** What is currently rendered in the left panel. Mutual exclusion is
   *  enforced here — consumers never need to check both sources. */
  view: LeftPanelView;
  /** Whether the wiki tree is open (persisted in localStorage). */
  isTreeOpen: boolean;
  /** True when the user has explicitly opened the Activities panel. */
  isActivitiesOpen: boolean;
  /** True when the current route is a wiki route (drives WikiItemActionsProvider). */
  isOnWikiRoute: boolean;
  /** Toggle the wiki tree open/closed. Persisted in localStorage. */
  toggleTree: () => void;
  /** Toggle the Activities panel open/closed. Ephemeral — not persisted. */
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
  // Starts open on server and client alike so hydration matches. The stored
  // preference applies after mount.
  const [treeOpen, setTreeOpen] = useState(true);
  useEffect(() => {
    if (window.localStorage.getItem(TREE_OPEN_KEY) === "0") setTreeOpen(false);
  }, []);

  const isOnWikiRoute = focus.isWiki();

  const toggleTree = useCallback(() => {
    setTreeOpen((prev) => {
      const next = !prev;
      if (typeof window !== "undefined")
        window.localStorage.setItem(TREE_OPEN_KEY, next ? "1" : "0");
      return next;
    });
  }, []);

  // Invariant: activities takes priority over wiki-tree. treeOpen is never
  // mutated by activities toggling — it persists underneath, so closing
  // activities always restores the tree to its prior state.
  const view: LeftPanelView = activitiesOpen
    ? "activities"
    : isOnWikiRoute && treeOpen
      ? "wiki-tree"
      : null;

  const value = useMemo<LeftPanelContextValue>(
    () => ({
      view,
      isTreeOpen: treeOpen,
      isActivitiesOpen: activitiesOpen,
      isOnWikiRoute,
      toggleTree,
      toggleActivities: () => setActivitiesOpen((v) => !v),
    }),
    [view, treeOpen, activitiesOpen, isOnWikiRoute, toggleTree],
  );

  return (
    <LeftPanelContext.Provider value={value}>
      {children}
    </LeftPanelContext.Provider>
  );
}
