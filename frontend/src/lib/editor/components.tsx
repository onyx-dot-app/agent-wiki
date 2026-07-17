"use client";

/** CodeMirror rendering extensions and the co-edit editor component. */
import {
  collab,
  getSyncedVersion,
  receiveUpdates,
  sendableUpdates,
} from "@codemirror/collab";
import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import { markdown } from "@codemirror/lang-markdown";
import {
  ChangeSet,
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
import {
  changeSetToChanges,
  colorFor,
  syncedDocLength,
} from "@/lib/editor/utils";
import { wysiwygMarkdown } from "@/lib/editor/wysiwyg";
import {
  commentsField,
  selectionToDraft,
  setCommentHighlightsEffect,
  type CommentDraft,
  type CommentHighlightTarget,
} from "@/lib/editor/comments";

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

/** Holds the peer list + its decorations. Rebuilds when peers change or the doc
 * changes (keeps offsets in range as text is edited); provides decorations to
 * the view via `EditorView.decorations`. */
const peersField = StateField.define<{
  peers: CoeditPeer[];
  deco: DecorationSet;
}>({
  create: () => ({ peers: [], deco: Decoration.none }),
  update(value, tr) {
    let peers = value.peers;
    for (const e of tr.effects) if (e.is(setPeersEffect)) peers = e.value;
    if (peers === value.peers && !tr.docChanged) return value;
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
const baseTheme = EditorView.theme({
  "&": {
    height: "100%",
    fontSize: "0.875rem",
    color: "var(--text-05)",
  },
  "&.cm-focused": { outline: "none" },
  ".cm-scroller": {
    overflow: "auto",
    fontFamily: "var(--font-sans, system-ui, -apple-system, sans-serif)",
    lineHeight: "1.6",
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
    fontFamily:
      "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
    backgroundColor: "var(--background-tint-01)",
    borderRadius: "var(--radius-04, 4px)",
    padding: "0.1em 0.3em",
  },
  ".cm-md-code-block": {
    fontFamily:
      "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
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
  ".cm-comment-highlight": {
    backgroundColor: "var(--status-warning-01)",
  },
  ".cm-comment-highlight-active": {
    backgroundColor: "var(--status-warning-02)",
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
    fontFamily: "var(--font-sans, system-ui)",
    pointerEvents: "none",
    userSelect: "none",
  },
});

interface CoeditorProps {
  session: CoeditSessionHandle;
  peers: CoeditPeer[];
  onSelectionChange: (anchor: number, head: number, isEdit: boolean) => void;
  onServerFrame: (handler: ((frame: CoeditFrame) => void) | null) => void;
  reportDoc: (doc: string) => void;
  registerFlush: (fn: (() => Promise<void>) | null) => void;
  registerSetDoc: (fn: ((text: string) => void) | null) => void;
  placeholder?: string;
  /** Comment thread spans to highlight in the doc (the active/selected thread
   * gets the stronger highlight). */
  commentHighlights?: CommentHighlightTarget[];
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
      onServerFrame,
      reportDoc,
      registerFlush,
      registerSetDoc,
      placeholder,
      commentHighlights,
      onSelectionForComment,
    },
    ref,
  ) {
    const host = useRef<HTMLDivElement | null>(null);
    const view = useRef<EditorView | null>(null);
    // Latest callbacks without re-creating the editor.
    const onSelRef = useRef(onSelectionChange);
    const reportDocRef = useRef(reportDoc);
    const onSelectionForCommentRef = useRef(onSelectionForComment);
    onSelRef.current = onSelectionChange;
    reportDocRef.current = reportDoc;
    onSelectionForCommentRef.current = onSelectionForComment;

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
      }),
      [],
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

      const updateListener = EditorView.updateListener.of((u) => {
        if (u.docChanged) {
          reportDocRef.current(u.state.doc.toString());
          void doPush();
        }
        // Remote-applied transactions aren't local input — don't report them as
        // our caret/typing.
        if (applyingRemote.current) return;
        const sel = u.state.selection.main;
        if (u.docChanged) onSelRef.current(sel.anchor, sel.head, true);
        else if (u.selectionSet) onSelRef.current(sel.anchor, sel.head, false);
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
          markdown(),
          wysiwygMarkdown(),
          EditorView.lineWrapping,
          placeholderExt(placeholder ?? ""),
          peersField,
          commentsField,
          baseTheme,
          updateListener,
        ],
      });
      v = new EditorView({ state, parent: host.current });
      view.current = v;
      onServerFrame(applyFrame);
      registerFlush(async () => {
        // Drive the push chain, then wait until every op is confirmed (sendable
        // drained by echoes) so the server has all our edits before checkpoint.
        // Time out rather than hang / silently checkpoint a stale buffer.
        const deadline = Date.now() + 5000;
        void doPush();
        while (sendableUpdates(v.state).length > 0) {
          if (Date.now() > deadline) {
            throw new Error(
              "Could not sync your latest edits — check your connection.",
            );
          }
          await new Promise((r) => setTimeout(r, 40));
          void doPush();
        }
      });
      registerSetDoc((text: string) => {
        v.dispatch({
          changes: { from: 0, to: v.state.doc.length, insert: text },
        });
      });

      return () => {
        onServerFrame(null);
        registerFlush(null);
        registerSetDoc(null);
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

    return (
      <div
        ref={host}
        className="box-border min-h-0 w-full flex-1 overflow-hidden"
      />
    );
  },
);

interface CoeditPresenceBarProps {
  participants: CoeditParticipant[];
  typing: string[];
  selfUserId: string | null;
}

// Co-editing presence: who else is in the session and who's typing right now,
// as a name/typing summary above the editor — complements the in-editor peer
// carets (CaretWidget/peersField above) rather than duplicating them. Renders
// nothing when you're alone.
export function CoeditPresenceBar({
  participants,
  typing,
  selfUserId,
}: CoeditPresenceBarProps) {
  const others = participants.filter((p) => p.user_id !== selfUserId);
  if (others.length === 0) return null;
  const typingSet = new Set(typing);
  return (
    <div className="flex shrink-0 flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-(--text-03)">
      <span
        className="inline-block h-[7px] w-[7px] rounded-full bg-(--status-success-05)"
        aria-hidden
      />
      {others.map((p) => (
        <span key={p.user_id} className="inline-flex items-center gap-1">
          <span className="font-medium text-(--text-04)">{p.user_display}</span>
          {typingSet.has(p.user_id) && (
            <span className="text-(--text-03) italic">typing…</span>
          )}
        </span>
      ))}
      <span>also editing</span>
    </div>
  );
}
