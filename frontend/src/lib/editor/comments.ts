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
  startOffset: number;
  endOffset: number;
  /** The selected/active thread gets the stronger (orange) highlight. */
  active: boolean;
}

/** Dispatched to update the highlighted comment spans in `commentsField`. */
export const setCommentHighlightsEffect =
  StateEffect.define<CommentHighlightTarget[]>();

function buildCommentDecorations(
  targets: CommentHighlightTarget[],
  docLen: number,
): DecorationSet {
  const ranges: Range<Decoration>[] = [];
  for (const t of targets) {
    const from = Math.max(0, Math.min(t.startOffset, docLen));
    const to = Math.max(from, Math.min(t.endOffset, docLen));
    if (from === to) continue;
    ranges.push(
      Decoration.mark({
        class: t.active
          ? "cm-comment-highlight-active"
          : "cm-comment-highlight",
      }).range(from, to),
    );
  }
  return Decoration.set(ranges, true);
}

/** Holds the current comment highlight targets + their decorations. Rebuilds
 * when the target list changes or the doc changes (keeps offsets in range as
 * text is edited); provides decorations to the view via `EditorView.decorations`. */
export const commentsField = StateField.define<{
  targets: CommentHighlightTarget[];
  deco: DecorationSet;
}>({
  create: () => ({ targets: [], deco: Decoration.none }),
  update(value, tr) {
    let targets = value.targets;
    for (const e of tr.effects)
      if (e.is(setCommentHighlightsEffect)) targets = e.value;
    if (targets === value.targets && !tr.docChanged) return value;
    return {
      targets,
      deco: buildCommentDecorations(targets, tr.state.doc.length),
    };
  },
  provide: (f) => EditorView.decorations.from(f, (v) => v.deco),
});
