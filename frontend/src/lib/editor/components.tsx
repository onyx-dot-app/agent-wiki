"use client";

/**
 * The raw-ProseMirror co-edit editor. Replaces the CodeMirror 6
 * implementation this file used to hold (WS/OT transport) and is *not*
 * built on Tiptap — see the project's own scoping discussion for why
 * (Tiptap's open-core/paid-tier model). `y-prosemirror`'s sync/cursor/undo
 * plugins are wired directly; the schema/input rules/keymap this mounts
 * come from `schema.ts`/`inputRules.ts`/`keymap.ts`.
 *
 * Deferred, not a regression (see the project's scoping discussion and
 * `docs/AGENT_WIKI_MARKDOWN_STANDARD.md`'s Phase B items): BubbleToolbar,
 * SlashMenu, drag-handle, images/footnotes/emoji-shortcode node types, real
 * per-cell table editing, and creating a *new* comment by selecting text
 * (`onSelectionForComment` — the reverse direction of the anchor
 * resolution below, needs its own PM-position -> flat-markdown-offset
 * resolution). Viewing/highlighting/navigating to existing comments and
 * sources is fully wired.
 */

import type { Node as PMNode } from "prosemirror-model";
import { EditorState } from "prosemirror-state";
import { Plugin, PluginKey } from "prosemirror-state";
import { EditorView, type NodeView } from "prosemirror-view";
import {
  initProseMirrorDoc,
  redoCommand,
  undoCommand,
  ySyncPlugin,
  yCursorPlugin,
  yUndoPlugin,
} from "y-prosemirror";
import { keymap as pmKeymap } from "prosemirror-keymap";
import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type ForwardedRef,
} from "react";
import type { CoeditProvider } from "@/lib/editor/provider";
import { agentWikiSchema as schema, coerceChecked } from "@/lib/editor/schema";
import { agentWikiInputRules } from "@/lib/editor/inputRules";
import { agentWikiKeymap } from "@/lib/editor/keymap";
import {
  anchoredHighlightPlugin,
  caretHitIds,
  setHighlightActive,
  setHighlightTargets,
  type AnchoredHighlightTarget,
  type HighlightState,
} from "@/lib/editor/highlights";
import { colorFor } from "@/lib/editor/utils";

const commentHighlightKey = new PluginKey<HighlightState>("commentHighlights");
const sourceHighlightKey = new PluginKey<HighlightState>("sourceHighlights");

function taskItemNodeView(
  node: PMNode,
  view: EditorView,
  getPos: () => number | undefined,
): NodeView {
  const li = document.createElement("li");
  li.setAttribute("data-type", "taskItem");
  const label = document.createElement("label");
  label.contentEditable = "false";
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  const content = document.createElement("div");

  checkbox.addEventListener("mousedown", (e) => e.preventDefault());
  checkbox.addEventListener("change", (e) => {
    const checked = (e.target as HTMLInputElement).checked;
    const pos = getPos();
    if (!view.editable || typeof pos !== "number") return;
    const tr = view.state.tr.setNodeMarkup(pos, undefined, {
      ...view.state.doc.nodeAt(pos)?.attrs,
      checked: checked ? "true" : "false",
    });
    view.dispatch(tr);
  });

  label.appendChild(checkbox);
  li.append(label, content);

  function render(n: PMNode) {
    const checked = coerceChecked(n.attrs.checked);
    li.dataset.checked = String(checked);
    checkbox.checked = checked;
  }
  render(node);

  return {
    dom: li,
    contentDOM: content,
    update(updatedNode) {
      if (updatedNode.type !== node.type) return false;
      render(updatedNode);
      return true;
    },
  };
}

export interface CoeditorHandle {
  scrollToOffset: (offset: number) => void;
  /** Scroll to a comment thread's live-mapped span (resolves via the
   * comment highlight plugin's already-resolved position — same mechanism
   * as `scrollToSource`, not a raw offset, since a comment anchor is
   * block-relative, not a PM position). */
  scrollToComment: (id: string) => void;
  scrollToSource: (id: string) => void;
  sourceTargets: () => { id: string; offset: number }[];
  anchorLine: (offset: number) => { top: number; height: number } | null;
  scrollTop: () => number;
  scrollBy: (dy: number) => void;
  scrollHeight: () => number;
  clientHeight: () => number;
  scrollerTop: () => number;
  subscribeLayout: (cb: (kind: "scroll" | "geometry") => void) => () => void;
  /** Replace the whole document (template pick / "start blank"). Inserts
   * `text` as plain paragraphs (split on blank lines) — NOT a Markdown
   * parse (that needs a client-side mirror of the backend's
   * markdown_yjs.py codec, deferred — see module docstring), so a
   * template's headings/lists/marks show as literal `#`/`-`/`**`
   * characters rather than rendering richly. Content is preserved
   * losslessly as text, just not reformatted. */
  setDoc: (text: string) => void;
}

export interface CoeditorProps {
  conn: CoeditProvider | null;
  userId: string;
  userDisplay: string;
  readOnly?: boolean;
  placeholder?: string;
  onEmptyChange?: (empty: boolean) => void;
  /** Fires on a real, user-driven doc change — not on the programmatic
   * transaction `setDoc` itself dispatches (tagged and filtered out, see
   * `setDoc`'s implementation). Lets a caller track "has the user edited
   * since I last called setDoc" without needing the whole-buffer-text
   * comparison the old CodeMirror editor's equivalent check used (there's
   * no cheap "current text" accessor here — the doc's a tree, not a
   * string). */
  onDocChanged?: () => void;
  commentHighlights?: AnchoredHighlightTarget[];
  activeCommentIds?: string[];
  onCommentCaret?: (ids: string[]) => void;
  sourceHighlights?: AnchoredHighlightTarget[];
  activeSourceIds?: string[];
  onSourceCaret?: (ids: string[]) => void;
}

export const Coeditor = forwardRef<CoeditorHandle, CoeditorProps>(
  function Coeditor(props, ref) {
    if (!props.conn) return null;
    return <CoeditorInner {...props} conn={props.conn} forwardedRef={ref} />;
  },
);

interface CoeditorInnerProps extends Omit<CoeditorProps, "conn"> {
  conn: CoeditProvider;
  forwardedRef: ForwardedRef<CoeditorHandle>;
}

function CoeditorInner({
  conn,
  userId,
  userDisplay,
  readOnly,
  placeholder,
  onEmptyChange,
  onDocChanged,
  commentHighlights,
  activeCommentIds,
  onCommentCaret,
  sourceHighlights,
  activeSourceIds,
  onSourceCaret,
  forwardedRef,
}: CoeditorInnerProps) {
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const editorHostRef = useRef<HTMLDivElement | null>(null);
  const viewRef = useRef<EditorView | null>(null);
  const layoutSubs = useRef<Set<(kind: "scroll" | "geometry") => void>>(
    new Set(),
  );
  const lastCaretIds = useRef<{ comment: string; source: string }>({
    comment: "",
    source: "",
  });
  const [, forceRender] = useState(0);

  useEffect(() => {
    conn.provider.awareness.setLocalStateField("user", {
      name: userDisplay,
      color: colorFor(userId),
    });
  }, [conn, userId, userDisplay]);

  useEffect(() => {
    if (!editorHostRef.current) return;
    const yType = conn.ydoc.getXmlFragment("prosemirror");
    const { doc, mapping } = initProseMirrorDoc(yType, schema);

    const state = EditorState.create({
      doc,
      schema,
      plugins: [
        ySyncPlugin(yType, { mapping }),
        yCursorPlugin(conn.provider.awareness),
        yUndoPlugin(),
        pmKeymap({
          "Mod-z": undoCommand,
          "Mod-y": redoCommand,
          "Mod-Shift-z": redoCommand,
        }),
        agentWikiKeymap(),
        agentWikiInputRules(),
        anchoredHighlightPlugin(commentHighlightKey, {
          idle: "agent-wiki-comment-highlight",
          active: "agent-wiki-comment-highlight-active",
        }),
        anchoredHighlightPlugin(sourceHighlightKey, {
          idle: "agent-wiki-source-highlight",
          active: "agent-wiki-source-highlight-active",
        }),
        new Plugin({
          props: {
            attributes: {
              // `markdown` (globals.css) is what actually styles h1-h4/
              // lists/code/tables — Tailwind's preflight strips element
              // defaults, so without it every node type renders as flat
              // unstyled text (the same class the read-only ReactMarkdown
              // view uses for its output).
              class:
                "markdown mx-auto max-w-[768px] px-(--cm-gutter,1.5rem) py-6 min-h-full",
            },
          },
        }),
      ],
    });

    const view = new EditorView(editorHostRef.current, {
      state,
      editable: () => !readOnly,
      nodeViews: { taskItem: taskItemNodeView },
      dispatchTransaction(tr) {
        const newState = view.state.apply(tr);
        view.updateState(newState);
        onEmptyChange?.(
          newState.doc.textContent.trim() === "" &&
            newState.doc.childCount <= 1,
        );
        if (tr.docChanged && !tr.getMeta("agentWikiProgrammatic"))
          onDocChanged?.();

        const { from, to } = newState.selection;
        const commentIds = caretHitIds(commentHighlightKey, newState, from, to);
        const sourceIds = caretHitIds(sourceHighlightKey, newState, from, to);
        const commentKey = commentIds.join(",");
        const sourceKey = sourceIds.join(",");
        if (commentKey !== lastCaretIds.current.comment) {
          lastCaretIds.current.comment = commentKey;
          onCommentCaret?.(commentIds);
        }
        if (sourceKey !== lastCaretIds.current.source) {
          lastCaretIds.current.source = sourceKey;
          onSourceCaret?.(sourceIds);
        }
        if (tr.docChanged) {
          for (const cb of layoutSubs.current) cb("geometry");
        }
      },
    });
    viewRef.current = view;
    forceRender((n) => n + 1);

    const scroller = scrollerRef.current;
    const onScroll = () => {
      for (const cb of layoutSubs.current) cb("scroll");
    };
    scroller?.addEventListener("scroll", onScroll, { passive: true });
    const resizeObserver = new ResizeObserver(() => {
      for (const cb of layoutSubs.current) cb("geometry");
    });
    if (editorHostRef.current) resizeObserver.observe(editorHostRef.current);

    return () => {
      scroller?.removeEventListener("scroll", onScroll);
      resizeObserver.disconnect();
      view.destroy();
      viewRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conn]);

  useEffect(() => {
    viewRef.current?.setProps({ editable: () => !readOnly });
  }, [readOnly]);

  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    setHighlightTargets(
      commentHighlightKey,
      view.dispatch.bind(view),
      view.state,
      commentHighlights ?? [],
    );
  }, [commentHighlights, viewRef.current]);

  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    setHighlightActive(
      commentHighlightKey,
      view.dispatch.bind(view),
      view.state,
      activeCommentIds ?? [],
    );
  }, [activeCommentIds, viewRef.current]);

  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    setHighlightTargets(
      sourceHighlightKey,
      view.dispatch.bind(view),
      view.state,
      sourceHighlights ?? [],
    );
  }, [sourceHighlights, viewRef.current]);

  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    setHighlightActive(
      sourceHighlightKey,
      view.dispatch.bind(view),
      view.state,
      activeSourceIds ?? [],
    );
  }, [activeSourceIds, viewRef.current]);

  function scrollToPos(pos: number) {
    const view = viewRef.current;
    const scroller = scrollerRef.current;
    if (!view || !scroller) return;
    const clamped = Math.max(0, Math.min(pos, view.state.doc.content.size));
    let coords;
    try {
      coords = view.coordsAtPos(clamped);
    } catch {
      return;
    }
    const rect = scroller.getBoundingClientRect();
    const target =
      coords.top - rect.top + scroller.scrollTop - scroller.clientHeight / 3;
    scroller.scrollTo({ top: Math.max(0, target), behavior: "smooth" });
  }

  useImperativeHandle(
    forwardedRef,
    () => ({
      scrollToOffset: (offset) => scrollToPos(offset),
      scrollToComment: (id) => {
        const view = viewRef.current;
        if (!view) return;
        const target = commentHighlightKey
          .getState(view.state)
          ?.resolved.find((r) => r.id === id);
        if (target !== undefined) scrollToPos(target.from);
      },
      scrollToSource: (id) => {
        const view = viewRef.current;
        if (!view) return;
        const target = sourceHighlightKey
          .getState(view.state)
          ?.resolved.find((r) => r.id === id);
        if (target !== undefined) scrollToPos(target.from);
      },
      sourceTargets: () => {
        const view = viewRef.current;
        if (!view) return [];
        const state = sourceHighlightKey.getState(view.state);
        return (state?.resolved ?? []).map((r) => ({
          id: r.id,
          offset: r.from,
        }));
      },
      anchorLine: (offset) => {
        const view = viewRef.current;
        const scroller = scrollerRef.current;
        if (!view || !scroller) return null;
        try {
          const pos = Math.max(
            0,
            Math.min(offset, view.state.doc.content.size),
          );
          const coords = view.coordsAtPos(pos);
          const rect = scroller.getBoundingClientRect();
          return {
            top: coords.top - rect.top + scroller.scrollTop,
            height: Math.max(1, coords.bottom - coords.top),
          };
        } catch {
          return null;
        }
      },
      scrollTop: () => scrollerRef.current?.scrollTop ?? 0,
      scrollBy: (dy) => scrollerRef.current?.scrollBy({ top: dy }),
      scrollHeight: () => scrollerRef.current?.scrollHeight ?? 0,
      clientHeight: () => scrollerRef.current?.clientHeight ?? 0,
      scrollerTop: () => scrollerRef.current?.getBoundingClientRect().top ?? 0,
      subscribeLayout: (cb) => {
        layoutSubs.current.add(cb);
        return () => layoutSubs.current.delete(cb);
      },
      setDoc: (text: string) => {
        const view = viewRef.current;
        if (!view) return;
        const paragraphs = text.split(/\n{2,}/).filter((p) => p.trim() !== "");
        const nodes = (paragraphs.length > 0 ? paragraphs : [""]).map((p) =>
          schema.nodes.paragraph.create(
            {},
            p.trim() ? schema.text(p.trim()) : undefined,
          ),
        );
        const { tr } = view.state;
        tr.replaceWith(0, view.state.doc.content.size, nodes);
        tr.setMeta("agentWikiProgrammatic", true);
        view.dispatch(tr);
      },
    }),
    [],
  );

  return (
    <div ref={scrollerRef} className="relative h-full w-full overflow-y-auto">
      {placeholder && viewRef.current?.state.doc.textContent === "" && (
        <div className="pointer-events-none absolute inset-x-0 top-0 mx-auto max-w-[768px] px-(--cm-gutter,1.5rem) py-6 text-(--text-04)">
          {placeholder}
        </div>
      )}
      <div ref={editorHostRef} className="h-full" />
    </div>
  );
}

export interface CoeditPresenceBarProps {
  provider: import("y-websocket").WebsocketProvider | null;
  selfUserId: string;
}

/** Minimal presence strip driven by Yjs Awareness states directly — no
 * separate participants/typing plumbing needed, matching how presence for
 * this transport works everywhere else in this module. */
export function CoeditPresenceBar({
  provider,
  selfUserId,
}: CoeditPresenceBarProps) {
  const [others, setOthers] = useState<
    { userId: string; display: string; color: string }[]
  >([]);

  useEffect(() => {
    if (!provider) return;
    const update = () => {
      const states = [...provider.awareness.getStates().entries()]
        .filter(([clientId]) => clientId !== provider.awareness.clientID)
        .map(([, state]) => state?.user)
        .filter((u): u is { name: string; color: string } => Boolean(u))
        .filter((u) => u.name !== selfUserId);
      setOthers(
        states.map((u) => ({
          userId: u.name,
          display: u.name,
          color: u.color,
        })),
      );
    };
    provider.awareness.on("change", update);
    update();
    return () => provider.awareness.off("change", update);
  }, [provider, selfUserId]);

  if (others.length === 0) return null;

  return (
    <div className="flex items-center gap-2 px-(--cm-gutter,1.5rem) py-1 text-xs text-(--text-04)">
      {others.map((p) => (
        <span key={p.userId} className="flex items-center gap-1">
          <span
            className="inline-block size-2 rounded-full"
            style={{ backgroundColor: p.color }}
          />
          {p.display}
        </span>
      ))}
    </div>
  );
}
