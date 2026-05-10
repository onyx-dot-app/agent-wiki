"use client";

import { useEffect, useState } from "react";

// Single mobile breakpoint covering phones and small tablets. Above this
// width components render their desktop layout. Larger tablets get the
// desktop layout to match how Linear/Notion behave.
export const MOBILE_BREAKPOINT = 768;

// SSR fallback. The server has no viewport, so any width chosen here
// only affects the very first render before useEffect hydrates with the
// real value. A desktop default keeps the SSR HTML sane on full pages.
const SSR_FALLBACK_WIDTH = 1024;

// Tracks window.innerWidth and re-renders on resize. Lazy-initializes
// from the real value on the client so the first client paint matches
// the actual viewport — same pattern AppShell uses for its sidebar
// `collapsed` state.
export function useViewportWidth(): number {
  const [width, setWidth] = useState<number>(() => {
    if (typeof window === "undefined") return SSR_FALLBACK_WIDTH;
    return window.innerWidth;
  });

  useEffect(() => {
    function onResize() {
      setWidth(window.innerWidth);
    }
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  return width;
}

export function useIsMobile(breakpoint: number = MOBILE_BREAKPOINT): boolean {
  return useViewportWidth() < breakpoint;
}
