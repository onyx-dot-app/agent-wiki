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

/** Opal-ish hues that read on both themes, one per peer slot — ported
 * verbatim from `lib/editor/constants.ts`. */
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

/** Deterministically map a `userId` to a color from `PEER_COLORS` so a given
 * peer keeps the same color for the full session — ported verbatim from
 * `lib/editor/utils.ts`. */
export function colorFor(userId: string): string {
  let h = 0;
  for (let i = 0; i < userId.length; i++)
    h = (h * 31 + userId.charCodeAt(i)) | 0;
  return PEER_COLORS[Math.abs(h) % PEER_COLORS.length]!;
}

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
