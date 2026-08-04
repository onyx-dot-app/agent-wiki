/** Peer presence/carets — Yjs-native reimplementation of `lib/editor/
 * components.tsx`'s hand-built `CaretWidget`/`peersField`/
 * `buildPeerDecorations`. Unlike the highlighting plugins (`highlights.ts`,
 * plain `{id, from, to}` state we own), peer cursors ride on Yjs's Awareness
 * protocol (ephemeral, separate from document content) and the actual
 * decoration-building/remapping is `@tiptap/y-tiptap`'s own `yCursorPlugin`
 * — already installed as a transitive dependency of
 * `@tiptap/extension-collaboration`, promoted to a direct one here since we
 * import from it and `y-protocols` (its `Awareness` type) directly. */
import { Extension } from "@tiptap/core";
import { yCursorPlugin } from "@tiptap/y-tiptap";
import type { Awareness } from "y-protocols/awareness";

// Re-exported so existing importers keep working; the definition lives in one
// place because the caret and the presence chip must agree (see identityColor).
export { colorFor } from "@/lib/editor/identityColor";

/** Registers `yCursorPlugin` — renders every other client's caret/selection
 * from `awareness`'s shared state as a `Decoration.widget` (colored bar +
 * name label) / `Decoration.inline` (selection highlight) pair, keyed by
 * client id. Styling for the widget it renders (`.ProseMirror-yjs-cursor`)
 * lives in `components.tsx`'s `PROSE_CLASSES`, same as every other
 * ProseMirror-rendered construct in this module. */
export function presenceExtension(awareness: Awareness) {
  return Extension.create({
    name: "presence",
    addProseMirrorPlugins() {
      return [yCursorPlugin(awareness)];
    },
  });
}
