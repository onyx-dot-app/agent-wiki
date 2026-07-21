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

const CARD_GAP = 8;
const DEFAULT_CARD_HEIGHT = 64;
const DRAFT_KEY = "__draft__";

/**
 * Floating margin comments (mocks 566:19918 / 669:264296 / 778:262971 /
 * 670:266803): cards sit in the doc's right whitespace, tops aligned to
 * their anchors' line blocks and pushed down when they would overlap. The
 * layer translates against the editor's internal scroll so cards track the
 * text. Anchor-collision stacking is unspecified in the mocks, so the rule
 * here is the Google-Docs one: document order, minimum 8px apart.
 */
export function CommentMarginRail({
  threads,
  draft,
  editorRef,
  activeId,
  onActivate,
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
      if (t.root.status === "orphaned" || t.root.start_offset === null)
        continue;
      const want = editor.anchorTop(t.root.start_offset);
      if (want !== null) wanted.push({ key: t.root.id, want });
    }
    if (draft) {
      const want = editor.anchorTop(draft.startOffset);
      if (want !== null) wanted.push({ key: DRAFT_KEY, want });
    }
    wanted.sort((a, b) => a.want - b.want);
    let cursor = 0;
    const next: Record<string, number> = {};
    for (const { key, want } of wanted) {
      const top = Math.max(want, cursor);
      next[key] = top;
      cursor = top + (heights.current[key] ?? DEFAULT_CARD_HEIGHT) + CARD_GAP;
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
    scheduled();
    const unsub = editor.subscribeLayout(scheduled);
    return () => {
      unsub();
      cancelAnimationFrame(rafId.current);
    };
  }, [relayout, editorRef]);

  // Card sizes feed the stacking pass (expanding a thread pushes the rest
  // down). One observer per mounted card.
  const observers = useRef<Map<string, ResizeObserver>>(new Map());
  const measureRef = useCallback(
    (key: string) => (el: HTMLDivElement | null) => {
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
          relayout();
        }
      });
      ro.observe(el);
      observers.current.set(key, ro);
    },
    [relayout],
  );
  useEffect(() => {
    const map = observers.current;
    return () => {
      for (const ro of map.values()) ro.disconnect();
      map.clear();
    };
  }, []);

  const anchoredThreads = threads.filter((t) => tops[t.root.id] !== undefined);

  return (
    <div className="pointer-events-none absolute inset-y-0 right-3 z-[5] w-[336px] overflow-hidden">
      {draft && tops[DRAFT_KEY] !== undefined && (
        <div
          ref={measureRef(DRAFT_KEY)}
          className="pointer-events-auto absolute inset-x-0 top-0"
          style={{
            transform: `translateY(${tops[DRAFT_KEY]! - scrollTop}px)`,
          }}
        >
          <NewCommentComposer
            selfName={selfName}
            quotedText={draft.quotedText}
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
          className="pointer-events-auto absolute inset-x-0 top-0"
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
            run={run}
          />
        </div>
      ))}
    </div>
  );
}
