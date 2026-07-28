/** Shared types for the Tiptap-based live editor scaffold.
 *
 * Scaffold scope only — see plans/editor.md, Track A P2. No live session
 * yet — that's Track B's WebSocket/schema work. This file is deliberately
 * not the old `lib/editor/types.ts` (CoeditSession, CoeditFrame, etc.) —
 * those are collab-wire-protocol shapes with no equivalent here yet.
 */
import type { Editor } from "@tiptap/core";
import type { Awareness } from "y-protocols/awareness";
import type * as Y from "yjs";

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

export interface TiptapEditorProps {
  /** The Yjs doc this editor's content lives in. Defaults to a fresh local
   * `Y.Doc` if omitted (P1's scaffold behavior — never synced anywhere).
   * Real live sessions (Track B) and multi-client verification (two editors
   * sharing two relayed docs) both need to supply their own. */
  doc?: Y.Doc;
  /** This client's Awareness instance (Yjs's ephemeral, non-document shared
   * state — presence/cursor data, separate from `doc`'s content). Defaults
   * to a fresh local instance tied to `doc` if omitted, owned and destroyed
   * by this component; a caller-supplied one (a real live session, or
   * multi-client verification wiring its own relay) is left for the caller
   * to destroy. */
  awareness?: Awareness;
  /** This client's id for presence/coloring — a stable random id if
   * omitted. Distinct clients sharing a `doc` must pass distinct ids. */
  userId?: string;
  /** This client's display name, shown on its caret in peers' views. */
  userDisplay?: string;
  /** Render without accepting edits. */
  readOnly?: boolean;
  /** Placeholder text shown on an empty document. */
  placeholder?: string;
  /** The wiki page path this editor is editing, used to scope image uploads
   * (`POST /api/wiki/images?path=...`). Omitted by the scaffold/multi-client
   * verification harness, which has no real page - image paste/drop then
   * falls through to default handling instead of uploading. */
  pagePath?: string;
  /** Comment thread spans to highlight in the doc. */
  commentHighlights?: CommentHighlightTarget[];
  /** Thread ids whose spans get the stronger (active) highlight. */
  activeCommentIds?: string[];
  /** Fires with the thread ids whose spans contain the caret (or intersect
   * the selection), deduped against the last report. */
  onCommentCaret?: (ids: string[]) => void;
  /** Source-attributed spans to highlight while the Sources tab is open. */
  sourceHighlights?: AnchoredHighlightTarget[];
  /** Source keys whose spans get the stronger (active) highlight. */
  activeSourceIds?: string[];
  /** Fires with the source ids whose spans contain the caret (or intersect
   * the selection), deduped against the last report. */
  onSourceCaret?: (ids: string[]) => void;
  /** Fires on every selection change with the current selection as a
   * comment draft (null if collapsed) plus its on-screen coordinates, for
   * the caller to position a floating "Comment" affordance. */
  onSelectionForComment?: (
    draft: CommentDraft | null,
    coords: { x: number; y: number } | null,
  ) => void;
  /** Fires once the underlying Tiptap `Editor` is created — for callers that
   * need the raw editor instance itself (seeding content, computing document
   * positions for a test harness), distinct from `CoeditorHandle` below,
   * which covers the scroll/geometry surface other components actually
   * depend on. */
  onEditorReady?: (editor: Editor) => void;
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
