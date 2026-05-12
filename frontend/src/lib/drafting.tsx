"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export interface DraftingState {
  /** Canonical wiki path the user is drafting. ``null`` while the doc
   *  is still being composed in NewDocView (no file on disk yet). */
  path: string | null;
  /** Display name of the template (null if it was deleted after creation). */
  templateName: string | null;
  /** Template id, used to fetch the system prompt server-side. ``null``
   *  when drafting state is sourced from a draft row whose template was
   *  later deleted (the path-based prompt lookup still works). */
  templateId: string | null;
}

interface DraftingContextValue {
  /** Active drafting state, or null if not drafting from a template. */
  drafting: DraftingState | null;
  /** Replace the active drafting state (or null to clear). */
  setDrafting: (next: DraftingState | null) => void;
  /** Increment to request the chat widget pop into expanded mode. */
  expandTick: number;
  /** Bump to fire the expand request. */
  requestExpand: () => void;
}

const DraftingContext = createContext<DraftingContextValue | null>(null);

export function DraftingProvider({ children }: { children: ReactNode }) {
  const [drafting, setDrafting] = useState<DraftingState | null>(null);
  const [expandTick, setExpandTick] = useState(0);

  const requestExpand = useCallback(() => setExpandTick((t) => t + 1), []);

  const value = useMemo(
    () => ({ drafting, setDrafting, expandTick, requestExpand }),
    [drafting, expandTick, requestExpand],
  );

  return <DraftingContext.Provider value={value}>{children}</DraftingContext.Provider>;
}

export function useDrafting(): DraftingContextValue {
  const ctx = useContext(DraftingContext);
  if (!ctx) {
    throw new Error("useDrafting must be used within DraftingProvider");
  }
  return ctx;
}
