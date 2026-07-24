/** `CommentDraft` — the shape a pending, not-yet-submitted inline comment
 * takes (quoted span + text). Consumed by `CommentsPanel`/`CommentMarginRail`
 * for the reply/compose UI.
 *
 * Creating a *new* draft from an editor text selection
 * (`selectionToDraft`'s old job) is deferred: it needs the reverse
 * direction of the comment/source anchor resolution in `highlights.ts` —
 * translating a live PM position back into a flat markdown offset — which
 * doesn't exist yet. See `components.tsx`'s module docstring.
 */

export interface CommentDraft {
  startOffset: number;
  endOffset: number;
  quotedText: string;
}
