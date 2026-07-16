/** Adapters between CodeMirror internals and the co-editing wire protocol. */
import { type ChangeSet, type EditorState } from "@codemirror/state";
import { sendableUpdates } from "@codemirror/collab";
import type { CoeditChange } from "./types";

/** Convert a CodeMirror `ChangeSet` to `CoeditChange[]` (old-doc coords).
 * One entry per changed span — exactly what `iterChanges` yields. */
export function changeSetToChanges(cs: ChangeSet): CoeditChange[] {
  const out: CoeditChange[] = [];
  cs.iterChanges((fromA, toA, _fromB, _toB, inserted) => {
    out.push({ from: fromA, to: toA, insert: inserted.toString() });
  });
  return out;
}

/** Length of the confirmed (synced) doc = current doc minus the net length of
 * un-acked local edits. Inbound op `ChangeSet`s must be anchored against this. */
export function syncedDocLength(state: EditorState): number {
  let len = state.doc.length;
  for (const u of sendableUpdates(state)) {
    len -= u.changes.newLength - u.changes.length;
  }
  return len;
}
