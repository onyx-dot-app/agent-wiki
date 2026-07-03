"use client";

/** CodeMirror 6 editor that owns the co-edit document via `@codemirror/collab`.
 *
 * The editor is the source of truth for the doc + version; local edits push to
 * the server as ops and inbound ops/resync are fed to collab's `receiveUpdates`,
 * which rebases un-acked local edits through them — so your keystrokes never
 * revert on a concurrent remote edit. It also renders remote peers' carets and
 * selection highlights and reports the local caret. Offsets are UTF-16 code
 * units end-to-end (JS-native, matching the server + `coedit.ts`).
 *
 * Ops are pushed one-per-version (our `/coedit/op` is one op per version); a
 * push is confirmed locally on 200 and the SSE echo is skipped as already-seen.
 * On a 409 or a version gap we pull the missed ops from `/coedit/ops`.
 */
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
import { useEffect, useRef } from "react";

import { ApiError } from "@/lib/api";
import {
  type CoeditChange,
  type CoeditFrame,
  type CoeditPeer,
  getOps,
  sendOp,
} from "@/lib/coedit";
import type { CoeditSessionHandle } from "@/lib/useCoeditSession";

// Stable per-user color from a small palette (Opal-ish hues that read on both
// themes). Hash the id so a given peer keeps one color across the session.
const PEER_COLORS = [
  "#e5484d",
  "#0090ff",
  "#30a46c",
  "#f76b15",
  "#8e4ec6",
  "#e5b000",
  "#00a2c7",
  "#e93d82",
];
function colorFor(userId: string): string {
  let h = 0;
  for (let i = 0; i < userId.length; i++)
    h = (h * 31 + userId.charCodeAt(i)) | 0;
  return PEER_COLORS[Math.abs(h) % PEER_COLORS.length]!;
}

// A remote caret: a thin colored bar with a small name label above it.
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
  // Zero-width; never let the editor treat it as an edit boundary.
  ignoreEvent() {
    return true;
  }
}

const setPeersEffect = StateEffect.define<CoeditPeer[]>();

function buildPeerDecorations(
  peers: CoeditPeer[],
  docLen: number,
): DecorationSet {
  const ranges = [];
  for (const p of peers) {
    // Clamp to the current doc so a caret from a slightly-stale frame can't
    // land out of range (which CodeMirror would throw on).
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

// Holds the peer list + its decorations. Rebuilds when the peers change or the
// doc changes (so offsets stay in range as text is edited); provides the
// decorations to the view.
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

const baseTheme = EditorView.theme({
  "&": {
    height: "100%",
    fontSize: "0.875rem",
    borderRadius: "var(--border-radius-08)",
    border: "1px solid var(--border-01)",
    backgroundColor: "var(--background-00)",
    color: "var(--text-05)",
  },
  "&.cm-focused": { outline: "none" },
  ".cm-scroller": {
    overflow: "auto",
    fontFamily:
      "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
    lineHeight: "1.6",
  },
  ".cm-content": { padding: "1rem", caretColor: "var(--text-05)" },
  ".cm-line": { padding: "0" },
  // Own caret + drawn cursor follow the theme token; native selection uses a
  // theme tint for contrast.
  ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--text-05)" },
  "&.cm-focused .cm-selectionBackground, ::selection": {
    backgroundColor: "var(--background-tint-03)",
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

// The changes a collab ChangeSet makes, as our wire ops ({from,to,insert} in
// the *old* doc coords — exactly what iterChanges yields).
function changeSetToChanges(cs: ChangeSet): CoeditChange[] {
  const out: CoeditChange[] = [];
  cs.iterChanges((fromA, toA, _fromB, _toB, inserted) => {
    out.push({ from: fromA, to: toA, insert: inserted.toString() });
  });
  return out;
}

// Length of the confirmed (synced) doc = current doc minus the net length of
// un-acked local edits. Inbound op ChangeSets are built against this.
function syncedDocLength(state: EditorState): number {
  let len = state.doc.length;
  for (const u of sendableUpdates(state)) {
    len -= u.changes.newLength - u.changes.length;
  }
  return len;
}

export function CoeditEditor({
  session,
  peers,
  onSelectionChange,
  onServerFrame,
  reportDoc,
  registerFlush,
  registerSetDoc,
  placeholder,
}: {
  session: CoeditSessionHandle;
  peers: CoeditPeer[];
  onSelectionChange: (anchor: number, head: number, isEdit: boolean) => void;
  onServerFrame: (handler: ((frame: CoeditFrame) => void) | null) => void;
  reportDoc: (doc: string) => void;
  registerFlush: (fn: (() => Promise<void>) | null) => void;
  registerSetDoc: (fn: ((text: string) => void) | null) => void;
  placeholder?: string;
}) {
  const host = useRef<HTMLDivElement | null>(null);
  const view = useRef<EditorView | null>(null);
  // Latest callbacks without re-creating the editor.
  const onSelRef = useRef(onSelectionChange);
  const reportDocRef = useRef(reportDoc);
  onSelRef.current = onSelectionChange;
  reportDocRef.current = reportDoc;

  // Create the collab editor once per session.
  useEffect(() => {
    if (!host.current) return;
    let v: EditorView;
    const pushing = { current: false };
    const applyingRemote = { current: false };

    const dispatchRemote = (spec: Parameters<EditorView["dispatch"]>[0]) => {
      applyingRemote.current = true;
      try {
        v.dispatch(spec);
      } finally {
        applyingRemote.current = false;
      }
    };

    // Push un-acked local edits, one op per version (our /op is one-per-
    // version). Confirm on 200 (the SSE echo is then skipped as <= synced); a
    // 409 means we missed ops → pull + rebase, then retry.
    const doPush = async (): Promise<void> => {
      if (pushing.current) return;
      const updates = sendableUpdates(v.state);
      if (updates.length === 0) return;
      pushing.current = true;
      try {
        const version = getSyncedVersion(v.state);
        await sendOp(
          session.id,
          version,
          changeSetToChanges(updates[0]!.changes),
          session.clientId,
        );
        dispatchRemote(receiveUpdates(v.state, [updates[0]!]));
      } catch (e) {
        if (e instanceof ApiError && e.status === 409) await pull();
        // else transient — leave un-acked; a later edit/echo retries
      } finally {
        pushing.current = false;
      }
      if (sendableUpdates(v.state).length > 0) await doPush();
    };

    const pull = async (): Promise<void> => {
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
    };

    const applyFrame = (frame: CoeditFrame): void => {
      if (frame.type === "resync") {
        void pull();
        return;
      }
      if (frame.type !== "op") return;
      const synced = getSyncedVersion(v.state);
      if (frame.version <= synced) return; // already applied (incl. our echo)
      if (frame.version > synced + 1) {
        void pull(); // a gap (missed frame / big-op resync) → fetch the ops
        return;
      }
      const cs = ChangeSet.of(frame.changes, syncedDocLength(v.state));
      dispatchRemote(
        receiveUpdates(v.state, [
          { changes: cs, clientID: frame.client_id ?? "" },
        ]),
      );
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
        EditorView.lineWrapping,
        placeholderExt(placeholder ?? ""),
        peersField,
        baseTheme,
        updateListener,
      ],
    });
    v = new EditorView({ state, parent: host.current });
    view.current = v;
    onServerFrame(applyFrame);
    registerFlush(async () => {
      await doPush();
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

  return (
    <div
      ref={host}
      className="box-border min-h-0 w-full flex-1 overflow-hidden"
    />
  );
}
