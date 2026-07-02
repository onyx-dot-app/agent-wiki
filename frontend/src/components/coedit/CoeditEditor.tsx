"use client";

/** CodeMirror 6 editor bound to a co-edit session buffer.
 *
 * A controlled editor (`value` + `onChange`) that also renders remote peers'
 * carets and selection highlights and reports the local caret so peers see
 * ours. Offsets are UTF-16 code units end-to-end (JS-native, matching the
 * server + `coedit.ts`).
 *
 * Peer carets are placed from each peer's latest `cursor` frame (clamped to the
 * doc); between frames a local edit can leave a caret briefly stale — the next
 * frame (~80ms) corrects it. Full position mapping arrives with pending-op
 * rebase.
 */
import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import { markdown } from "@codemirror/lang-markdown";
import {
  EditorState,
  StateEffect,
  StateField,
  Transaction,
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

import { type CoeditPeer, diffToChange } from "@/lib/coedit";

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
  ".cm-content": { padding: "1rem" },
  ".cm-line": { padding: "0" },
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

export function CoeditEditor({
  value,
  onChange,
  onSelectionChange,
  peers,
  placeholder,
}: {
  value: string;
  onChange: (value: string) => void;
  onSelectionChange: (anchor: number, head: number, isEdit: boolean) => void;
  peers: CoeditPeer[];
  placeholder?: string;
}) {
  const host = useRef<HTMLDivElement | null>(null);
  const view = useRef<EditorView | null>(null);
  // Latest callbacks without re-creating the editor.
  const onChangeRef = useRef(onChange);
  const onSelRef = useRef(onSelectionChange);
  onChangeRef.current = onChange;
  onSelRef.current = onSelectionChange;

  // Create the editor once.
  useEffect(() => {
    if (!host.current) return;
    const updateListener = EditorView.updateListener.of((u) => {
      // Skip transactions we dispatched to apply a remote op/resync — they
      // aren't user input, so reporting them would echo the change back and
      // falsely flag us as "typing" to peers.
      if (u.transactions.some((t) => t.annotation(Transaction.remote))) return;
      const sel = u.state.selection.main;
      if (u.docChanged) {
        onChangeRef.current(u.state.doc.toString());
        onSelRef.current(sel.anchor, sel.head, true);
      } else if (u.selectionSet) {
        onSelRef.current(sel.anchor, sel.head, false);
      }
    });
    const state = EditorState.create({
      doc: value,
      extensions: [
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
    const v = new EditorView({ state, parent: host.current });
    view.current = v;
    return () => {
      v.destroy();
      view.current = null;
    };
    // Editor is created once; `value`/`placeholder` sync via the effects below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Sync external buffer → editor (remote ops, resync, discard). Apply the
  // minimal diff (not a whole-doc replace) so CodeMirror maps the local caret
  // through it instead of jumping it to the end on every remote keystroke.
  // A no-op diff (our own echoed edit) leaves the editor untouched.
  useEffect(() => {
    const v = view.current;
    if (!v) return;
    const current = v.state.doc.toString();
    if (current === value) return;
    const change = diffToChange(current, value);
    if (change) {
      v.dispatch({
        changes: {
          from: change.from,
          to: change.to,
          insert: change.insert,
        },
        // Marks this as not-user-input so the updateListener ignores it.
        annotations: Transaction.remote.of(true),
      });
    }
  }, [value]);

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
