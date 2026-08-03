"use client";

import { useCallback, useEffect, useMemo, useRef, type RefObject } from "react";

/**
 * What EdgeScrollbar drives: any vertically scrolling surface that can
 * report its geometry and notify on layout changes. `CoeditorHandle`
 * satisfies it structurally; a plain DOM scroller adapts via
 * `useElementScrollTarget`. One module so every doc surface (live editor,
 * update-history diff) shares the same thumb — geometry, color, behavior.
 */
export interface EdgeScrollTarget {
  scrollHeight: () => number;
  clientHeight: () => number;
  scrollTop: () => number;
  scrollBy: (delta: number) => void;
  /** Notify `cb` on scroll/geometry changes; returns an unsubscribe. */
  subscribeLayout: (cb: (kind: "scroll" | "geometry") => void) => () => void;
}

/** Adapts a plain DOM scroll container to `EdgeScrollTarget`. Geometry
 * changes are observed on the container and its first child (the content
 * wrapper), which is where content-driven height changes land. */
export function useElementScrollTarget(
  elRef: RefObject<HTMLElement | null>,
): RefObject<EdgeScrollTarget | null> {
  const target = useMemo<EdgeScrollTarget>(
    () => ({
      scrollHeight: () => elRef.current?.scrollHeight ?? 0,
      clientHeight: () => elRef.current?.clientHeight ?? 0,
      scrollTop: () => elRef.current?.scrollTop ?? 0,
      scrollBy: (delta) => elRef.current?.scrollBy({ top: delta }),
      subscribeLayout: (cb) => {
        const el = elRef.current;
        if (!el) return () => {};
        const onScroll = () => cb("scroll");
        const ro = new ResizeObserver(() => cb("geometry"));
        const watchContent = () => {
          ro.disconnect();
          ro.observe(el);
          if (el.firstElementChild) ro.observe(el.firstElementChild);
        };
        // The content wrapper is replaced wholesale when the surface swaps
        // what it shows (e.g. a different commit's diff) — re-aim the resize
        // observer at the new child or thumb geometry goes stale.
        const mo = new MutationObserver(() => {
          watchContent();
          cb("geometry");
        });
        el.addEventListener("scroll", onScroll, { passive: true });
        watchContent();
        mo.observe(el, { childList: true });
        return () => {
          el.removeEventListener("scroll", onScroll);
          ro.disconnect();
          mo.disconnect();
        };
      },
    }),
    [elRef],
  );
  const ref = useRef<EdgeScrollTarget | null>(target);
  ref.current = target;
  return ref;
}

/**
 * Thumb-only doc scrollbar (no rail) for a surface whose native bar is
 * hidden. Direct style writes on scroll notifications, pointer-capture drag.
 */
export function EdgeScrollbar({
  targetRef,
  className = "absolute inset-y-1 -right-2 w-3",
}: {
  targetRef: RefObject<EdgeScrollTarget | null>;
  /** Track placement relative to the nearest positioned ancestor. */
  className?: string;
}) {
  const trackRef = useRef<HTMLDivElement | null>(null);
  const thumbRef = useRef<HTMLDivElement | null>(null);
  const drag = useRef<{ startY: number; startTop: number } | null>(null);

  const metrics = useCallback(() => {
    const target = targetRef.current;
    const track = trackRef.current;
    if (!target || !track) return null;
    const sh = target.scrollHeight();
    const ch = target.clientHeight();
    const trackH = track.clientHeight;
    if (sh <= ch + 1 || trackH <= 0) return null;
    const thumbH = Math.max(24, (ch / sh) * trackH);
    return { sh, ch, trackH, thumbH, maxThumb: trackH - thumbH };
  }, [targetRef]);

  const sync = useCallback(() => {
    const target = targetRef.current;
    const thumb = thumbRef.current;
    if (!target || !thumb) return;
    const m = metrics();
    if (!m) {
      thumb.style.display = "none";
      return;
    }
    thumb.style.display = "";
    thumb.style.height = `${m.thumbH}px`;
    const ratio = target.scrollTop() / (m.sh - m.ch);
    thumb.style.transform = `translateY(${ratio * m.maxThumb}px)`;
  }, [targetRef, metrics]);

  useEffect(() => {
    const target = targetRef.current;
    if (!target) return;
    sync();
    return target.subscribeLayout(sync);
  }, [targetRef, sync]);

  return (
    /* raw-ok: a custom scrollbar control has no Opal equivalent */
    <div
      ref={trackRef}
      className={className}
      onPointerDown={(e) => {
        const target = targetRef.current;
        const thumb = thumbRef.current;
        const m = metrics();
        if (!target || !thumb || !m) return;
        const thumbRect = thumb.getBoundingClientRect();
        if (e.clientY < thumbRect.top || e.clientY > thumbRect.bottom) {
          // Track press: jump so the thumb centers on the pointer.
          const trackTop = trackRef.current!.getBoundingClientRect().top;
          const ratio = Math.min(
            1,
            Math.max(0, (e.clientY - trackTop - m.thumbH / 2) / m.maxThumb),
          );
          target.scrollBy(ratio * (m.sh - m.ch) - target.scrollTop());
        }
        drag.current = { startY: e.clientY, startTop: target.scrollTop() };
        e.currentTarget.setPointerCapture(e.pointerId);
      }}
      onPointerMove={(e) => {
        const target = targetRef.current;
        const m = metrics();
        if (!drag.current || !target || !m) return;
        const dy = e.clientY - drag.current.startY;
        const next = drag.current.startTop + (dy / m.maxThumb) * (m.sh - m.ch);
        target.scrollBy(next - target.scrollTop());
      }}
      onPointerUp={(e) => {
        drag.current = null;
        e.currentTarget.releasePointerCapture(e.pointerId);
      }}
    >
      <div
        ref={thumbRef}
        className="mx-auto w-[6px] rounded-full bg-(--text-02) hover:bg-(--text-04)"
      />
    </div>
  );
}
