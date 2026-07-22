/** CodeMirror-native comment anchoring.
 *
 * CodeMirror's doc *is* the raw markdown string, so a comment's anchor is
 * just a `[from, to)` offset pair into it — no DOM-alignment bridge needed.
 */
import type { EditorState, Range } from "@codemirror/state";
import { StateEffect, StateField } from "@codemirror/state";
import { Decoration, type DecorationSet, EditorView } from "@codemirror/view";

export interface CommentDraft {
  startOffset: number;
  endOffset: number;
  quotedText: string;
}

/** Read the current (non-collapsed) selection as a comment draft, or null if
 * there's nothing selected to anchor a new comment to. */
export function selectionToDraft(state: EditorState): CommentDraft | null {
  const { from, to } = state.selection.main;
  if (from === to) return null;
  return {
    startOffset: from,
    endOffset: to,
    quotedText: state.sliceDoc(from, to),
  };
}

export interface CommentHighlightTarget {
  /** Thread root comment id, matched against the active-id effect. */
  id: string;
  startOffset: number;
  endOffset: number;
}

/** Dispatched to update the highlighted comment spans in `commentsField`. */
export const setCommentHighlightsEffect =
  StateEffect.define<CommentHighlightTarget[]>();

/** Dispatched when the selected/hovered threads change, giving those ids the
 * stronger (orange) highlight. Separate from the spans effect so hover and
 * selection flips never re-send offsets over a locally edited doc. */
export const setActiveCommentHighlightsEffect = StateEffect.define<string[]>();

function buildCommentDecorations(
  targets: CommentHighlightTarget[],
  activeIds: string[],
  docLen: number,
): DecorationSet {
  const ranges: Range<Decoration>[] = [];
  for (const t of targets) {
    const from = Math.max(0, Math.min(t.startOffset, docLen));
    const to = Math.max(from, Math.min(t.endOffset, docLen));
    if (from === to) continue;
    ranges.push(
      Decoration.mark({
        class: activeIds.includes(t.id)
          ? "cm-comment-highlight-active"
          : "cm-comment-highlight",
      }).range(from, to),
    );
  }
  return Decoration.set(ranges, true);
}

/** Holds the comment highlight targets, the active thread ids, and their
 * decorations. Doc changes map the held offsets through the edit so a
 * highlight stays on the text it was anchored to. Fresh server offsets and
 * active ids only arrive via their effects. */
export const commentsField = StateField.define<{
  targets: CommentHighlightTarget[];
  activeIds: string[];
  deco: DecorationSet;
}>({
  create: () => ({ targets: [], activeIds: [], deco: Decoration.none }),
  update(value, tr) {
    let targets = value.targets;
    let activeIds = value.activeIds;
    if (tr.docChanged) {
      // Boundary-typed text stays outside the range (start maps after an
      // insertion at the start, end maps before one at the end).
      targets = targets.map((t) => ({
        ...t,
        startOffset: tr.changes.mapPos(t.startOffset, 1),
        endOffset: tr.changes.mapPos(t.endOffset, -1),
      }));
    }
    for (const e of tr.effects) {
      if (e.is(setCommentHighlightsEffect)) targets = e.value;
      if (e.is(setActiveCommentHighlightsEffect)) activeIds = e.value;
    }
    if (targets === value.targets && activeIds === value.activeIds)
      return value;
    return {
      targets,
      activeIds,
      deco: buildCommentDecorations(targets, activeIds, tr.state.doc.length),
    };
  },
  provide: (f) => EditorView.decorations.from(f, (v) => v.deco),
});
