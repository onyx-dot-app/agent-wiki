/** CodeMirror-native comment anchoring.
 *
 * CodeMirror's doc *is* the raw markdown string, so a comment's anchor is
 * just a `[from, to)` offset pair into it — no DOM-alignment bridge needed.
 * The highlight field itself is the comments instantiation of the shared
 * anchored-highlight machinery in `highlights.ts`.
 */
import type { EditorState } from "@codemirror/state";

import { commentHighlights } from "@/lib/editor/highlights";
import type { AnchoredHighlightTarget } from "@/lib/editor/highlights";

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

export type CommentHighlightTarget = AnchoredHighlightTarget;

export const commentsField = commentHighlights.field;
export const setCommentHighlightsEffect = commentHighlights.setTargets;
export const setActiveCommentHighlightsEffect = commentHighlights.setActive;
