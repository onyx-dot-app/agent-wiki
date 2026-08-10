"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

/** Read/write access to the in-progress (unsaved) doc editor, registered by
 *  NewDocView so the drafting chat can live-edit the draft before it's saved. */
export interface DraftBridge {
  get: () => string;
  set: (body: string) => void;
}

/** Active drafting state. Discriminated on ``kind``:
 *
 *  - ``template`` — user picked a named template; chat is seeded with
 *    that template's body and (optional) system prompt.
 *  - ``blank`` — user picked "Blank document"; chat gets a generic
 *    "what do you want to work on" prime that hints at the wiki's
 *    auto-fill behavior. No template row on the server.
 *
 *  Both variants carry ``path`` — null while the doc is still being
 *  composed in NewDocView (no file on disk yet). */
export type DraftingState =
  | {
      kind: "template";
      path: string | null;
      templateName: string | null;
      /** Used to fetch the system prompt server-side. ``null`` when the
       *  draft row's template was deleted after creation. */
      templateId: string | null;
    }
  | {
      kind: "blank";
      path: string | null;
    };

interface DraftingContextValue {
  /** Active drafting state, or null if not drafting from a template. */
  drafting: DraftingState | null;
  /** Replace the active drafting state (or null to clear). */
  setDrafting: (next: DraftingState | null) => void;
  /** Increment to request the chat widget pop into expanded mode. */
  expandTick: number;
  /** Bump to fire the expand request. */
  requestExpand: () => void;
  /** NewDocView registers its editor here (null to unregister on unmount). */
  registerDraftBridge: (bridge: DraftBridge | null) => void;
  /** Current unsaved draft body, or null when no editor is bridged. */
  getDraftBody: () => string | null;
  /** Replace the unsaved draft body. Returns false when no editor is bridged. */
  applyDraftBody: (body: string) => boolean;
}

const DraftingContext = createContext<DraftingContextValue | null>(null);

export function DraftingProvider({ children }: { children: ReactNode }) {
  const [drafting, setDrafting] = useState<DraftingState | null>(null);
  const [expandTick, setExpandTick] = useState(0);

  const requestExpand = useCallback(() => setExpandTick((t) => t + 1), []);

  // A ref (not state) so registering / reading the editor doesn't re-render.
  const draftBridge = useRef<DraftBridge | null>(null);
  const registerDraftBridge = useCallback((b: DraftBridge | null) => {
    draftBridge.current = b;
  }, []);
  const getDraftBody = useCallback(
    () => draftBridge.current?.get() ?? null,
    [],
  );
  const applyDraftBody = useCallback((body: string) => {
    if (!draftBridge.current) return false;
    draftBridge.current.set(body);
    return true;
  }, []);

  const value = useMemo(
    () => ({
      drafting,
      setDrafting,
      expandTick,
      requestExpand,
      registerDraftBridge,
      getDraftBody,
      applyDraftBody,
    }),
    [
      drafting,
      expandTick,
      requestExpand,
      registerDraftBridge,
      getDraftBody,
      applyDraftBody,
    ],
  );

  return (
    <DraftingContext.Provider value={value}>
      {children}
    </DraftingContext.Provider>
  );
}

export function useDrafting(): DraftingContextValue {
  const ctx = useContext(DraftingContext);
  if (!ctx) {
    throw new Error("useDrafting must be used within DraftingProvider");
  }
  return ctx;
}
