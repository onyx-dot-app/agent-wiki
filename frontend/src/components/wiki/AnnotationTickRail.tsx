"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type RefObject,
} from "react";

import type { CoeditorHandle } from "@/lib/editor/components";
import type { AnchoredHighlightTarget } from "@/lib/editor/highlights";

// A tick per annotated region, deduped when two regions land within one
// slot of each other on the strip.
const TICK_SLOT = 12;

interface Tick {
  kind: "comment" | "source";
  id: string;
  offset: number;
  /** Content fraction of the doc, 0..1. */
  fraction: number;
}

/**
 * Overview tick strip at the doc's right margin (mock 1855:281488): one
 * line per commented or source-attributed region at its proportional doc
 * position, the active region's tick heavier (Weight/Bar/Medium,
 * Border/05) than the rest (Weight/Bar/Small, Border/02). Clicking a tick
 * scrolls to and activates its region. Reads whichever highlight field is
 * populated, which the tabs keep mutually exclusive.
 */
export function AnnotationTickRail({
  editorRef,
  commentTargets,
  sourceTargets,
  activeCommentIds,
  activeSourceIds,
  onPickComment,
  onPickSource,
}: {
  editorRef: RefObject<CoeditorHandle | null>;
  /** The page's current targets, retriggering layout when they change.
   * Tick positions read the editor's live-mapped copies. */
  commentTargets: AnchoredHighlightTarget[];
  sourceTargets: AnchoredHighlightTarget[];
  activeCommentIds?: string[];
  activeSourceIds?: string[];
  onPickComment?: (id: string) => void;
  onPickSource?: (key: string) => void;
}) {
  const [ticks, setTicks] = useState<Tick[]>([]);
  const [trackH, setTrackH] = useState(0);
  const trackRef = useRef<HTMLDivElement | null>(null);
  const rafId = useRef(0);

  // The slot collapse needs the strip's real height, measured after mount
  // and on resize (reading it during render sees 0 on the first
  // tick-bearing pass, before the strip exists).
  const trackObserver = useRef<ResizeObserver | null>(null);
  const measureTrack = useCallback((el: HTMLDivElement | null) => {
    trackObserver.current?.disconnect();
    trackObserver.current = null;
    trackRef.current = el;
    if (!el) return;
    setTrackH(el.clientHeight);
    const ro = new ResizeObserver(() => setTrackH(el.clientHeight));
    ro.observe(el);
    trackObserver.current = ro;
  }, []);

  const relayout = useCallback(() => {
    const editor = editorRef.current;
    if (!editor) return;
    const sh = editor.scrollHeight();
    if (sh <= 0) return;
    // First surviving span per id, from the live-mapped fields so ticks
    // ride edits like the highlights do.
    const next: Tick[] = [];
    const seen = new Set<string>();
    const collect = (
      targets: AnchoredHighlightTarget[],
      kind: Tick["kind"],
    ) => {
      for (const t of targets) {
        const key = `${kind}:${t.id}`;
        if (!t.id || t.startOffset >= t.endOffset || seen.has(key)) continue;
        const line = editor.anchorLine(t.startOffset);
        if (!line) continue;
        seen.add(key);
        next.push({
          kind,
          id: t.id,
          offset: t.startOffset,
          fraction: Math.min(1, Math.max(0, line.top / sh)),
        });
      }
    };
    collect(editor.commentTargets(), "comment");
    collect(editor.sourceTargets(), "source");
    next.sort((a, b) => a.fraction - b.fraction);
    setTicks(next);
  }, [editorRef]);

  useEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    const scheduled = () => {
      cancelAnimationFrame(rafId.current);
      rafId.current = requestAnimationFrame(relayout);
    };
    // Synchronous first pass, rAF is throttled in occluded tabs. Ticks are
    // content-fraction positioned, so plain scrolling never moves them.
    relayout();
    const unsub = editor.subscribeLayout((kind) => {
      if (kind === "geometry") scheduled();
    });
    return () => {
      unsub();
      cancelAnimationFrame(rafId.current);
    };
  }, [relayout, editorRef]);

  // Retrigger on target-set changes that reach the editor via effects,
  // which produce no geometry notification of their own.
  useEffect(relayout, [relayout, commentTargets, sourceTargets]);

  if (ticks.length === 0) return null;

  const isActive = (t: Tick) =>
    t.kind === "comment"
      ? !!activeCommentIds?.includes(t.id)
      : !!activeSourceIds?.includes(t.id);

  // Collapse ticks that share a slot, keeping an active one visible.
  const shown: Tick[] = [];
  for (const t of ticks) {
    const prev = shown[shown.length - 1];
    if (
      prev &&
      trackH > 0 &&
      (t.fraction - prev.fraction) * trackH < TICK_SLOT &&
      !isActive(t)
    )
      continue;
    shown.push(t);
  }

  return (
    <div
      ref={measureTrack}
      className="pointer-events-none absolute inset-y-2 right-5 z-10 w-5"
    >
      {shown.map((t) => (
        // raw-ok: no Opal control renders a 2px proportional minimap tick
        <button
          key={`${t.kind}:${t.id}`}
          type="button"
          aria-label={t.kind === "comment" ? "Comment" : "Source"}
          className="pointer-events-auto absolute inset-x-[2px] flex h-2 cursor-pointer items-center"
          style={{ top: `calc(${t.fraction * 100}% - 4px)` }}
          onClick={() =>
            t.kind === "comment" ? onPickComment?.(t.id) : onPickSource?.(t.id)
          }
        >
          <span
            className={
              isActive(t)
                ? "h-[4px] w-full rounded-full bg-(--border-05)"
                : "h-[2px] w-full rounded-full bg-(--border-02)"
            }
          />
        </button>
      ))}
    </div>
  );
}
