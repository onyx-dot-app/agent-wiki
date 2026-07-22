/** Comment anchoring shape shared with `CommentsPanel`/`FileView`. The
 * durable anchor is always a markdown code-point offset into committed
 * HEAD (`app/wiki/comment_anchor.py`), independent of editor transport.
 *
 * Live in-editor highlight decorations and the "select text to comment"
 * draft-capture (both CM6-specific before the onyx-editor Tiptap migration,
 * plans/onyx-editor.md) are deferred — see
 * `frontend/src/lib/editor/components.tsx`'s module docstring. Only the
 * wire shape survives here for now.
 */

export interface CommentDraft {
  startOffset: number;
  endOffset: number;
  quotedText: string;
}
