"use client";

/** Tiptap-based live editor. Replaces `frontend/src/lib/editor/` (the
 * CodeMirror/OT-era editor, deleted once this cutover lands). */
import { posToDOMRect, type Editor } from "@tiptap/core";
import { EditorContent, useEditor } from "@tiptap/react";
import type {
  SuggestionKeyDownProps,
  SuggestionProps,
} from "@tiptap/suggestion";
import {
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type MutableRefObject,
  type Ref,
} from "react";
import { Awareness } from "y-protocols/awareness";
import * as Y from "yjs";
import { LineItemButton } from "@onyx-ai/opal/components";
import { tiptapExtensions } from "@/lib/editor/extensions";
import type { CommandItem } from "@/lib/editor/extensions/types";
import {
  commentHighlights as commentHighlightPlugin,
  sourceHighlights as sourceHighlightPlugin,
} from "@/lib/editor/extensions/highlights";
import type { CoeditPeer } from "@/lib/editor/hooks";
import { sessionColorFor } from "@/lib/editor/identityColor";
import type { CoeditParticipant } from "@/lib/editor/svc";
import {
  docTextBetween,
  pmPosToTextOffset,
  textOffsetToPmPos,
} from "@/lib/editor/textOffsets";
import { opaqueId } from "@/lib/editor/ids";
import type {
  AnchoredHighlightTarget,
  CoeditorHandle,
  CommentDraft,
  CommentHighlightTarget,
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

export interface TipTapEditorProps {
  /** The Yjs doc this editor's content lives in. Defaults to a fresh local
   * `Y.Doc` if omitted (P1's scaffold behavior — never synced anywhere).
   * Real live sessions (Track B) and multi-client verification (two editors
   * sharing two relayed docs) both need to supply their own. */
  doc?: Y.Doc;
  /** This client's Awareness instance (Yjs's ephemeral, non-document shared
   * state — presence/cursor data, separate from `doc`'s content). Defaults
   * to a fresh local instance tied to `doc` if omitted, owned and destroyed
   * by this component; a caller-supplied one (a real live session, or
   * multi-client verification wiring its own relay) is left for the caller
   * to destroy. */
  awareness?: Awareness;
  /** This client's id for presence/coloring — a stable random id if
   * omitted. Distinct clients sharing a `doc` must pass distinct ids. */
  userId?: string;
  /** This client's display name, shown on its caret in peers' views. */
  userDisplay?: string;
  /** Render without accepting edits. */
  readOnly?: boolean;
  /** The wiki page path this editor is editing, used to scope image uploads
   * (`POST /api/wiki/media?path=...`). Omitted by the scaffold/multi-client
   * verification harness, which has no real page - image paste/drop then
   * falls through to default handling instead of uploading. */
  pagePath?: string;
  /** Comment thread spans to highlight in the doc. */
  commentHighlights?: CommentHighlightTarget[];
  /** Thread ids whose spans get the stronger (active) highlight. */
  activeCommentIds?: string[];
  /** Fires with the thread ids whose spans contain the caret (or intersect
   * the selection), deduped against the last report. */
  onCommentCaret?: (ids: string[]) => void;
  /** Source-attributed spans to highlight while the Sources tab is open. */
  sourceHighlights?: AnchoredHighlightTarget[];
  /** Source keys whose spans get the stronger (active) highlight. */
  activeSourceIds?: string[];
  /** Fires with the source ids whose spans contain the caret (or intersect
   * the selection), deduped against the last report. */
  onSourceCaret?: (ids: string[]) => void;
  /** Fires on every selection change with the current selection as a
   * comment draft (null if collapsed) plus its on-screen coordinates, for
   * the caller to position a floating "Comment" affordance. */
  onSelectionForComment?: (
    draft: CommentDraft | null,
    coords: { x: number; y: number } | null,
  ) => void;
  /** Fires once the underlying Tiptap `Editor` is created — for callers that
   * need the raw editor instance itself (seeding content, computing document
   * positions for a test harness), distinct from the imported `CoeditorHandle`
   * (in `@/lib/editor/types`), which covers the scroll/geometry surface other
   * components actually depend on. */
  onEditorReady?: (editor: Editor) => void;
  /** Imperative handle exposing the editor's scroll/geometry surface
   * (`CoeditorHandle`). A plain prop in React 19 — no `forwardRef` wrapper. */
  ref?: Ref<CoeditorHandle>;
}
export function TipTapEditor({
  doc: docProp,
  awareness: awarenessProp,
  userId,
  userDisplay,
  readOnly,
  pagePath,
  commentHighlights,
  activeCommentIds,
  onCommentCaret,
  sourceHighlights,
  activeSourceIds,
  onSourceCaret,
  onSelectionForComment,
  onEditorReady,
  ref,
}: TipTapEditorProps) {
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
  const [localId] = useState(() => userId ?? opaqueId());
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
      // Per-session, not per-user: the same user's second tab is its own
      // caret with its own colour (chips read this advertised colour back
      // out of awareness, so both surfaces stay in agreement).
      color: sessionColorFor(localId, awareness.clientID),
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
    extensions: tiptapExtensions(doc, awareness, pagePath),
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
          const quotedText = docTextBetween(editor, from, to);
          // The server re-anchors by the quote, and a whitespace-only quote
          // matches anywhere.
          if (!quotedText.trim()) {
            cb(null, null);
          } else {
            const draft: CommentDraft = {
              startOffset: pmPosToTextOffset(editor, from),
              endOffset: pmPosToTextOffset(editor, to),
              quotedText,
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
}

/* Live-session presence: who else is on the page — labeled "editing" while
   their cursor is rendered in the content (yCursorPlugin), "viewing"
   otherwise — and who's typing right now. The label is derived from the
   same peers list that renders the carets, so bar and doc can never
   disagree. Renders nothing when you're alone. Ported verbatim from
   `lib/editor/components.tsx`'s component of the same name. */
export interface CoeditPresenceBarProps {
  participants: CoeditParticipant[];
  /** Peers with a live cursor (from `useCoeditSession`) — a participant
   * with an entry here is "editing", the rest are "viewing". */
  peers: CoeditPeer[];
  typing: string[];
  selfUserId: string | null;
}
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

export interface CommandMenuHandle {
  onKeyDown: (props: SuggestionKeyDownProps) => boolean;
}
export type CommandMenuProps = {
  ref?: Ref<CommandMenuHandle>;
} & SuggestionProps<CommandItem>;
export function CommandMenu({ ref, items, command }: CommandMenuProps) {
  const [selected, setSelected] = useState(0);
  useEffect(() => setSelected(0), [items]);

  const select = (index: number) => {
    const item = items[index];
    if (item) command(item);
  };

  useImperativeHandle(ref, () => ({
    onKeyDown: ({ event }) => {
      if (event.key === "ArrowDown") {
        setSelected((s) => (items.length ? (s + 1) % items.length : 0));
        return true;
      }
      if (event.key === "ArrowUp") {
        setSelected((s) =>
          items.length ? (s - 1 + items.length) % items.length : 0,
        );
        return true;
      }
      if (event.key === "Enter") {
        select(selected);
        return true;
      }
      return false;
    },
  }));

  if (items.length === 0) return null;

  return (
    <div className="max-h-[320px] w-[220px] overflow-y-auto rounded-(--radius-08) border border-(--border-01) bg-(--background-tint-00) p-1 shadow-[0px_2px_6px_var(--shadow-02),0px_0px_2px_var(--shadow-01)]">
      {items.map((item, i) => (
        // The wrapper carries the keyboard-selection highlight rather than
        // relying on LineItemButton's own "selected" state, which is a hover-
        // weight tint — too quiet for the thing Enter is about to apply. A
        // solid fill and nothing else, as Notion's menu does it.
        <div
          key={item.title}
          className={
            i === selected
              ? "rounded-(--radius-06) bg-(--background-tint-03)"
              : undefined
          }
        >
          <LineItemButton
            title={item.title}
            icon={item.icon}
            sizePreset="main-ui"
            variant="section"
            state={i === selected ? "selected" : "empty"}
            onClick={() => select(i)}
          />
        </div>
      ))}
    </div>
  );
}
