/** Map between a rendered-view text selection and raw-markdown character offsets.
 *
 * The wiki page renders raw markdown via react-markdown with `rehypeSourcePos`,
 * which stamps every element (block *and* inline — `<p>`, `<strong>`, `<code>`,
 * `<a>`, …) with `data-sourcepos="L:C-L:C"` (1-based source line:col). For a
 * given element that gives us the char range in the raw body its content came
 * from.
 *
 * The hard part is mapping *within* an element: the rendered text drops the
 * markdown syntax (`**`, `` ` ``, `[`…`](url)`), so rendered char N isn't raw
 * char N. We bridge that with `alignBlock`: a whitespace-tolerant subsequence
 * alignment between an element's rendered text and its raw slice. Markdown only
 * ever *adds* syntax characters, so the rendered text is a subsequence of the
 * raw slice — walking both with two pointers and skipping the raw-only syntax
 * chars yields, for each rendered char, the exact raw offset it came from.
 *
 * That single primitive runs both directions:
 *  - `selectionToAnchor` (rendered selection → raw offsets) — anchors a new
 *    comment, and works across inline syntax (`**bold**`) and across multiple
 *    blocks, because each selection endpoint is resolved to an *absolute* body
 *    offset independently via the nearest source-positioned element.
 *  - `paintCommentHighlights` (raw offsets → rendered ranges) — repaints stored
 *    comment spans, splitting a span across every block it overlaps.
 *
 * Alignment fails (→ null / skip) only when a rendered char has no counterpart
 * in the raw slice (e.g. an HTML entity like `&lt;`); such selections just
 * don't get the affordance. The backend stores raw code-point offsets and
 * treats `quoted_text` as display-only, so wider/multi-block spans are fine
 * server-side.
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

/** A source-positioned element aligned to its raw-markdown slice. `rawAt[i]` is
 * the absolute body offset of the raw char that rendered char `i` came from
 * (strictly increasing). */
interface BlockAlign {
  el: HTMLElement;
  blockStart: number;
  blockEnd: number;
  rawAt: number[];
}

/** Two chars are "the same" for alignment if equal, or both whitespace —
 * markdown collapses/normalizes whitespace (soft breaks → space/newline), so we
 * don't require the exact whitespace char to match. */
function charsAlign(raw: string, rendered: string): boolean {
  if (raw === rendered) return true;
  return /\s/.test(raw) && /\s/.test(rendered);
}

/** Align an element's rendered text to its raw-markdown slice by subsequence
 * walk, skipping raw-only syntax chars. Rendered whitespace with no raw
 * counterpart is tolerated (react-markdown adds newlines around block
 * structure); a non-whitespace rendered char with no counterpart returns null
 * (the selection/highlight then just isn't mapped). */
function alignBlock(el: HTMLElement, body: string, starts: number[]): BlockAlign | null {
  const pos = parseSourcePos(el.getAttribute("data-sourcepos") ?? "");
  if (!pos) return null;
  const blockStart = offsetOf(starts, pos.startLine, pos.startCol);
  const blockEnd = offsetOf(starts, pos.endLine, pos.endCol);
  const raw = body.slice(blockStart, Math.max(blockStart, blockEnd));
  const rendered = el.textContent ?? "";

  const rawAt: number[] = new Array<number>(rendered.length);
  let j = 0;
  for (let i = 0; i < rendered.length; i++) {
    const ch = rendered[i]!;
    let k = j;
    while (k < raw.length && !charsAlign(raw[k]!, ch)) k++;
    if (k < raw.length) {
      rawAt[i] = blockStart + k;
      j = k + 1;
    } else if (/\s/.test(ch)) {
      // Rendered whitespace with no raw counterpart: react-markdown emits
      // extra newlines around block structure (e.g. between a list item's text
      // and its nested sublist) that the raw slice doesn't carry. Map it to the
      // current position without consuming raw, instead of failing the block.
      rawAt[i] = blockStart + Math.min(j, raw.length);
    } else {
      return null; // a non-whitespace rendered char absent from raw (e.g. an entity)
    }
  }
  return { el, blockStart, blockEnd, rawAt };
}

/** Count of rendered chars from the start of `block` up to a (container, offset)
 * selection boundary — i.e. the boundary's index into `block.textContent`. */
function renderedOffsetInBlock(block: HTMLElement, container: Node, offset: number): number {
  const r = document.createRange();
  r.selectNodeContents(block);
  r.setEnd(container, offset);
  return r.toString().length;
}

/** Absolute body offset where rendered char `i` of a block *starts*. */
function rawStartOffset(a: BlockAlign, i: number): number {
  if (a.rawAt.length === 0) return a.blockStart;
  if (i >= a.rawAt.length) return a.blockEnd;
  return a.rawAt[Math.max(0, i)]!;
}

/** Absolute body offset just past rendered char `i - 1` (an exclusive end at
 * rendered index `i`). Excludes any trailing syntax after the last char. */
function rawEndOffset(a: BlockAlign, i: number): number {
  if (a.rawAt.length === 0) return a.blockEnd;
  if (i <= 0) return a.blockStart;
  return a.rawAt[Math.min(i, a.rawAt.length) - 1]! + 1;
}

/** Resolve the current window selection (inside `article`) to a raw-markdown
 * anchor, or null if it can't be mapped. Each endpoint is resolved against the
 * nearest source-positioned element, so selections that cross inline syntax
 * (`**bold**`) or span multiple blocks map fine — both endpoints become
 * absolute body offsets. */
export function selectionToAnchor(article: HTMLElement, body: string): CommentDraft | null {
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || sel.rangeCount === 0) return null;
  if (!sel.toString().trim()) return null;

  const range = sel.getRangeAt(0);
  if (!article.contains(range.commonAncestorContainer)) return null;

  const startEl = closestSourcePos(range.startContainer);
  const endEl = closestSourcePos(range.endContainer);
  if (!startEl || !endEl) return null;

  const starts = lineStartOffsets(body);
  const startAlign = alignBlock(startEl, body, starts);
  const endAlign = alignBlock(endEl, body, starts);
  if (!startAlign || !endAlign) return null;

  const startOffset = rawStartOffset(
    startAlign,
    renderedOffsetInBlock(startEl, range.startContainer, range.startOffset),
  );
  const endOffset = rawEndOffset(
    endAlign,
    renderedOffsetInBlock(endEl, range.endContainer, range.endOffset),
  );
  if (endOffset <= startOffset) return null;

  return { startOffset, endOffset, quotedText: body.slice(startOffset, endOffset) };
}

// --------------------------------------------------------------------------- //
// Inline highlights — paint commented spans via the CSS Custom Highlight API  //
// (registers Ranges, no DOM mutation, so it doesn't fight react-markdown).    //
// --------------------------------------------------------------------------- //

const HIGHLIGHT_NAME = "wiki-comment";
const ACTIVE_HIGHLIGHT_NAME = "wiki-comment-active";

export interface HighlightTarget {
  startOffset: number;
  endOffset: number;
  quotedText: string;
  /** The selected/active thread gets the stronger (orange) highlight. */
  active?: boolean;
}

// Minimal typings for the CSS Custom Highlight API (not in every TS lib yet).
interface HighlightLike {
  set(name: string, value: object): void;
  delete(name: string): void;
}
type HighlightCtor = new (...ranges: Range[]) => object;

function highlightRegistry(): HighlightLike | null {
  const reg = (CSS as unknown as { highlights?: HighlightLike }).highlights;
  return reg ?? null;
}

/** True if `el` has no source-positioned ancestor up to (but excluding)
 * `article` — i.e. it's a top-level rendered block, not a nested inline span.
 * Painting only top-level blocks keeps their char ranges disjoint so a span is
 * never double-registered. */
function isTopLevelBlock(el: HTMLElement, article: HTMLElement): boolean {
  let p = el.parentElement;
  while (p && p !== article) {
    if (p.hasAttribute("data-sourcepos")) return false;
    p = p.parentElement;
  }
  return true;
}

/** DOM Range over rendered offsets `[start, end)` (indices into the element's
 * concatenated text) of `el`. */
function rangeForRenderedSpan(el: HTMLElement, start: number, end: number): Range | null {
  if (end <= start) return null;
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  let acc = 0;
  let startNode: Text | null = null;
  let startOff = 0;
  let endNode: Text | null = null;
  let endOff = 0;
  let n = walker.nextNode();
  while (n) {
    const t = n as Text;
    const len = t.length;
    if (startNode === null && start < acc + len) {
      startNode = t;
      startOff = start - acc;
    }
    if (startNode !== null && end <= acc + len) {
      endNode = t;
      endOff = end - acc;
      break;
    }
    acc += len;
    n = walker.nextNode();
  }
  if (!startNode || !endNode) return null;
  const range = document.createRange();
  range.setStart(startNode, startOff);
  range.setEnd(endNode, endOff);
  return range;
}

/** Repaint comment highlights over the rendered article. Pass an empty list to
 * clear (e.g. when entering edit mode). No-op where the API is unsupported.
 *
 * Returns the number of target ranges actually painted. Callers use this to
 * detect "DOM not ready yet" (asked for N, painted < N) and retry — react-
 * markdown commits its text nodes a tick after React renders, so an eager paint
 * finds nothing to range over. Returns the target count when unsupported so
 * callers don't retry forever on a browser without the Custom Highlight API. */
export function paintCommentHighlights(
  article: HTMLElement,
  body: string,
  targets: HighlightTarget[],
): number {
  const reg = highlightRegistry();
  const Ctor = (globalThis as { Highlight?: HighlightCtor }).Highlight;
  if (!reg || !Ctor) return targets.length;

  if (targets.length === 0) {
    reg.delete(HIGHLIGHT_NAME);
    reg.delete(ACTIVE_HIGHLIGHT_NAME);
    return 0;
  }

  const starts = lineStartOffsets(body);
  const blocks: BlockAlign[] = [];
  article.querySelectorAll<HTMLElement>("[data-sourcepos]").forEach((el) => {
    if (!isTopLevelBlock(el, article)) return;
    const a = alignBlock(el, body, starts);
    if (a) blocks.push(a);
  });

  const defaultRanges: Range[] = [];
  const activeRanges: Range[] = [];
  let painted = 0;
  for (const t of targets) {
    let any = false;
    // A span can cross several blocks — paint the overlapping slice of each.
    for (const a of blocks) {
      if (a.blockEnd <= t.startOffset || a.blockStart >= t.endOffset) continue;
      let lo = -1;
      let hi = -1;
      for (let i = 0; i < a.rawAt.length; i++) {
        const off = a.rawAt[i]!;
        if (off >= t.startOffset && off < t.endOffset) {
          if (lo < 0) lo = i;
          hi = i;
        }
      }
      if (lo < 0) continue;
      const range = rangeForRenderedSpan(a.el, lo, hi + 1);
      if (range) {
        (t.active ? activeRanges : defaultRanges).push(range);
        any = true;
      }
    }
    if (any) painted += 1;
  }

  // Two registries: default (light) and the selected thread (strong).
  if (defaultRanges.length) reg.set(HIGHLIGHT_NAME, new Ctor(...defaultRanges));
  else reg.delete(HIGHLIGHT_NAME);
  if (activeRanges.length) reg.set(ACTIVE_HIGHLIGHT_NAME, new Ctor(...activeRanges));
  else reg.delete(ACTIVE_HIGHLIGHT_NAME);

  return painted;
}
