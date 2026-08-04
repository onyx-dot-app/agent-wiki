"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import type { ToolbarContext } from "@/components/wiki/toolbar/chatParts";

interface RemovableToolbarContext {
  /** The host's page context first, then anything attached, in chip order. */
  contexts: ToolbarContext[];
  removeContext: (path: string) => void;
  /** Attach another wiki doc alongside whatever is already attached. */
  attachContext: (path: string) => void;
}

export function useRemovableToolbarContext(
  context: ToolbarContext | null | undefined,
): RemovableToolbarContext {
  const [removed, setRemoved] = useState<string[]>([]);
  const [attached, setAttached] = useState<ToolbarContext[]>([]);

  // Navigating resets removals and drops anything attached to the old page.
  useEffect(() => {
    setRemoved([]);
    setAttached([]);
  }, [context?.path]);

  const contexts = useMemo(() => {
    const all = context ? [context, ...attached] : attached;
    return all.filter((c) => !removed.includes(c.path));
  }, [context, attached, removed]);

  const removeContext = useCallback((path: string) => {
    setAttached((prev) => prev.filter((c) => c.path !== path));
    setRemoved((prev) => (prev.includes(path) ? prev : [...prev, path]));
  }, []);

  const attachContext = useCallback((path: string) => {
    setRemoved((prev) => prev.filter((p) => p !== path));
    setAttached((prev) =>
      prev.some((c) => c.path === path)
        ? prev
        : [...prev, { path, kind: "doc" }],
    );
  }, []);

  return { contexts, removeContext, attachContext };
}
