"use client";

/** CodeMirror rendering extensions and the co-edit editor component. */
import {
  collab,
  getSyncedVersion,
  receiveUpdates,
  sendableUpdates,
} from "@codemirror/collab";
import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import { markdown, markdownLanguage } from "@codemirror/lang-markdown";
import {
  ChangeSet,
  Compartment,
  EditorState,
  StateEffect,
  StateField,
} from "@codemirror/state";
import {
  Decoration,
  type DecorationSet,
  EditorView,
  keymap,
  placeholder as placeholderExt,
  WidgetType,
} from "@codemirror/view";
import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import { ApiError } from "@/lib/api";
import type {
  CoeditFrame,
  CoeditParticipant,
  CoeditPeer,
  CoeditSessionHandle,
} from "@/lib/editor/types";
import { getOps, sendOp } from "@/lib/editor/svc";
import { IDLE_UNFOCUS_MS } from "@/lib/editor/constants";
import {
  changeSetToChanges,
  colorFor,
  syncedDocLength,
} from "@/lib/editor/utils";
import { wysiwygMarkdown } from "@/lib/editor/wysiwyg";
import {
  commentsField,
  selectionToDraft,
  setActiveCommentHighlightsEffect,
  setCommentHighlightsEffect,
  type CommentDraft,
  type CommentHighlightTarget,
} from "@/lib/editor/comments";
import {
  sourceHighlights as sourceHighlightsExt,
  type AnchoredHighlightTarget,
} from "@/lib/editor/highlights";

/** A remote peer's caret: a thin colored bar with a small name label above it. */
class CaretWidget extends WidgetType {
  constructor(
    readonly color: string,
    readonly label: string,
  ) {
    super();
  }
  eq(other: CaretWidget) {
    return other.color === this.color && other.label === this.label;
  }
  toDOM() {
    const wrap = document.createElement("span");
    wrap.className = "cm-coedit-caret";
    wrap.style.borderColor = this.color;
    const tag = document.createElement("span");
    tag.className = "cm-coedit-caret-label";
    tag.style.background = this.color;
    tag.textContent = this.label;
    wrap.appendChild(tag);
    return wrap;
  }
  /** Zero-width; never let the editor treat it as an edit boundary. */
  ignoreEvent() {
    return true;
  }
}

/** Dispatched to update the peer list in `peersField`. */
const setPeersEffect = StateEffect.define<CoeditPeer[]>();

/** Build a `DecorationSet` from the current peer list: one selection highlight
 * per non-collapsed selection and one `CaretWidget` per peer head position.
 * Offsets are clamped to `docLen` so a stale frame never lands out of range. */
function buildPeerDecorations(
  peers: CoeditPeer[],
  docLen: number,
): DecorationSet {
  const ranges = [];
  for (const p of peers) {
    const anchor = Math.max(0, Math.min(p.anchor, docLen));
    const head = Math.max(0, Math.min(p.head, docLen));
    const from = Math.min(anchor, head);
    const to = Math.max(anchor, head);
    const color = colorFor(p.user_id);
    if (from !== to) {
      ranges.push(
        Decoration.mark({
          attributes: { style: `background-color:${color}33` },
        }).range(from, to),
      );
    }
    ranges.push(
      Decoration.widget({
        widget: new CaretWidget(color, p.user_display),
        side: 1,
      }).range(head),
    );
  }
  return Decoration.set(ranges, true);
}

/** Holds the peer list + its decorations; provides decorations to the view via
 * `EditorView.decorations`. A parked caret must keep pointing at the text its
 * owner left it in, so held positions are mapped through every doc change.
 * When a new peer array arrives, raw offsets are adopted only for entries with
 * a fresh cursor frame (`seq` advanced) — an entry re-sent unchanged (the
 * array was rebuilt by some other peer's frame) keeps its mapped position,
 * since the raw offsets it carries are relative to an older doc. */
const peersField = StateField.define<{
  peers: CoeditPeer[];
  deco: DecorationSet;
}>({
  create: () => ({ peers: [], deco: Decoration.none }),
  update(value, tr) {
    let incoming: CoeditPeer[] | null = null;
    for (const e of tr.effects) if (e.is(setPeersEffect)) incoming = e.value;
    if (incoming === null && !tr.docChanged) return value;
    let peers = value.peers;
    if (tr.docChanged) {
      peers = peers.map((p) => ({
        ...p,
        anchor: tr.changes.mapPos(p.anchor),
        head: tr.changes.mapPos(p.head),
      }));
    }
    if (incoming !== null) {
      const held = new Map(peers.map((p) => [p.user_id, p]));
      peers = incoming.map((p) => {
        const h = held.get(p.user_id);
        return h && h.seq === p.seq ? h : p;
      });
    }
    return { peers, deco: buildPeerDecorations(peers, tr.state.doc.length) };
  },
  provide: (f) => EditorView.decorations.from(f, (v) => v.deco),
});

/** Base CodeMirror theme: full-height layout, borderless (the WYSIWYG surface
 * reads as part of the page, not a boxed textarea), prose-font scroller,
 * Opal tokens, and styles for the `.cm-coedit-caret` / `.cm-coedit-caret-label`
 * widgets. The scroller spans the full width so its scrollbar sits flush at
 * the far-right edge; the text is capped and centered by `.cm-content`
 * (`max-width` + `margin: auto`) to line up with the `max-w-[768px]`
 * `DocTitle` above it. `.cm-scroller` takes its horizontal gutter from the
 * `--cm-gutter` custom property that the surrounding column sets (so the text
 * gutter tracks the page's responsive padding at every breakpoint), and it
 * uses a slim scrollbar with a transparent track. */
// Every anchored-highlight mark class, for selectors that must match all.
const ANCHOR_HIGHLIGHT =
  ":is(.cm-comment-highlight, .cm-comment-highlight-active, .cm-source-highlight, .cm-source-highlight-active)";

const baseTheme = EditorView.theme({
  "&": {
    height: "100%",
    // Opal's Main Content/Body preset (see `font-main-content-body` in
    // @onyx-ai/opal styles.css) — the doc text matches the design system's
    // content type ramp. CM manages these nodes, so the utility class can't
    // be applied directly; reference the same tokens instead.
    fontSize: "var(--height-font-label, 1rem)",
    fontWeight: "450",
    color: "var(--text-04)",
  },
  "&.cm-focused": { outline: "none" },
  ".cm-scroller": {
    overflow: "auto",
    // The app's font (set on <html> by next/font in layout.tsx) — the doc
    // text must match DocTitle and the rest of the chrome.
    fontFamily: "var(--font-hanken-grotesk, system-ui, sans-serif)",
    lineHeight: "var(--height-line-label, 1.5rem)",
    padding: "0 var(--cm-gutter, 2rem)",
    scrollbarWidth: "thin",
    scrollbarColor: "var(--border-03) transparent",
  },
  ".cm-scroller::-webkit-scrollbar": { width: "12px", height: "12px" },
  ".cm-scroller::-webkit-scrollbar-track": { backgroundColor: "transparent" },
  ".cm-scroller::-webkit-scrollbar-thumb": {
    backgroundColor: "var(--border-03)",
    borderRadius: "9999px",
    border: "3px solid transparent",
    backgroundClip: "content-box",
  },
  ".cm-scroller::-webkit-scrollbar-thumb:hover": {
    backgroundColor: "var(--text-04)",
  },
  ".cm-content": {
    padding: "0.5rem 0",
    width: "100%",
    maxWidth: "768px",
    marginInline: "auto",
    caretColor: "var(--text-05)",
  },
  ".cm-line": { padding: "0" },
  ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--text-05)" },
  "&.cm-focused .cm-selectionBackground, ::selection": {
    backgroundColor: "var(--background-tint-03)",
  },
  ".cm-md-h1, .cm-md-h2, .cm-md-h3, .cm-md-h4, .cm-md-h5, .cm-md-h6": {
    fontWeight: "bold",
    display: "inline-block",
  },
  ".cm-md-h1": { fontSize: "2em", marginTop: "0.5em" },
  ".cm-md-h2": { fontSize: "1.6em", marginTop: "0.5em" },
  ".cm-md-h3": { fontSize: "1.375em", marginTop: "0.4em" },
  ".cm-md-h4": { fontSize: "1.25em", marginTop: "0.4em" },
  ".cm-md-h5": { fontSize: "1.125em", marginTop: "0.3em" },
  ".cm-md-h6": { fontSize: "1.125em", marginTop: "0.3em", opacity: "0.85" },
  ".cm-md-strong": { fontWeight: "bold" },
  ".cm-md-em": { fontStyle: "italic" },
  ".cm-md-code-inline": {
    fontFamily: "var(--font-dm-mono, ui-monospace, monospace)",
    backgroundColor: "var(--background-tint-01)",
    borderRadius: "var(--radius-04, 4px)",
    padding: "0.1em 0.3em",
  },
  ".cm-md-code-block": {
    fontFamily: "var(--font-dm-mono, ui-monospace, monospace)",
    display: "block",
    backgroundColor: "var(--background-tint-01)",
    borderRadius: "var(--radius-08)",
  },
  ".cm-md-blockquote": {
    display: "inline-block",
    borderLeft: "3px solid var(--border-02)",
    paddingLeft: "0.75em",
    color: "var(--text-03)",
  },
  ".cm-md-link": {
    color: "var(--accent-01)",
    textDecoration: "underline",
  },
  ".cm-md-list-bullet, .cm-md-list-number": {
    color: "var(--text-03)",
    userSelect: "none",
  },
  ".cm-md-task-checkbox": {
    width: "0.95em",
    height: "0.95em",
    margin: "0 0.35em 0 0",
    verticalAlign: "middle",
    accentColor: "var(--accent-01)",
    cursor: "pointer",
  },
  ".cm-md-hr": {
    display: "inline-block",
    width: "100%",
    borderTop: "1px solid var(--border-02)",
    verticalAlign: "middle",
  },
  // Idle threads at 30% amber (the mock's 20% reads as unhighlighted on
  // real content), the hovered/selected thread at Highlight/Active (60%).
  ".cm-comment-highlight": {
    backgroundColor: "var(--neon-amber-a30)",
  },
  ".cm-comment-highlight-active": {
    backgroundColor: "var(--highlight-active)",
  },
  // Source-attributed spans idle at the light amber, and the hovered
  // card's spans jump to Highlight/Active (mock 1832:81274) so a reader
  // can tell which highlight belongs to which source.
  ".cm-source-highlight": {
    backgroundColor: "var(--neon-amber-a30)",
  },
  ".cm-source-highlight-active": {
    backgroundColor: "var(--highlight-active)",
  },
  // Code marks nest inside highlight marks with an opaque tint that would
  // occlude the wrapping span's amber, so they repaint the highlight color
  // over their own background.
  ".cm-comment-highlight .cm-md-code-block, .cm-comment-highlight .cm-md-code-inline, .cm-source-highlight .cm-md-code-block, .cm-source-highlight .cm-md-code-inline":
    {
      backgroundImage:
        "linear-gradient(var(--neon-amber-a30), var(--neon-amber-a30))",
    },
  ".cm-comment-highlight-active .cm-md-code-block, .cm-comment-highlight-active .cm-md-code-inline, .cm-source-highlight-active .cm-md-code-block, .cm-source-highlight-active .cm-md-code-inline":
    {
      backgroundImage:
        "linear-gradient(var(--highlight-active), var(--highlight-active))",
    },
  // A highlight mark splits the line's block-display code span, and each
  // piece being a block would break the line at the highlight's boundaries.
  // Inline pieces keep the line whole and the amber hugging the text, the
  // same as highlights on plain content.
  [`.cm-line:has(${ANCHOR_HIGHLIGHT}) .cm-md-code-block`]: {
    display: "inline",
    borderRadius: "0",
  },
  ".cm-coedit-caret": {
    display: "inline-block",
    width: "0",
    borderLeft: "2px solid",
    marginLeft: "-1px",
    height: "1.2em",
    verticalAlign: "text-bottom",
    position: "relative",
  },
  ".cm-coedit-caret-label": {
    position: "absolute",
    top: "-1.15em",
    left: "-1px",
    fontSize: "10px",
    lineHeight: "1.2",
    padding: "0 3px",
    borderRadius: "3px",
    color: "var(--text-inverse, #fff)",
    whiteSpace: "nowrap",
    fontFamily: "var(--font-hanken-grotesk, system-ui, sans-serif)",
    pointerEvents: "none",
    userSelect: "none",
  },
});

interface CoeditorProps {
  session: CoeditSessionHandle;
  peers: CoeditPeer[];
  onSelectionChange: (anchor: number, head: number, isEdit: boolean) => void;
  /** Fires when the editor loses focus — the local caret is no longer placed,
   * so peers drop it and presence flips us to "viewing". */
  onCaretCleared: () => void;
  /** The current caret epoch while our caret is placed, else null — attached
   * to every op so an edit asserts caret placement with the same ordering as
   * cursor writes (see useCoeditSession.getCaretSeq). */
  getCaretSeq: () => number | null;
  onServerFrame: (handler: ((frame: CoeditFrame) => void) | null) => void;
  reportDoc: (doc: string) => void;
  registerFlush: (fn: (() => Promise<void>) | null) => void;
  registerSetDoc: (fn: ((text: string) => void) | null) => void;
  /** Register the editor's "pull missed ops" fn — the hook calls it after an
   * SSE reconnect and when the tab becomes visible again, so a returning user
   * catches up instead of interacting with a stale doc. */
  registerCatchUp: (fn: (() => void) | null) => void;
  placeholder?: string;
  /** Render the doc without accepting edits — for participants whose
   * `can_write` is false. They stay in the live session (presence + real-time
   * updates); only local mutation is disabled. */
  readOnly?: boolean;
  /** Comment thread spans to highlight in the doc. */
  commentHighlights?: CommentHighlightTarget[];
  /** Thread ids whose spans get the stronger (active) highlight. */
  activeCommentIds?: string[];
  /** Source-attributed spans to highlight while the Sources tab is open. */
  sourceHighlights?: AnchoredHighlightTarget[];
  /** Source keys whose spans get the stronger (active) highlight. */
  activeSourceIds?: string[];
  /** Fires with the source ids whose spans contain the caret (or intersect
   * the selection), deduped against the last report. */
  onSourceCaret?: (ids: string[]) => void;
  /** Fires on every selection change with the current selection as a comment
   * draft (null if collapsed) plus its on-screen coordinates, for the caller
   * to position a floating "Comment" affordance. */
  onSelectionForComment?: (
    draft: CommentDraft | null,
    coords: { x: number; y: number } | null,
  ) => void;
}

/** Imperative handle for scrolling the editor to a raw-doc offset — used to
 * bring an anchored comment into view (click-to-focus, `?comment=<id>` deep
 * links). */
export interface CoeditorHandle {
  scrollToOffset: (offset: number) => void;
  /** Scroll to a source's first attributed span, read from the highlight
   * field's live-mapped offsets so edits since the fetch are honored. */
  scrollToSource: (id: string) => void;
  /** The source highlight field's live-mapped targets, for hosts that
   * anchor UI to span positions (collapsed spans included, callers skip
   * them). */
  sourceTargets: () => AnchoredHighlightTarget[];
  /** Doc-space top and height (px from the document's start) of the line
   * block holding a character offset. Stable for off-screen positions
   * (line-block geometry, not rendered coordinates). */
  anchorLine: (offset: number) => { top: number; height: number } | null;
  /** The editor scroller's current scrollTop. */
  scrollTop: () => number;
  /** Scroll the editor by a wheel delta, for hosts outside the scroller. */
  scrollBy: (dy: number) => void;
  /** The scroller's total scrollHeight, the doc-space lower bound. */
  scrollHeight: () => number;
  /** The scroller's viewport height, for external scrollbar math. */
  clientHeight: () => number;
  /** Viewport-space top of the scroller, for hosts not sharing its origin. */
  scrollerTop: () => number;
  /** Subscribe to scroll and geometry changes. Scroll notifications fire
   * synchronously inside the scroll event so overlays can repaint in the
   * same frame as the editor. Returns the unsubscriber. */
  subscribeLayout: (cb: (kind: "scroll" | "geometry") => void) => () => void;
}

/** CodeMirror 6 editor that owns the co-edit document via `@codemirror/collab`.
 *
 * The editor is the source of truth for the doc + version; local edits push to
 * the server as ops and inbound ops/resync are fed to collab's `receiveUpdates`,
 * which rebases un-acked local edits through them — so your keystrokes never
 * revert on a concurrent remote edit. It also renders remote peers' carets and
 * selection highlights and reports the local caret. Offsets are UTF-16 code
 * units end-to-end (JS-native, matching the server).
 *
 * Ops are pushed one-per-version (our `/coedit/op` is one op per version); a
 * push is confirmed locally on 200 and the SSE echo is skipped as already-seen.
 * On a 409 or a version gap we pull the missed ops from `/coedit/ops`.
 */
export const Coeditor = forwardRef<CoeditorHandle, CoeditorProps>(
  function Coeditor(
    {
      session,
      peers,
      onSelectionChange,
      onCaretCleared,
      getCaretSeq,
      onServerFrame,
      reportDoc,
      registerFlush,
      registerSetDoc,
      registerCatchUp,
      placeholder,
      readOnly,
      commentHighlights,
      activeCommentIds,
      sourceHighlights,
      activeSourceIds,
      onSourceCaret,
      onSelectionForComment,
    },
    ref,
  ) {
    const host = useRef<HTMLDivElement | null>(null);
    const view = useRef<EditorView | null>(null);
    // Margin-rail subscribers, notified on scroll and geometry changes.
    const layoutSubs = useRef<Set<(kind: "scroll" | "geometry") => void>>(
      new Set(),
    );
    // Swappable slot for the read-only facets — see readOnlyExtensions.
    const readOnlyCompartment = useRef(new Compartment());
    // Latest callbacks without re-creating the editor.
    const onSelRef = useRef(onSelectionChange);
    const onCaretClearedRef = useRef(onCaretCleared);
    const getCaretSeqRef = useRef(getCaretSeq);
    const reportDocRef = useRef(reportDoc);
    const onSelectionForCommentRef = useRef(onSelectionForComment);
    const onSourceCaretRef = useRef(onSourceCaret);
    // Last caret-source report, so selection churn inside one span is quiet.
    const lastCaretIds = useRef("");
    const peersRef = useRef(peers);
    const commentHighlightsRef = useRef(commentHighlights);
    const activeCommentIdsRef = useRef(activeCommentIds);
    const sourceHighlightsRef = useRef(sourceHighlights);
    const activeSourceIdsRef = useRef(activeSourceIds);
    onSelRef.current = onSelectionChange;
    onCaretClearedRef.current = onCaretCleared;
    getCaretSeqRef.current = getCaretSeq;
    reportDocRef.current = reportDoc;
    onSelectionForCommentRef.current = onSelectionForComment;
    onSourceCaretRef.current = onSourceCaret;
    peersRef.current = peers;
    commentHighlightsRef.current = commentHighlights;
    activeCommentIdsRef.current = activeCommentIds;
    sourceHighlightsRef.current = sourceHighlights;
    activeSourceIdsRef.current = activeSourceIds;

    useImperativeHandle(
      ref,
      () => ({
        scrollToOffset: (offset: number) => {
          const v = view.current;
          if (!v) return;
          v.dispatch({
            effects: EditorView.scrollIntoView(
              Math.max(0, Math.min(offset, v.state.doc.length)),
              { y: "center" },
            ),
          });
        },
        sourceTargets: () => {
          const v = view.current;
          return v ? v.state.field(sourceHighlightsExt.field).targets : [];
        },
        scrollToSource: (id: string) => {
          const v = view.current;
          if (!v) return;
          // An edit can collapse a span to zero width, and a collapsed
          // target paints nothing, so it can't be the scroll destination.
          const target = v.state
            .field(sourceHighlightsExt.field)
            .targets.find((t) => t.id === id && t.startOffset < t.endOffset);
          if (!target) return;
          v.dispatch({
            effects: EditorView.scrollIntoView(
              Math.max(0, Math.min(target.startOffset, v.state.doc.length)),
              { y: "center" },
            ),
          });
        },
        anchorLine: (offset: number) => {
          const v = view.current;
          if (!v) return null;
          const pos = Math.max(0, Math.min(offset, v.state.doc.length));
          // Line blocks are defined document-wide, unlike coordsAtPos.
          // documentTop folds in the content padding. A block spans the
          // wrapped paragraph, so clamp to its first visual line.
          const contentOffset =
            v.documentTop -
            v.scrollDOM.getBoundingClientRect().top +
            v.scrollDOM.scrollTop;
          const block = v.lineBlockAt(pos);
          return {
            top: block.top + contentOffset,
            height: Math.min(block.height, v.defaultLineHeight),
          };
        },
        scrollTop: () => view.current?.scrollDOM.scrollTop ?? 0,
        scrollBy: (dy: number) => {
          const v = view.current;
          if (v) v.scrollDOM.scrollTop += dy;
        },
        scrollHeight: () => view.current?.scrollDOM.scrollHeight ?? 0,
        clientHeight: () => view.current?.scrollDOM.clientHeight ?? 0,
        scrollerTop: () =>
          view.current?.scrollDOM.getBoundingClientRect().top ?? 0,
        subscribeLayout: (cb: (kind: "scroll" | "geometry") => void) => {
          layoutSubs.current.add(cb);
          return () => {
            layoutSubs.current.delete(cb);
          };
        },
      }),
      // No deps: the handle re-attaches every render, so a live session
      // never holds an object missing later-added methods.
    );

    // Create the collab editor once per session.
    useEffect(() => {
      if (!host.current) return;
      let v: EditorView;
      const pushing = { current: false };
      const applyingRemote = { current: false };
      const pullPromise = { current: null as Promise<void> | null };
      const pullQueued = { current: false };
      // The synced version we last sent an op at — so we don't re-send the same
      // op while awaiting its echo (which advances synced past this).
      const sentAtVersion = { current: -1 };

      // Idle auto-unfocus: N minutes without local activity blurs the editor.
      // The blur runs the normal focus-loss path (caret clear broadcast,
      // presence flips to "viewing", reveal-on-focus collapses to preview),
      // so an untouched tab can't hold an "editing" caret indefinitely. The
      // timer is armed only while focused and re-armed on every local edit /
      // caret move; suspended timers fire on wake, so a slept laptop unfocuses
      // right when the user returns.
      const idleUnfocus = {
        current: null as ReturnType<typeof setTimeout> | null,
      };
      const armIdleUnfocus = () => {
        if (idleUnfocus.current) clearTimeout(idleUnfocus.current);
        idleUnfocus.current = null;
        if (!v.hasFocus) return;
        idleUnfocus.current = setTimeout(() => {
          idleUnfocus.current = null;
          if (v.hasFocus) v.contentDOM.blur();
        }, IDLE_UNFOCUS_MS);
      };

      const dispatchRemote = (spec: Parameters<EditorView["dispatch"]>[0]) => {
        applyingRemote.current = true;
        try {
          v.dispatch(spec);
        } finally {
          applyingRemote.current = false;
        }
      };

      // Push the first un-acked op at the synced version (our /op is one op per
      // version). We do NOT confirm locally — the op echoes back over the stream
      // (client_id === ours) and is confirmed via receiveUpdates like any other,
      // so every client applies the same ordered sequence (the convergence
      // invariant; self-confirming out-of-band diverged clients). The next op is
      // sent once this one's echo confirms + clears it (see the applyFrame/pull
      // tails). A 409 means we missed ops → pull + rebase, then a tail re-pushes.
      const doPush = async (): Promise<void> => {
        if (pushing.current) return;
        const version = getSyncedVersion(v.state);
        // Already sent the op at this version; wait for its echo to advance
        // synced before sending the next (else we'd re-send and 409).
        if (version === sentAtVersion.current) return;
        const updates = sendableUpdates(v.state);
        if (updates.length === 0) return;
        pushing.current = true;
        try {
          await sendOp(
            session.id,
            version,
            changeSetToChanges(updates[0]!.changes),
            session.clientId,
            getCaretSeqRef.current(),
          );
          sentAtVersion.current = version;
        } catch (e) {
          if (e instanceof ApiError && e.status === 409) await pull();
          // else transient — the op stays sendable; a later echo/edit retries
        } finally {
          pushing.current = false;
        }
        // Re-push if there's still un-acked work at a *new* synced version — the
        // 409→pull path above rebased our op onto a later version, and pull's own
        // tail push was blocked by `pushing`. The `sentAtVersion` guard makes this
        // a no-op after a clean send (we wait for that op's echo instead).
        if (getSyncedVersion(v.state) !== sentAtVersion.current) void doPush();
      };

      // Single-flight: overlapping pulls would both anchor at the same synced
      // version and re-apply the same ops (duplicate insertions). A concurrent
      // caller gets the in-flight promise (so a 409 `await pull()` waits for the
      // real result); a gap that arrives mid-pull queues one more run.
      const pull = (): Promise<void> => {
        if (pullPromise.current) {
          pullQueued.current = true;
          return pullPromise.current;
        }
        const run = (async () => {
          const synced = getSyncedVersion(v.state);
          let data;
          try {
            data = await getOps(session.id, synced);
          } catch {
            return; // transient; a later trigger retries
          }
          if (data.ops.length === 0) return;
          let len = syncedDocLength(v.state);
          const updates = data.ops.map((o) => {
            const cs = ChangeSet.of(o.changes, len);
            len = cs.newLength;
            return { changes: cs, clientID: o.client_id ?? "" };
          });
          dispatchRemote(receiveUpdates(v.state, updates));
          if (sendableUpdates(v.state).length > 0) void doPush();
        })();
        pullPromise.current = run;
        void run.finally(() => {
          pullPromise.current = null;
          if (pullQueued.current) {
            pullQueued.current = false;
            void pull();
          }
        });
        return run;
      };

      const applyFrame = (frame: CoeditFrame): void => {
        if (frame.type === "resync") {
          void pull();
          return;
        }
        if (frame.type !== "op") return;
        // A pull in flight is fetching the authoritative op sequence; applying a
        // frame now would double-apply. Queue a re-pull so an op that landed
        // after the pull's snapshot is still fetched, and drop this frame.
        if (pullPromise.current) {
          pullQueued.current = true;
          return;
        }
        const synced = getSyncedVersion(v.state);
        if (frame.version <= synced) return; // already applied (incl. our echo)
        if (frame.version > synced + 1) {
          void pull(); // a gap (missed frame / big-op resync) → fetch the ops
          return;
        }
        try {
          const cs = ChangeSet.of(frame.changes, syncedDocLength(v.state));
          dispatchRemote(
            receiveUpdates(v.state, [
              { changes: cs, clientID: frame.client_id ?? "" },
            ]),
          );
        } catch (err) {
          // A malformed / mis-anchored op would otherwise be swallowed by the SSE
          // reader. Surface it and re-sync from the authoritative op log.
          console.error("coedit: failed to apply op frame; re-syncing", err);
          void pull();
        }
        if (sendableUpdates(v.state).length > 0) void doPush();
      };

      const notifyLayout = (kind: "scroll" | "geometry") => {
        for (const cb of layoutSubs.current) cb(kind);
      };
      const updateListener = EditorView.updateListener.of((u) => {
        if (u.docChanged) {
          reportDocRef.current(u.state.doc.toString());
          void doPush();
        }
        if (u.geometryChanged || u.docChanged) notifyLayout("geometry");
        // Caret-to-source attribution against the field's live-mapped spans.
        // Runs above the remote-op return and also on field-value changes,
        // since remote edits and effect-only target swaps move or clear the
        // spans under a parked caret.
        const sourceField = sourceHighlightsExt.field;
        if (
          (u.selectionSet ||
            u.docChanged ||
            u.startState.field(sourceField) !== u.state.field(sourceField)) &&
          onSourceCaretRef.current
        ) {
          const { from, to } = u.state.selection.main;
          const ids: string[] = [];
          for (const t of u.state.field(sourceField).targets) {
            const hit =
              from === to
                ? from >= t.startOffset && from < t.endOffset
                : from < t.endOffset && to > t.startOffset;
            if (hit && !ids.includes(t.id)) ids.push(t.id);
          }
          const key = ids.join("\n");
          if (key !== lastCaretIds.current) {
            lastCaretIds.current = key;
            onSourceCaretRef.current(ids);
          }
        }
        // Remote-applied transactions aren't local input — don't report them as
        // our caret/typing.
        if (applyingRemote.current) return;
        const sel = u.state.selection.main;
        // Focus is caret presence: losing it clears our caret for peers;
        // regaining it re-reports the position the clear removed.
        if (u.focusChanged) {
          if (u.view.hasFocus) onSelRef.current(sel.anchor, sel.head, false);
          else onCaretClearedRef.current();
        }
        if (u.docChanged) onSelRef.current(sel.anchor, sel.head, true);
        else if (u.selectionSet) onSelRef.current(sel.anchor, sel.head, false);
        // Any local activity (or a focus flip) restarts the idle-unfocus
        // clock. Remote-applied ops can't reach this line: CM update
        // listeners run synchronously inside dispatchRemote's
        // applyingRemote bracket, so they exit at the early-return above —
        // a busy peer can't keep an idle user's caret alive.
        if (u.focusChanged || u.docChanged || u.selectionSet) armIdleUnfocus();
        if (u.selectionSet && onSelectionForCommentRef.current) {
          const draft = selectionToDraft(u.state);
          const coords = draft ? u.view.coordsAtPos(sel.head) : null;
          onSelectionForCommentRef.current(
            draft,
            coords ? { x: coords.left, y: coords.top } : null,
          );
        }
      });

      const state = EditorState.create({
        doc: session.startDoc,
        extensions: [
          collab({
            startVersion: session.startVersion,
            clientID: session.clientId,
          }),
          history(),
          keymap.of([...defaultKeymap, ...historyKeymap]),
          // GFM base (not the commonmark default) so task-list markers parse
          // as TaskMarker.
          markdown({ base: markdownLanguage }),
          wysiwygMarkdown(),
          EditorView.lineWrapping,
          // In a compartment so a later `readOnly` prop change reconfigures
          // the live editor (facets are otherwise baked in at create time —
          // e.g. can_write flipping after a permissions change must not
          // leave the editor writable).
          readOnlyCompartment.current.of(readOnlyExtensions(!!readOnly)),
          placeholderExt(placeholder ?? ""),
          peersField,
          commentsField,
          sourceHighlightsExt.field,
          baseTheme,
          updateListener,
        ],
      });
      v = new EditorView({ state, parent: host.current });
      view.current = v;
      // A fresh state starts with empty peer/highlight fields, and the
      // prop-tracking effects below only fire on identity change. Seed both.
      v.dispatch({
        effects: [
          setPeersEffect.of(peersRef.current),
          setCommentHighlightsEffect.of(commentHighlightsRef.current ?? []),
          setActiveCommentHighlightsEffect.of(
            activeCommentIdsRef.current ?? [],
          ),
          sourceHighlightsExt.setTargets.of(sourceHighlightsRef.current ?? []),
          sourceHighlightsExt.setActive.of(activeSourceIdsRef.current ?? []),
        ],
      });
      const onScroll = () => notifyLayout("scroll");
      v.scrollDOM.addEventListener("scroll", onScroll, { passive: true });
      onServerFrame(applyFrame);
      registerFlush(async () => {
        // Deliver every un-acked local op over plain HTTP, treating a 200 as
        // delivered. Unlike `doPush`, this must not wait for stream echoes to
        // drain `sendableUpdates` — flush runs during teardown, when the SSE
        // stream (and even the view) may be gone, so an echo may never arrive.
        // The server CAS makes a duplicate send harmless (409). Time out
        // rather than hang / silently checkpoint a stale buffer.
        const deadline = Date.now() + 5000;
        while (true) {
          if (Date.now() > deadline) {
            throw new Error(
              "Could not sync your latest edits — check your connection.",
            );
          }
          if (pushing.current) {
            // An op is in flight (doPush or a concurrent flush) — wait for it
            // rather than double-sending at the same version.
            await new Promise((r) => setTimeout(r, 40));
            continue;
          }
          const updates = sendableUpdates(v.state);
          if (updates.length === 0) return;
          let version = getSyncedVersion(v.state);
          let start = 0;
          if (version === sentAtVersion.current) {
            // updates[0] already got its 200 (it's only un-drained because its
            // echo hasn't landed) — the rest are based one version later.
            start = 1;
            version += 1;
            if (updates.length === 1) return;
          }
          pushing.current = true;
          try {
            for (let i = start; i < updates.length; i++) {
              await sendOp(
                session.id,
                version,
                changeSetToChanges(updates[i]!.changes),
                session.clientId,
                // Null after a blur/teardown clear — the flushed tail then
                // makes no caret assertion, so it can't resurrect the caret.
                getCaretSeqRef.current(),
              );
              sentAtVersion.current = version;
              version += 1;
            }
            return;
          } catch (e) {
            if (e instanceof ApiError && e.status === 409) {
              // A peer's op interleaved — rebase through the op log and retry.
              // Safe even when the teardown flush runs after `v.destroy()`:
              // CM6's dispatch on a destroyed view still advances the state
              // (it only skips DOM work — see EditorView.update's destroyed
              // path), so receiveUpdates rebases and the retry sees it.
              await pull();
              continue;
            }
            throw e;
          } finally {
            pushing.current = false;
          }
        }
      });
      registerSetDoc((text: string) => {
        v.dispatch({
          changes: { from: 0, to: v.state.doc.length, insert: text },
        });
      });
      registerCatchUp(() => void pull());

      return () => {
        onServerFrame(null);
        // Deliberately NOT registerFlush(null): the session teardown in
        // useCoeditSession runs after this cleanup on unmount and needs the
        // flush for its final flush → checkpoint → leave sequence. The flush
        // only reads `v.state` and POSTs — both safe after `v.destroy()` —
        // and the hook clears it once it has taken ownership.
        registerSetDoc(null);
        registerCatchUp(null);
        if (idleUnfocus.current) clearTimeout(idleUnfocus.current);
        // A focused editor unmounting (in-app navigation, session
        // replacement) never gets a focusChanged update — clear our caret
        // for peers explicitly instead of leaving it parked until the
        // server-side session leave catches up.
        if (v.hasFocus) onCaretClearedRef.current();
        v.scrollDOM.removeEventListener("scroll", onScroll);
        v.destroy();
        view.current = null;
      };
      // Recreate the editor when the session changes; callbacks come via refs.
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [session.id, session.clientId, session.startVersion]);

    // Push peer carets into the editor state.
    useEffect(() => {
      view.current?.dispatch({ effects: setPeersEffect.of(peers) });
    }, [peers]);

    // Push comment highlight spans into the editor state.
    useEffect(() => {
      view.current?.dispatch({
        effects: setCommentHighlightsEffect.of(commentHighlights ?? []),
      });
    }, [commentHighlights]);

    // Push the active/hovered thread ids separately, so a hover flip while
    // the local doc is ahead of the server can't reset mapped offsets.
    useEffect(() => {
      view.current?.dispatch({
        effects: setActiveCommentHighlightsEffect.of(activeCommentIds ?? []),
      });
    }, [activeCommentIds]);

    // Push source-attributed spans into the editor state.
    useEffect(() => {
      view.current?.dispatch({
        effects: sourceHighlightsExt.setTargets.of(sourceHighlights ?? []),
      });
    }, [sourceHighlights]);

    // Hovered source keys ride their own effect, like comment actives.
    useEffect(() => {
      view.current?.dispatch({
        effects: sourceHighlightsExt.setActive.of(activeSourceIds ?? []),
      });
    }, [activeSourceIds]);

    // Reconfigure the read-only facets when the prop changes — they're baked
    // into the state at create time otherwise, and a `can_write` correction
    // (e.g. permissions revoked mid-session) must not leave the editor
    // writable.
    useEffect(() => {
      view.current?.dispatch({
        effects: readOnlyCompartment.current.reconfigure(
          readOnlyExtensions(!!readOnly),
        ),
      });
    }, [readOnly]);

    return (
      <div
        ref={host}
        className="box-border min-h-0 w-full flex-1 overflow-hidden"
      />
    );
  },
);

/** The facets that make the editor a pure preview for `readOnly` viewers.
 * Always installed through `readOnlyCompartment` so a prop change can
 * reconfigure the live editor. */
function readOnlyExtensions(readOnly: boolean) {
  return [EditorState.readOnly.of(readOnly), EditorView.editable.of(!readOnly)];
}

interface CoeditPresenceBarProps {
  participants: CoeditParticipant[];
  /** Peers with a live caret (from `useCoeditSession`) — a participant with
   * an entry here is "editing", the rest are "viewing". */
  peers: CoeditPeer[];
  typing: string[];
  selfUserId: string | null;
}

// Live-session presence: who else is on the page — labeled "editing" while
// their caret is rendered in the content, "viewing" otherwise — and who's
// typing right now. The label is DERIVED from the same peers list that
// renders the carets (CaretWidget/peersField above), so bar and doc can
// never disagree: if a person's cursor is in the content they're editing,
// otherwise they're viewing. Renders nothing when you're alone.
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
