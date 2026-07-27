import type { AnyExtension } from "@tiptap/core";
import { Collaboration } from "@tiptap/extension-collaboration";
import { TaskItem } from "@tiptap/extension-task-item";
import { TaskList } from "@tiptap/extension-task-list";
import { Placeholder } from "@tiptap/extensions";
import { StarterKit } from "@tiptap/starter-kit";
import type { Awareness } from "y-protocols/awareness";
import type * as Y from "yjs";
import {
  BlockIdentity,
  HtmlBlock,
  OtherBlock,
  Table,
  TableRow,
  TableSeparator,
  ThematicBreak,
} from "@/lib/tiptapEditor/blocks";
import { AnchoredHighlights } from "@/lib/tiptapEditor/highlights";
import { presenceExtension } from "@/lib/tiptapEditor/presence";

/**
 * The Tiptap extension set for the live wiki editor. Images stay deferred
 * (unrelated, separate gap) — everything else the backend's markdown<->Yjs
 * codec can produce (`app/wiki/markdown_yjs.py`) has a matching node here;
 * see `blocks.ts` for why that's a correctness requirement, not scaffold
 * completeness.
 */
export function tiptapExtensions(
  doc: Y.Doc,
  awareness: Awareness,
  placeholder?: string,
): AnyExtension[] {
  return [
    StarterKit.configure({
      // Collaboration owns undo/redo over the Yjs doc; StarterKit's own
      // history extension binds the same Cmd+Z/Cmd+Shift+Z keys and would
      // conflict with it, not coexist alongside it.
      undoRedo: false,
      link: { openOnClick: false },
      // Replaced by ThematicBreak below — same content, but named to match
      // the backend's literal "thematic_break" Yjs XML tag (see blocks.ts).
      horizontalRule: false,
    }),
    TaskList,
    TaskItem.configure({ nested: true }),
    BlockIdentity,
    ThematicBreak,
    HtmlBlock,
    OtherBlock,
    Table,
    TableRow,
    TableSeparator,
    Collaboration.configure({
      document: doc,
      // Must match the backend's ROOT_XML_KEY (app/wiki/markdown_yjs.py) —
      // Collaboration defaults to a field named "default", not this one, so
      // omitting this silently binds to an empty fragment: real content
      // sits at "prosemirror", never surfacing in the editor. Only visible
      // against a real backend-seeded Y.Doc — the dev harness's own
      // locally-constructed docs never exercised the mismatch.
      field: "prosemirror",
    }),
    Placeholder.configure({ placeholder: placeholder ?? "" }),
    AnchoredHighlights,
    presenceExtension(awareness),
  ];
}
