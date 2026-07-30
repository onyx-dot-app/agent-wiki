"use client";

/** Tiptap-based live editor. Replaces `frontend/src/lib/editor/` (the
 * CodeMirror/OT-era editor, deleted once this cutover lands). */
import { posToDOMRect } from "@tiptap/core";
import { EditorContent, useEditor } from "@tiptap/react";
import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type MutableRefObject,
} from "react";
import { Awareness } from "y-protocols/awareness";
import * as Y from "yjs";
import { tiptapExtensions } from "@/lib/editor/extensions";
import {
  commentHighlights as commentHighlightPlugin,
  sourceHighlights as sourceHighlightPlugin,
} from "@/lib/editor/highlights";
import type { CoeditPeer } from "@/lib/editor/hooks";
import { colorFor } from "@/lib/editor/presence";
import type { CoeditParticipant } from "@/lib/editor/svc";
import { pmPosToTextOffset, textOffsetToPmPos } from "@/lib/editor/textOffsets";
import type {
  AnchoredHighlightTarget,
  CoeditorHandle,
  CommentDraft,
  TiptapEditorProps,
} from "@/lib/editor/types";

/** Highlight ids whose spans contain a collapsed caret or intersect a
 * selection, half-open at span ends so a caret just past a span misses —
 * port of `lib/editor/components.tsx`'s `caretHitIds`. */
function caretHitIds(
  targets: AnchoredHighlightTarget[],
  from: number,
  to: number,
): string[] {
  const ids: string[] = [];
  for (const t of targets) {
    const hit =
      from === to
        ? from >= t.startOffset && from < t.endOffset
        : from < t.endOffset && to > t.startOffset;
    if (hit && !ids.includes(t.id)) ids.push(t.id);
  }
  return ids;
}

export const TiptapEditor = forwardRef<CoeditorHandle, TiptapEditorProps>(
  function TiptapEditor(
    {
      doc: docProp,
      awareness: awarenessProp,
      userId,
      userDisplay,
      readOnly,
      placeholder,
      commentHighlights,
      activeCommentIds,
      onCommentCaret,
      sourceHighlights,
      activeSourceIds,
      onSourceCaret,
      onSelectionForComment,
      onEditorReady,
    }: TiptapEditorProps,
    ref,
  ) {
    // A caller-supplied doc (a real live session, or two relayed docs for
    // local multi-client verification) wins; otherwise a fresh local one —
    // P1's scaffold behavior, never synced anywhere.
    const [doc] = useState(() => docProp ?? new Y.Doc());
    // Awareness is Yjs's ephemeral (non-document) shared-state channel —
    // presence/cursor data rides on it, separate from the doc's actual
    // content. A caller-supplied instance wins (it owns that instance's
    // lifecycle — a real live session's or multi-client verification's own
    // relay wiring); otherwise a fresh local one, owned and destroyed here.
    const [awareness] = useState(() => awarenessProp ?? new Awareness(doc));
    const [localId] = useState(() => userId ?? crypto.randomUUID());
    const scrollRef = useRef<HTMLDivElement | null>(null);
    // Layout subscribers (CommentMarginRail/EditorEdgeScrollbar-equivalents),
    // notified on scroll and geometry changes — see subscribeLayout below.
    const layoutSubs = useRef<Set<(kind: "scroll" | "geometry") => void>>(
      new Set(),
    );

    useEffect(() => {
      awareness.setLocalStateField("user", {
        // `id` correlates a peer's awareness entry (keyed by Yjs's own
        // ephemeral client id) back to their real app user_id — needed by
        // hooks.ts's `derivePeers` to cross-reference the durable
        // `participants` roster (join/leave, "editing" vs "viewing") against
        // live cursor presence.
        id: localId,
        name: userDisplay ?? "Anonymous",
        color: colorFor(localId),
      });
    }, [awareness, localId, userDisplay]);

    useEffect(() => {
      if (awarenessProp) return; // caller owns it — not ours to destroy
      return () => awareness.destroy();
    }, [awareness, awarenessProp]);

    const editor = useEditor({
      // Next.js renders this "use client" component's first pass on the
      // server too; without this, Tiptap's own first render there mismatches
      // the client's hydration pass. Tiptap's documented fix for SSR.
      immediatelyRender: false,
      editable: !readOnly,
      extensions: tiptapExtensions(doc, awareness, placeholder),
    });

    useEffect(() => {
      editor?.setEditable(!readOnly);
    }, [editor, readOnly]);

    useEffect(() => {
      if (editor) onEditorReady?.(editor);
      // Fires once per editor instance, deliberately not re-firing on every
      // onEditorReady identity change (the caller's own concern to memoize).
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [editor]);

    // commentHighlights/sourceHighlights arrive in markdown-source offset
    // space (matching the backend's comment/source-span APIs) — the
    // highlight plugins operate in ProseMirror position space, so this is
    // the one place that boundary gets crossed (see textOffsets.ts's
    // module docstring for the approximation and its known limits).
    useEffect(() => {
      if (!editor) return;
      const targets = (commentHighlights ?? []).map((t) => ({
        ...t,
        startOffset: textOffsetToPmPos(editor, t.startOffset),
        endOffset: textOffsetToPmPos(editor, t.endOffset),
      }));
      commentHighlightPlugin.setTargets(editor, targets);
    }, [editor, commentHighlights]);

    useEffect(() => {
      if (editor)
        commentHighlightPlugin.setActiveIds(editor, activeCommentIds ?? []);
    }, [editor, activeCommentIds]);

    useEffect(() => {
      if (!editor) return;
      const targets = (sourceHighlights ?? []).map((t) => ({
        ...t,
        startOffset: textOffsetToPmPos(editor, t.startOffset),
        endOffset: textOffsetToPmPos(editor, t.endOffset),
      }));
      sourceHighlightPlugin.setTargets(editor, targets);
    }, [editor, sourceHighlights]);

    useEffect(() => {
      if (editor)
        sourceHighlightPlugin.setActiveIds(editor, activeSourceIds ?? []);
    }, [editor, activeSourceIds]);

    // Caret-intersection reporting — fires on every transaction (doc changes,
    // selection changes, *and* the setMeta-only updates the effects above
    // dispatch), same trigger surface as the CM6 version's single
    // updateListener. Deduped against the last reported id set per field so
    // selection churn inside one span stays quiet.
    const onCommentCaretRef = useRef(onCommentCaret);
    const onSourceCaretRef = useRef(onSourceCaret);
    onCommentCaretRef.current = onCommentCaret;
    onSourceCaretRef.current = onSourceCaret;
    const lastCommentCaretIds = useRef("\0");
    const lastSourceCaretIds = useRef("\0");
    const onSelectionForCommentRef = useRef(onSelectionForComment);
    onSelectionForCommentRef.current = onSelectionForComment;
    const lastSelectionForComment = useRef("\0");

    useEffect(() => {
      if (!editor) return;
      const report = () => {
        const { from, to } = editor.state.selection;
        const selKey = `${from}:${to}`;
        if (selKey !== lastSelectionForComment.current) {
          lastSelectionForComment.current = selKey;
          const cb = onSelectionForCommentRef.current;
          if (cb) {
            if (from === to) {
              cb(null, null);
            } else {
              const draft: CommentDraft = {
                startOffset: pmPosToTextOffset(editor, from),
                endOffset: pmPosToTextOffset(editor, to),
                quotedText: editor.state.doc.textBetween(from, to, "\n\n"),
              };
              const coords = editor.view.coordsAtPos(to);
              cb(draft, { x: coords.left, y: coords.top });
            }
          }
        }
        const reportOne = (
          plugin: typeof commentHighlightPlugin,
          cb: ((ids: string[]) => void) | undefined,
          last: MutableRefObject<string>,
        ) => {
          if (!cb) return;
          const ids = caretHitIds(plugin.targets(editor), from, to);
          const key = ids.join("\n");
          if (key !== last.current) {
            last.current = key;
            cb(ids);
          }
        };
        reportOne(
          commentHighlightPlugin,
          onCommentCaretRef.current,
          lastCommentCaretIds,
        );
        reportOne(
          sourceHighlightPlugin,
          onSourceCaretRef.current,
          lastSourceCaretIds,
        );
      };
      editor.on("transaction", report);
      return () => {
        editor.off("transaction", report);
      };
    }, [editor]);

    const notifyLayout = (kind: "scroll" | "geometry") => {
      for (const cb of layoutSubs.current) cb(kind);
    };

    // Scroll notifications: native listener on the wrapper (fires
    // synchronously inside the scroll event, same guarantee the CM6 version's
    // subscribeLayout doc promises). Geometry notifications: ProseMirror has
    // no discrete "geometryChanged" flag the way CM6's update.geometryChanged
    // does, so every doc update is treated as a potential geometry change (the
    // closest equivalent), plus a ResizeObserver on the content element for
    // pure-reflow changes (window resize, wrapping) that aren't doc updates at
    // all.
    useEffect(() => {
      const wrapper = scrollRef.current;
      if (!wrapper) return;
      const onScroll = () => notifyLayout("scroll");
      wrapper.addEventListener("scroll", onScroll, { passive: true });
      const onUpdate = () => notifyLayout("geometry");
      editor?.on("update", onUpdate);
      const resizeObserver = new ResizeObserver(() => notifyLayout("geometry"));
      resizeObserver.observe(wrapper);
      return () => {
        wrapper.removeEventListener("scroll", onScroll);
        editor?.off("update", onUpdate);
        resizeObserver.disconnect();
      };
    }, [editor]);

    // Doc-space rect (top/height relative to the wrapper's own scroll origin,
    // not the current scroll position) for the region `[from, to]` — shared by
    // anchorLine and the scrollTo* methods below. `posToDOMRect` is Tiptap's
    // own building block for this; there's no ProseMirror equivalent of CM6's
    // lineBlockAt, so this is the idiomatic manual-measurement route.
    const docSpaceRect = (
      from: number,
      to: number,
    ): { top: number; height: number } | null => {
      if (!editor || !scrollRef.current) return null;
      const rect = posToDOMRect(editor.view, from, to);
      const wrapperRect = scrollRef.current.getBoundingClientRect();
      return {
        top: rect.top - wrapperRect.top + scrollRef.current.scrollTop,
        height: rect.height,
      };
    };

    useImperativeHandle(
      ref,
      (): CoeditorHandle => ({
        textOffsetToPos: (offset) => {
          if (!editor) return offset;
          return textOffsetToPmPos(editor, offset);
        },
        scrollToOffset: (offset) => {
          if (!editor || !scrollRef.current) return;
          const pos = Math.max(
            0,
            Math.min(offset, editor.state.doc.content.size),
          );
          const target = docSpaceRect(pos, pos);
          if (!target) return;
          // Center, matching the CM6 version's `y: "center"` — deliberately
          // manual scrollTop math, not setTextSelection().scrollIntoView(),
          // which would move the user's actual cursor just to scroll.
          scrollRef.current.scrollTop =
            target.top - scrollRef.current.clientHeight / 2;
        },
        scrollToSource: (id) => {
          if (!editor || !scrollRef.current) return;
          // A touched-block edit can collapse a span to zero width, and a
          // collapsed target paints nothing, so it can't be the scroll
          // destination.
          const target = sourceHighlightPlugin
            .targets(editor)
            .find((t) => t.id === id && t.startOffset < t.endOffset);
          if (!target) return;
          const pos = Math.max(
            0,
            Math.min(target.startOffset, editor.state.doc.content.size),
          );
          const rect = docSpaceRect(pos, pos);
          if (!rect) return;
          scrollRef.current.scrollTop =
            rect.top - scrollRef.current.clientHeight / 2;
        },
        sourceTargets: () =>
          editor ? sourceHighlightPlugin.targets(editor) : [],
        anchorLine: (offset) => {
          if (!editor) return null;
          const pos = Math.max(
            0,
            Math.min(offset, editor.state.doc.content.size),
          );
          const $pos = editor.state.doc.resolve(pos);
          return docSpaceRect($pos.start($pos.depth), $pos.end($pos.depth));
        },
        scrollTop: () => scrollRef.current?.scrollTop ?? 0,
        scrollBy: (dy) => {
          if (scrollRef.current) scrollRef.current.scrollTop += dy;
        },
        scrollHeight: () => scrollRef.current?.scrollHeight ?? 0,
        clientHeight: () => scrollRef.current?.clientHeight ?? 0,
        scrollerTop: () => scrollRef.current?.getBoundingClientRect().top ?? 0,
        subscribeLayout: (cb) => {
          layoutSubs.current.add(cb);
          return () => {
            layoutSubs.current.delete(cb);
          };
        },
      }),
      // No deps: the handle re-attaches every render, so a live session never
      // holds an object missing later-added methods — same rationale as the
      // CM6 version's identical choice here.
      // eslint-disable-next-line react-hooks/exhaustive-deps
    );

    return (
      <div ref={scrollRef} className="editor-prose">
        <EditorContent editor={editor} />
      </div>
    );
  },
);

interface CoeditPresenceBarProps {
  participants: CoeditParticipant[];
  /** Peers with a live cursor (from `useCoeditSession`) — a participant
   * with an entry here is "editing", the rest are "viewing". */
  peers: CoeditPeer[];
  typing: string[];
  selfUserId: string | null;
}

// Live-session presence: who else is on the page — labeled "editing" while
// their cursor is rendered in the content (yCursorPlugin), "viewing"
// otherwise — and who's typing right now. The label is derived from the
// same peers list that renders the carets, so bar and doc can never
// disagree. Renders nothing when you're alone. Ported verbatim from
// `lib/editor/components.tsx`'s component of the same name.
export function CoeditPresenceBar({
  participants,
  peers,
  typing,
  selfUserId,
}: CoeditPresenceBarProps) {
  const others = participants.filter((p) => p.user_id !== selfUserId);
  if (others.length === 0) return null;
  const typingSet = new Set(typing);
  const caretSet = new Set(peers.map((p) => p.user_id));
  return (
    <div className="flex shrink-0 flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-(--text-03)">
      <span
        className="inline-block h-[7px] w-[7px] rounded-full bg-(--status-success-05)"
        aria-hidden
      />
      {others.map((p) => (
        <span key={p.user_id} className="inline-flex items-center gap-1">
          <span className="font-medium text-(--text-04)">{p.user_display}</span>
          <span className="text-(--text-03) italic">
            {typingSet.has(p.user_id)
              ? "typing…"
              : caretSet.has(p.user_id)
                ? "editing"
                : "viewing"}
          </span>
        </span>
      ))}
    </div>
  );
}
