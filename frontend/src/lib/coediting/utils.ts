import type { ChangeSet, EditorState } from "@codemirror/state";
import { sendableUpdates } from "@codemirror/collab";
import type { CoeditChange } from "@/lib/coediting/types";

/** Opal-ish hues that read on both themes, one per peer slot. */
export const PEER_COLORS = [
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
 * peer keeps the same color for the full session. */
export function colorFor(userId: string): string {
  let h = 0;
  for (let i = 0; i < userId.length; i++)
    h = (h * 31 + userId.charCodeAt(i)) | 0;
  return PEER_COLORS[Math.abs(h) % PEER_COLORS.length]!;
}

/** Diff `oldStr` → `newStr` into one range change (trim common prefix/suffix),
 * or null if unchanged. Offsets are UTF-16 code units (JS-native), matching the
 * server. Coarse (one span), which is all the server needs. */
export function diffToChange(
  oldStr: string,
  newStr: string,
): CoeditChange | null {
  if (oldStr === newStr) return null;
  const oldLen = oldStr.length;
  const newLen = newStr.length;
  const maxPre = Math.min(oldLen, newLen);
  let pre = 0;
  while (pre < maxPre && oldStr.charCodeAt(pre) === newStr.charCodeAt(pre))
    pre++;
  const maxSuf = Math.min(oldLen, newLen) - pre;
  let suf = 0;
  while (
    suf < maxSuf &&
    oldStr.charCodeAt(oldLen - 1 - suf) === newStr.charCodeAt(newLen - 1 - suf)
  ) {
    suf++;
  }
  return {
    from: pre,
    to: oldLen - suf,
    insert: newStr.slice(pre, newLen - suf),
  };
}

/** Apply a range change to a string (UTF-16 offsets). */
export function applyChange(str: string, c: CoeditChange): string {
  return str.slice(0, c.from) + c.insert + str.slice(c.to);
}

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
