"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type RefObject,
} from "react";

import type { CoeditorHandle } from "@/lib/editor/components";
import type { CommentDraft } from "@/lib/editor/comments";
import type { CommentThreadView } from "@/types";

import { ThreadCard } from "./CommentsPanel";
import { NewCommentComposer } from "./commentCards";

const CARD_GAP = 4;
const DEFAULT_CARD_HEIGHT = 64;
const DRAFT_KEY = "__draft__";
// Card top such that the 32px title row's center sits on the anchor line's
// center (mocks 566:19918 / 669:264296 measure the two centers equal).
const TITLE_ROW_CENTER = 20;

/**
 * Margin comments column (mocks 566:19918 / 669:264296 / 778:262971 /
 * 670:266803): a real 360px lane beside the doc, cards 336 wide with 12px
 * side insets. Each card anchors its title-row center to its highlight
 * line's center and is pushed down when the card above collides, minimum
 * 4px apart (mock 778 measures the collision gap). The layer translates
 * against the editor's internal scroll so cards track the text. Resolved
 * threads never float here, the panel owns them.
 */
export function CommentMarginRail({
  threads,
  draft,
  editorRef,
  activeId,
  onActivate,
  onHoverThread,
  selfName,
  path,
  selfId,
  isAdmin,
  busy,
  run,
  onSubmitDraft,
  onCancelDraft,
}: {
  threads: CommentThreadView[];
  draft: CommentDraft | null;
  editorRef: RefObject<CoeditorHandle | null>;
  activeId: string | null;
  onActivate: (id: string | null) => void;
  onHoverThread?: (id: string | null) => void;
  selfName: string;
  path: string;
  selfId: string | undefined;
  isAdmin: boolean;
  busy: boolean;
  run: (fn: () => Promise<unknown>) => Promise<boolean>;
  onSubmitDraft: (body: string) => void;
  onCancelDraft: () => void;
}) {
  const [tops, setTops] = useState<Record<string, number>>({});
  const [scrollTop, setScrollTop] = useState(0);
  const heights = useRef<Record<string, number>>({});
  const rafId = useRef(0);

  const relayout = useCallback(() => {
    const editor = editorRef.current;
    if (!editor) return;
    const wanted: Array<{ key: string; want: number }> = [];
    for (const t of threads) {
      if (t.root.status !== "open" || t.root.start_offset === null) continue;
      const line = editor.anchorLine(t.root.start_offset);
      if (line)
        wanted.push({
          key: t.root.id,
          want: line.top + line.height / 2 - TITLE_ROW_CENTER,
        });
    }
    if (draft) {
      const line = editor.anchorLine(draft.startOffset);
      if (line)
        wanted.push({
          key: DRAFT_KEY,
          want: line.top + line.height / 2 - TITLE_ROW_CENTER,
        });
    }
    wanted.sort((a, b) => a.want - b.want);
    let cursor = 0;
    const next: Record<string, number> = {};
    for (const { key, want } of wanted) {
      const top = Math.max(want, cursor);
      next[key] = top;
      cursor = top + (heights.current[key] ?? DEFAULT_CARD_HEIGHT) + CARD_GAP;
    }
    // Reverse clamp against the doc's scroll extent: a card stacked past the
    // last reachable viewport bottom could never be scrolled into view, so
    // bottom cards shift up (chaining the 4px gap) instead of clipping.
    let bottom = editor.scrollHeight() - CARD_GAP;
    for (let i = wanted.length - 1; i >= 0; i--) {
      const key = wanted[i]!.key;
      const h = heights.current[key] ?? DEFAULT_CARD_HEIGHT;
      if (next[key]! + h > bottom) next[key] = Math.max(0, bottom - h);
      bottom = next[key]! - CARD_GAP;
    }
    setTops(next);
    setScrollTop(editor.scrollTop());
  }, [threads, draft, editorRef]);

  // Scroll and geometry changes arrive per frame at most.
  useEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    const scheduled = () => {
      cancelAnimationFrame(rafId.current);
      rafId.current = requestAnimationFrame(relayout);
    };
    // Synchronous first pass: rAF is throttled in occluded tabs, and the
    // initial card layout must not wait for a frame.
    relayout();
    const unsub = editor.subscribeLayout(scheduled);
    return () => {
      unsub();
      cancelAnimationFrame(rafId.current);
    };
  }, [relayout, editorRef]);

  // Card sizes feed the stacking pass (expanding a thread pushes the rest
  // down). One observer per mounted card, ref callbacks cached per key so
  // per-frame re-renders don't detach and reattach observers.
  const observers = useRef<Map<string, ResizeObserver>>(new Map());
  const refCallbacks = useRef<Map<string, (el: HTMLDivElement | null) => void>>(
    new Map(),
  );
  const relayoutRef = useRef(relayout);
  relayoutRef.current = relayout;
  const measureRef = useCallback((key: string) => {
    let cb = refCallbacks.current.get(key);
    if (!cb) {
      cb = (el: HTMLDivElement | null) => {
        const prev = observers.current.get(key);
        if (prev) {
          prev.disconnect();
          observers.current.delete(key);
        }
        if (!el) return;
        const ro = new ResizeObserver(() => {
          const h = el.getBoundingClientRect().height;
          if (Math.abs((heights.current[key] ?? 0) - h) > 1) {
            heights.current[key] = h;
            relayoutRef.current();
          }
        });
        ro.observe(el);
        observers.current.set(key, ro);
      };
      refCallbacks.current.set(key, cb);
    }
    return cb;
  }, []);
  useEffect(() => {
    const map = observers.current;
    return () => {
      for (const ro of map.values()) ro.disconnect();
      map.clear();
    };
  }, []);

  const anchoredThreads = threads.filter((t) => tops[t.root.id] !== undefined);

  return (
    <div className="relative w-[360px] shrink-0 overflow-clip @max-[920px]:hidden">
      {draft && tops[DRAFT_KEY] !== undefined && (
        <div
          ref={measureRef(DRAFT_KEY)}
          className="absolute inset-x-3 top-0"
          style={{
            transform: `translateY(${tops[DRAFT_KEY]! - scrollTop}px)`,
          }}
        >
          <NewCommentComposer
            selfName={selfName}
            disabled={busy}
            onSubmit={onSubmitDraft}
            onCancel={onCancelDraft}
          />
        </div>
      )}
      {anchoredThreads.map((t) => (
        <div
          key={t.root.id}
          ref={measureRef(t.root.id)}
          className="absolute inset-x-3 top-0"
          style={{
            transform: `translateY(${tops[t.root.id]! - scrollTop}px)`,
          }}
        >
          <ThreadCard
            thread={t}
            path={path}
            selfId={selfId}
            isAdmin={isAdmin}
            busy={busy}
            active={t.root.id === activeId}
            anchored
            onActivate={() => onActivate(t.root.id)}
            onHoverChange={(h) => onHoverThread?.(h ? t.root.id : null)}
            run={run}
          />
        </div>
      ))}
    </div>
  );
}
