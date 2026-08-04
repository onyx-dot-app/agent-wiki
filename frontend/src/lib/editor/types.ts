/** Shared types for the Tiptap-based live editor scaffold.
 *
 * Scaffold scope only — see plans/editor.md, Track A P2. No live session
 * yet — that's Track B's WebSocket/schema work. This file is deliberately
 * not the old `lib/editor/types.ts` (CoeditSession, CoeditFrame, etc.) —
 * those are collab-wire-protocol shapes with no equivalent here yet.
 */

/** An anchored `[startOffset, endOffset)` range decorated into the doc —
 * comment threads and source attribution are the two instantiations,
 * differing only in mark classes (see `highlights.ts`). Shape preserved
 * exactly from `lib/editor/highlights.ts`'s type of the same name: three
 * real consumers (`FileView.tsx`, `SourcesPanel.tsx`, `SourceAnchorRail.tsx`)
 * key off it, and keeping it unchanged is what makes the eventual cutover
 * low-risk for those callers. */
export interface AnchoredHighlightTarget {
  /** Owner id (comment thread root or source dedupe key), matched against
   * the active-id list. Several targets may share one id. */
  id: string;
  startOffset: number;
  endOffset: number;
}

export type CommentHighlightTarget = AnchoredHighlightTarget;

/** A pending comment anchored to the current (non-collapsed) selection —
 * shape preserved exactly from `lib/editor/comments.ts`'s type of the same
 * name. `startOffset`/`endOffset` are markdown-source character offsets
 * (see `textOffsets.ts`'s module docstring for the approximation this
 * editor uses to produce them from a ProseMirror selection). */
export interface CommentDraft {
  startOffset: number;
  endOffset: number;
  quotedText: string;
}

/** Imperative handle for scrolling the editor to a raw-doc position — used
 * to bring an anchored comment into view (click-to-focus, `?comment=<id>`
 * deep links) and to back the margin-rail/custom-scrollbar UI. Shape
 * preserved exactly from `lib/editor/components.tsx`'s `CoeditorHandle`:
 * every method except `sourceTargets` is called by a real external consumer
 * (`CommentMarginRail`, `SourceAnchorRail`, `EditorEdgeScrollbar`), confirmed
 * by the P1 research pass — `sourceTargets` is implemented for
 * contract-completeness even though nothing calls it today. */
export interface CoeditorHandle {
  /** Convert a markdown-source character offset (comment/source-span API
   * offsets, as read straight off the wire) into the ProseMirror document
   * position every other method on this handle expects — see
   * `textOffsets.ts`'s module docstring for the approximation and its known
   * limits. Every raw backend offset must go through this before reaching
   * `scrollToOffset`/`anchorLine`; a value already in PM-position space
   * (e.g. from `sourceTargets()`, or a peer's cursor from
   * `useCoeditSession`'s `peers`) must NOT be converted again. */
  textOffsetToPos: (offset: number) => number;
  scrollToOffset: (offset: number) => void;
  /** Scroll to a source's first attributed span, read from the highlight
   * plugin's live-mapped offsets so edits since the fetch are honored. */
  scrollToSource: (id: string) => void;
  /** The source highlight plugin's live-mapped targets, for hosts that
   * anchor UI to span positions (collapsed spans included, callers skip
   * them). */
  sourceTargets: () => AnchoredHighlightTarget[];
  /** Doc-space top and height (px from the document's start) of the block
   * containing a character offset. Stable for off-screen positions (block
   * geometry via `posToDOMRect`, not rendered coordinates). */
  anchorLine: (offset: number) => { top: number; height: number } | null;
  /** The editor's scroll wrapper's current scrollTop. */
  scrollTop: () => number;
  /** Scroll the editor by a wheel delta, for hosts outside the wrapper. */
  scrollBy: (dy: number) => void;
  /** The scroll wrapper's total scrollHeight, the doc-space lower bound. */
  scrollHeight: () => number;
  /** The scroll wrapper's viewport height, for external scrollbar math. */
  clientHeight: () => number;
  /** Viewport-space top of the scroll wrapper, for hosts not sharing its
   * origin. */
  scrollerTop: () => number;
  /** Subscribe to scroll and geometry changes. Scroll notifications fire
   * synchronously inside the scroll event so overlays can repaint in the
   * same frame as the editor. Returns the unsubscriber. */
  subscribeLayout: (cb: (kind: "scroll" | "geometry") => void) => () => void;
}
