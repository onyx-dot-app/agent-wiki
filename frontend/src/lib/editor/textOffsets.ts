/** Approximate mapping between a ProseMirror document position and a
 * "plain text" character offset (concatenated text content, block
 * boundaries joined by `"\n\n"`, marks/block syntax stripped).
 *
 * KNOWN LIMITATION, not a rounding nit: this is *not* the same offset
 * space as the backend's markdown-source character offsets (what
 * comment/source spans are anchored to — see `app/wiki/comments.py`,
 * `app/wiki/provenance.py`). A heading's plain-text content is `"Heading"`,
 * not `"# Heading"`; a bold run's is `"text"`, not `"**text**"`. This
 * mapper under-counts by exactly the markdown syntax overhead of
 * everything before the target position — exact for plain, unformatted
 * paragraph text (the common case), increasingly approximate for
 * headings/lists/blockquotes/inline marks/tables. A precise mapper would
 * need to mirror `app/wiki/markdown_yjs.py`'s serialization rules
 * client-side (or have the backend report PM-compatible positions
 * directly) — flagged as follow-up work, not solved here. This backs
 * comment anchoring and peer-cursor/deep-link scroll-to; a
 * wrong-by-a-few-characters *display* position is the accepted cost of
 * shipping the live editor now rather than blocking on this.
 *
 * The span persisted when a comment is created is corrected server-side
 * against the real markdown source (`app/wiki/comments.py:create_thread`, via
 * `comment_anchor.resolve_exact_span`), which locates the selection by its
 * quoted text — so this mapper only has to get the creation-time request into
 * the right neighborhood.
 *
 * That correction has a gap worth knowing about, because it is the one case
 * where a *wrong span gets stored*: the quote we send is plain text, so a
 * selection containing inline formatting ("hello world" over a source
 * `hello **world**`) matches nothing in the body, and `resolve_exact_span`
 * falls back to this mapper's own estimate. Selections without formatting
 * inside them are corrected exactly; selections across a bold/italic/code run
 * are not.
 */
import type { Editor } from "@tiptap/core";
import type { Node as PMNode } from "@tiptap/pm/model";

/** Text a leaf contributes, so it occupies offsets rather than collapsing to
 * nothing. The src must stay in step with `comment_anchor._project_media`,
 * which reduces the source the same way. */
function leafText(leaf: PMNode): string {
  if (leaf.type.name !== "image") return "";
  return (leaf.attrs.src as string | null) ?? "";
}

/** The plain-text projection offsets and comment quotes are counted in. */
export function docTextBetween(
  editor: Editor,
  from: number,
  to: number,
): string {
  return editor.state.doc.textBetween(from, to, "\n\n", leafText);
}

export function pmPosToTextOffset(editor: Editor, pos: number): number {
  const clamped = Math.max(0, Math.min(pos, editor.state.doc.content.size));
  return docTextBetween(editor, 0, clamped).length;
}

/** Inverse of `pmPosToTextOffset`, via binary search rather than a
 * hand-rolled tree walk — guarantees exact consistency with the forward
 * direction (`textBetween`'s own separator-insertion rules don't need
 * reimplementing) at the cost of O(log n) traversals instead of one;
 * negligible for a wiki-page-sized document on an interactive path. */
export function textOffsetToPmPos(editor: Editor, offset: number): number {
  const docSize = editor.state.doc.content.size;
  if (offset <= 0) return 0;
  let lo = 0;
  let hi = docSize;
  while (lo < hi) {
    const mid = Math.floor((lo + hi) / 2);
    if (pmPosToTextOffset(editor, mid) < offset) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}
