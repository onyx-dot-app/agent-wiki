/**
 * Keybindings lost by not using Tiptap's StarterKit, rebuilt by hand:
 * bold/italic/code toggles, and list Enter/Backspace/Tab/Shift-Tab
 * (split/lift/sink), chained before `baseKeymap`'s defaults so they only
 * take over inside a list item and fall through to normal behavior
 * everywhere else (every `prosemirror-schema-list` command returns
 * `false`/no-ops when its precondition doesn't hold, which is what makes
 * `chainCommands` a safe "try this first" rather than a hard override).
 *
 * Undo/redo are deliberately absent here — `y-prosemirror`'s own
 * `yUndoPlugin`/`undo`/`redo` own that keymap slot (CRDT-aware undo, not
 * `prosemirror-history`'s), wired in `components.tsx` alongside the Yjs
 * sync plugins, not here.
 */

import { baseKeymap, chainCommands, toggleMark } from "prosemirror-commands";
import type { NodeType } from "prosemirror-model";
import { keymap } from "prosemirror-keymap";
import {
  liftListItem,
  sinkListItem,
  splitListItem,
} from "prosemirror-schema-list";
import {
  TextSelection,
  type Command,
  type EditorState,
  type Plugin,
  type Transaction,
} from "prosemirror-state";
import { agentWikiSchema as schema } from "./schema";

const splitListItemCmd = chainCommands(
  splitListItem(schema.nodes.taskItem),
  splitListItem(schema.nodes.listItem),
);

/** `liftListItem` lifts the item containing the selection regardless of
 * where the cursor sits inside it — bound directly to plain Backspace, that
 * outdents on every keystroke anywhere in the item's text, not just at its
 * start (the reference `prosemirror-example-setup` keymap only ever binds
 * it to a dedicated outdent shortcut, never Backspace, for this reason).
 * Restricting to "cursor at offset 0 of the item's own first child" makes
 * repeated Backspace-at-start behave like Notion/Docs: each press lifts one
 * level (`liftListItem` itself already peels only one level of nesting per
 * call), and once the item is no longer inside a list this falls through to
 * `baseKeymap.Backspace`'s normal join-with-previous-block. */
function liftListItemAtStart(itemType: NodeType): Command {
  const lift = liftListItem(itemType);
  return (state: EditorState, dispatch) => {
    const { $from, empty } = state.selection;
    if (!empty || $from.parentOffset !== 0) return false;
    if ($from.index($from.depth - 1) !== 0) return false;
    return lift(state, dispatch);
  };
}

const liftListItemCmd = chainCommands(
  liftListItemAtStart(schema.nodes.taskItem),
  liftListItemAtStart(schema.nodes.listItem),
);
const sinkListItemCmd = chainCommands(
  sinkListItem(schema.nodes.taskItem),
  sinkListItem(schema.nodes.listItem),
);

const LIST_TYPES = [
  schema.nodes.bulletList,
  schema.nodes.orderedList,
  schema.nodes.taskList,
];

/** Backspace at the start of a plain paragraph that directly follows a list
 * (the state `liftListItemAtStart` leaves behind once an item's been fully
 * outdented) — merge its text into the end of the list's last item instead
 * of falling through to `baseKeymap.Backspace`'s default `joinBackward`.
 * That default doesn't know how to join a textblock into a list: rather
 * than appending to the last item's text, it re-wraps the paragraph as a
 * brand-new list item, which from the outside looks like the list item you
 * just outdented reappearing on the very next keystroke. Only handles the
 * common flat case (the last item's own single paragraph, no nested
 * sub-list) — anything else falls through to default behavior rather than
 * guessing where "the end of the previous line" is inside nested content. */
function joinParagraphIntoPrecedingList(
  state: EditorState,
  dispatch?: (tr: Transaction) => void,
): boolean {
  const { $from, empty } = state.selection;
  if (!empty || $from.parentOffset !== 0) return false;
  if ($from.parent.type !== schema.nodes.paragraph) return false;

  const before = $from.before();
  if (before === 0) return false;
  const prevNode = state.doc.resolve(before).nodeBefore;
  if (!prevNode || !LIST_TYPES.includes(prevNode.type)) return false;

  const lastItem = prevNode.lastChild;
  if (!lastItem || lastItem.childCount > 1) return false; // has nested content — don't guess
  const itemPara = lastItem.firstChild;
  if (!itemPara || itemPara.type !== schema.nodes.paragraph) return false;

  if (dispatch) {
    const para = $from.parent;
    // `before` sits right after the list's closing tag (list-item content
    // depth). The target — the end of the last item's own paragraph text —
    // is 3 positions further in: back over the list's close, the item's
    // close, and the paragraph's close (verified against a concrete
    // position trace, not guessed — inserting at `before - 1` instead
    // lands at the *list's* content depth, where only a listItem is valid,
    // so PM's content-fitting auto-wraps the inserted text into a brand
    // new list item — the exact "bullet reappears" bug this replaces).
    const insertPos = before - 3;
    const tr = state.tr;
    tr.delete(before, before + para.nodeSize);
    tr.insert(insertPos, para.content);
    tr.setSelection(TextSelection.create(tr.doc, insertPos));
    dispatch(tr);
  }
  return true;
}

export function agentWikiKeymap(): Plugin {
  return keymap({
    ...baseKeymap,
    "Mod-b": toggleMark(schema.marks.bold),
    "Mod-i": toggleMark(schema.marks.italic),
    "Mod-e": toggleMark(schema.marks.code),
    Enter: chainCommands(splitListItemCmd, baseKeymap.Enter),
    Backspace: chainCommands(
      liftListItemCmd,
      joinParagraphIntoPrecedingList,
      baseKeymap.Backspace,
    ),
    "Shift-Tab": liftListItemCmd,
    Tab: sinkListItemCmd,
  });
}
