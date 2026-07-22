"use client";

/**
 * The Tiptap-based co-edit editor (onyx-editor migration, Phase 2 —
 * plans/onyx-editor.md). Replaces the CodeMirror 6 implementation this file
 * used to hold; see git history for that version.
 *
 * Presence/cursors are no longer hand-rolled: Yjs's own Awareness protocol
 * (wired through `y-websocket`'s provider + Tiptap's `CollaborationCursor`
 * extension) replaces the old caret-epoch bookkeeping in `hooks.ts`
 * entirely — there's no `onSelectionChange`/`getCaretSeq`/cursor-frame
 * plumbing to port, Yjs already solves "which of two concurrent cursor
 * updates is newer" natively.
 *
 * The Notion-like affordances (`BubbleToolbar`, `SlashMenu`, `DragHandle`)
 * are a from-scratch open-source build against free Tiptap primitives —
 * Tiptap's own "Notion-like editor" template is a paid product, not
 * something to copy from; see those two modules' docstrings.
 *
 * Scope note (deferred, not a regression — see the migration plan's Phase 2
 * scope decision): comment-highlight decorations, the "select text to
 * comment" popover, and real per-cell table editing are not wired up in
 * this pass. Lists/blockquotes/code blocks/marks are real structural
 * Tiptap nodes, synced live.
 */

import Collaboration from "@tiptap/extension-collaboration";
import CollaborationCursor from "@tiptap/extension-collaboration-cursor";
import Placeholder from "@tiptap/extension-placeholder";
import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useMemo,
  useState,
  type ForwardedRef,
} from "react";
import { SvgHandle } from "@onyx-ai/opal/icons";
import { DragHandle } from "@tiptap/extension-drag-handle-react";
import { BubbleToolbar } from "@/lib/editor/BubbleToolbar";
import type { CoeditProvider } from "@/lib/editor/provider";
import { SlashCommand } from "@/lib/editor/SlashMenu";
import {
  Blockquote,
  Bold,
  BulletList,
  Code,
  CodeBlock,
  Heading,
  Italic,
  Link,
  ListItem,
  OrderedList,
  Paragraph,
} from "@/lib/editor/tiptapSchema";
import { colorFor } from "@/lib/editor/utils";

export interface CoeditorHandle {
  /** Best-effort: scrolls the editor to a position. Real position-mapped
   * scroll-to-comment (matching the old CM6 `scrollToOffset`'s offset
   * semantics) is deferred along with comment-highlight decorations — see
   * module docstring. */
  scrollToOffset: (offset: number) => void;
  /** Replace the whole document (template pick / "start blank"). Inserts
   * `text` as plain paragraphs (split on blank lines) — NOT a Markdown
   * parse, so a template's headings/lists/marks show as literal `#`/`-`/
   * `**` characters rather than rendering richly. A real Markdown ->
   * ProseMirror importer is deferred (this needs the mirror image of the
   * backend's markdown_yjs.py codec, on the client, and didn't fit this
   * pass) — content is preserved losslessly as text, just not reformatted.
   */
  setDoc: (text: string) => void;
}

export interface CoeditorProps {
  /** The live connection, owned by `useCoeditSession` — a parent (FileView)
   * creates it once and can also drive a `CoeditPresenceBar` from the same
   * instance. Null while connecting; `Coeditor` renders nothing until set. */
  conn: CoeditProvider | null;
  userId: string;
  userDisplay: string;
  readOnly?: boolean;
  placeholder?: string;
  /** Fires whenever the document's emptiness changes — for UI that only
   * makes sense on a blank page (e.g. the template gallery). */
  onEmptyChange?: (empty: boolean) => void;
}

export const Coeditor = forwardRef<CoeditorHandle, CoeditorProps>(
  function Coeditor(props, ref) {
    if (!props.conn) {
      return null;
    }
    // Keyed by the connection's own Y.Doc identity (via CoeditorInner's own
    // props, one per `conn` object) so a path change — a new `conn` from
    // useCoeditSession — tears down and rebuilds the whole Tiptap editor
    // instance, matching the old component's per-session remount behavior
    // (FileView.tsx also keys Coeditor by session id for the same reason).
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
  forwardedRef,
}: CoeditorInnerProps) {
  const extensions = useMemo(
    () => [
      StarterKit.configure({
        heading: false,
        paragraph: false,
        bulletList: false,
        orderedList: false,
        listItem: false,
        blockquote: false,
        codeBlock: false,
        link: false,
        bold: false,
        italic: false,
        code: false,
        // Tiptap's stock history plugin isn't Yjs-aware and conflicts with
        // collaborative editing — Collaboration below supplies its own
        // Yjs-native undo/redo.
        undoRedo: false,
      }),
      Heading,
      Paragraph,
      BulletList,
      OrderedList,
      ListItem,
      Blockquote,
      CodeBlock,
      Bold,
      Italic,
      Code,
      Link.configure({ openOnClick: false }),
      Placeholder.configure({ placeholder: placeholder ?? "" }),
      SlashCommand,
      Collaboration.configure({ document: conn.ydoc, field: "prosemirror" }),
      CollaborationCursor.configure({
        provider: conn.provider,
        user: { name: userDisplay, color: colorFor(userId) },
      }),
      // eslint-disable-next-line react-hooks/exhaustive-deps
    ],
    [conn, userId, userDisplay, placeholder],
  );

  const editor = useEditor({
    extensions,
    editable: !readOnly,
    immediatelyRender: false,
    editorProps: {
      // Styling for the real h1-h6/ul/ol/li/strong/em/code/pre/blockquote/a
      // elements this renders lives in globals.css, scoped under
      // `.ProseMirror` (Tailwind's preflight strips element defaults, same
      // reason `.markdown` exists there for react-markdown output).
      attributes: {
        class: "mx-auto max-w-[768px] px-(--cm-gutter,1.5rem) py-6 min-h-full",
      },
    },
  });

  useEffect(() => {
    editor?.setEditable(!readOnly);
  }, [editor, readOnly]);

  useEffect(() => {
    if (!editor) return;
    const report = () => onEmptyChange?.(editor.isEmpty);
    editor.on("update", report);
    report();
    return () => {
      editor.off("update", report);
    };
  }, [editor, onEmptyChange]);

  useImperativeHandle(
    forwardedRef,
    () => ({
      scrollToOffset: () => {
        // Deferred — see CoeditorHandle docstring.
      },
      setDoc: (text: string) => {
        if (!editor) return;
        const paragraphs = text.split(/\n{2,}/).filter((p) => p.trim() !== "");
        editor
          .chain()
          .clearContent()
          .insertContent(
            paragraphs.length > 0
              ? paragraphs.map((p) => ({
                  type: "paragraph",
                  content: [{ type: "text", text: p.trim() }],
                }))
              : [{ type: "paragraph" }],
          )
          .run();
      },
    }),
    [editor],
  );

  return (
    <div className="h-full w-full overflow-y-auto">
      {editor && (
        <>
          <BubbleToolbar editor={editor} />
          <DragHandle editor={editor}>
            <SvgHandle className="size-4 cursor-grab text-(--text-03) active:cursor-grabbing" />
          </DragHandle>
        </>
      )}
      <EditorContent editor={editor} className="h-full" />
    </div>
  );
}

export interface CoeditPresenceBarProps {
  provider: import("y-websocket").WebsocketProvider | null;
  selfUserId: string;
}

/** Minimal presence strip driven by Yjs Awareness states directly (no
 * separate participants/typing plumbing — see module docstring). */
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

  if (others.length === 0) {
    return null;
  }

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
