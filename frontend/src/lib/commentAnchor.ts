/** Map a rendered-view text selection back to raw-markdown character offsets.
 *
 * The wiki page renders raw markdown via react-markdown with `sourcePos`, which
 * stamps each block element with `data-sourcepos="L:C-L:C"` (1-based source
 * line:col). We use that to find the block's char range in the raw body, then
 * locate the selected text within the block's raw slice for a precise offset.
 *
 * v1 limits (return null — caller just doesn't offer "comment"):
 *  - selection spanning multiple blocks,
 *  - selection whose rendered text isn't a verbatim substring of the block's
 *    raw markdown (i.e. it crosses inline syntax like `code` or **bold**).
 * Plain-prose selections — the common case — map exactly. The backend's exact
 * offsets + drift take over after creation.
 */

export interface CommentDraft {
  startOffset: number;
  endOffset: number;
  quotedText: string;
}

/** Char offset of the start of each 1-based source line. */
function lineStartOffsets(body: string): number[] {
  const starts = [0];
  for (let i = 0; i < body.length; i++) {
    if (body[i] === "\n") starts.push(i + 1);
  }
  return starts;
}

function offsetOf(starts: number[], line: number, col: number): number {
  const base = starts[Math.min(line, starts.length) - 1] ?? 0;
  return base + (col - 1);
}

function countOccurrences(haystack: string, needle: string): number {
  if (!needle) return 0;
  let count = 0;
  let from = 0;
  for (;;) {
    const i = haystack.indexOf(needle, from);
    if (i < 0) return count;
    count++;
    from = i + 1;
  }
}

/** Index of the (0-based) `nth` occurrence of `needle` in `haystack`, or -1. */
function nthIndexOf(haystack: string, needle: string, nth: number): number {
  let from = 0;
  for (let k = 0; ; k++) {
    const i = haystack.indexOf(needle, from);
    if (i < 0) return -1;
    if (k === nth) return i;
    from = i + 1;
  }
}

/** Nearest ancestor element (inclusive) carrying a `data-sourcepos` attribute. */
function closestSourcePos(node: Node | null): HTMLElement | null {
  let el: Node | null = node;
  while (el && el.nodeType !== Node.ELEMENT_NODE) el = el.parentNode;
  let cur = el as HTMLElement | null;
  while (cur && !cur.hasAttribute("data-sourcepos")) cur = cur.parentElement;
  return cur;
}

function parseSourcePos(
  sp: string,
): { startLine: number; startCol: number; endLine: number; endCol: number } | null {
  const m = /^(\d+):(\d+)-(\d+):(\d+)$/.exec(sp.trim());
  if (!m) return null;
  return {
    startLine: Number(m[1]),
    startCol: Number(m[2]),
    endLine: Number(m[3]),
    endCol: Number(m[4]),
  };
}

/** Resolve the current window selection (inside `article`) to a raw-markdown
 * anchor, or null if it can't be mapped precisely. */
export function selectionToAnchor(article: HTMLElement, body: string): CommentDraft | null {
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || sel.rangeCount === 0) return null;
  const text = sel.toString();
  if (!text.trim()) return null;

  const range = sel.getRangeAt(0);
  if (!article.contains(range.commonAncestorContainer)) return null;

  const block = closestSourcePos(range.startContainer);
  const endBlock = closestSourcePos(range.endContainer);
  if (!block || block !== endBlock) return null; // single block only (v1)

  const pos = parseSourcePos(block.getAttribute("data-sourcepos") ?? "");
  if (!pos) return null;

  const starts = lineStartOffsets(body);
  const blockStart = offsetOf(starts, pos.startLine, pos.startCol);
  const blockEnd = offsetOf(starts, pos.endLine, pos.endCol);
  const slice = body.slice(blockStart, Math.max(blockStart, blockEnd));

  // Which occurrence of the selected text did the user pick? Count how many
  // times it appears in the block's *rendered* text before the selection
  // start, then take that same occurrence in the raw slice (aligns for prose).
  const pre = document.createRange();
  pre.selectNodeContents(block);
  pre.setEnd(range.startContainer, range.startOffset);
  const occurrence = countOccurrences(pre.toString(), text);

  const idx = nthIndexOf(slice, text, occurrence);
  if (idx < 0) return null; // rendered text isn't verbatim in raw (inline syntax)

  const startOffset = blockStart + idx;
  const endOffset = startOffset + text.length;
  return { startOffset, endOffset, quotedText: body.slice(startOffset, endOffset) };
}
