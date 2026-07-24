/**
 * A ProseMirror plugin factory for comment/source anchor highlighting.
 * Instantiated twice in `components.tsx` (comments, sources) — same shape
 * as the old CodeMirror `anchoredHighlightField`, ported to PM's
 * `Decoration`/`StateField` primitives instead of CM6's.
 *
 * Two-stage resolution, matching the backend design
 * (`app/wiki/coedit_ws.py:resolve_live_spans`,
 * `Engineering Projects/Agent Wiki Project/design/Co-Editing.md`):
 *
 * 1. **Block-id resolution** (`resolveAnchor`, run once per `setTargets`
 *    call — a fresh fetch's results): the backend already re-anchored each
 *    span across git history *and* onto this session's live doc, returning
 *    `{blockId, blockOffset}` instead of a flat offset (a flat offset into
 *    some other document's text has no direct meaning as a PM position).
 *    Resolves to a PM position by finding the live node carrying that
 *    `_blockId` and walking its text.
 * 2. **Live-session tracking** (every subsequent transaction): once
 *    resolved to a PM position, ordinary `tr.mapping` keeps it correct
 *    through further edits — including a peer's remote edits, which
 *    `y-prosemirror` applies as real PM transactions with real mapping
 *    info, not a side channel. No CRDT-level position tracking needed for
 *    this part; PM's own machinery already covers it.
 *
 * `blockOffset` is an offset into the block's *markdown-serialized* text
 * (matching `_blockId`'s size on the backend), not a PM content position —
 * mark delimiters (`**`, `` ` ``) and hard breaks (`  \n`) add characters
 * the serializer emits that a PM node's plain `textContent` doesn't. This
 * resolves via `textContent` regardless, as an approximation: exact for an
 * unmarked run, off by roughly the accumulated delimiter-character count
 * when the target offset falls after marked text earlier in the same
 * block. Same "correct, not necessarily exact" tradeoff already accepted
 * throughout the markdown<->Yjs codec (see backend docstrings) — a
 * reasonable simplification for now, not a silent gap: comment/source
 * anchors are coarse (sentence/paragraph-scale) spans, not
 * single-character-precise ones, so a few characters of drift in a rare
 * marked-text case degrades the highlight boundary slightly rather than
 * breaking it.
 */

import type { Node as PMNode } from "prosemirror-model";
import { Plugin, type PluginKey } from "prosemirror-state";
import { Decoration, DecorationSet } from "prosemirror-view";

export interface LiveAnchor {
  blockId: string;
  blockOffset: number;
}

export interface AnchoredHighlightTarget {
  id: string;
  start: LiveAnchor;
  end: LiveAnchor;
}

interface ResolvedTarget {
  id: string;
  from: number;
  to: number;
}

export interface HighlightState {
  resolved: ResolvedTarget[];
  activeIds: Set<string>;
  decorations: DecorationSet;
}

interface HighlightClasses {
  idle: string;
  active: string;
}

interface HighlightMeta {
  setTargets?: AnchoredHighlightTarget[];
  setActive?: string[];
}

function findBlockPos(doc: PMNode, blockId: string): number | null {
  let found: number | null = null;
  doc.descendants((node, pos) => {
    if (found !== null) return false;
    if (node.attrs?._blockId === blockId) {
      found = pos;
      return false;
    }
    return true;
  });
  return found;
}

function resolveAnchor(doc: PMNode, anchor: LiveAnchor): number | null {
  const blockPos = findBlockPos(doc, anchor.blockId);
  if (blockPos === null) return null;
  const node = doc.nodeAt(blockPos);
  if (!node) return null;
  const contentPos = blockPos + 1;
  const text = node.textContent;
  const clamped = Math.max(0, Math.min(anchor.blockOffset, text.length));
  return contentPos + clamped;
}

function resolveTargets(
  doc: PMNode,
  targets: AnchoredHighlightTarget[],
): ResolvedTarget[] {
  const resolved: ResolvedTarget[] = [];
  for (const t of targets) {
    const from = resolveAnchor(doc, t.start);
    const to = resolveAnchor(doc, t.end);
    if (from === null || to === null || to <= from) continue;
    resolved.push({ id: t.id, from, to });
  }
  return resolved;
}

function buildDecorations(
  doc: PMNode,
  resolved: ResolvedTarget[],
  activeIds: Set<string>,
  classes: HighlightClasses,
): DecorationSet {
  const decos = resolved
    .filter((r) => r.from >= 0 && r.to <= doc.content.size && r.to > r.from)
    .map((r) =>
      Decoration.inline(r.from, r.to, {
        class: activeIds.has(r.id) ? classes.active : classes.idle,
      }),
    );
  return DecorationSet.create(doc, decos);
}

/** `key` must be unique per instance (comments vs. sources get separate
 * plugin instances/keys) — `view.dispatch`ing `setHighlightTargets`/
 * `setHighlightActive` needs it to address the right one. */
export function anchoredHighlightPlugin(
  key: PluginKey<HighlightState>,
  classes: HighlightClasses,
): Plugin<HighlightState> {
  return new Plugin<HighlightState>({
    key,
    state: {
      init: (): HighlightState => ({
        resolved: [],
        activeIds: new Set(),
        decorations: DecorationSet.empty,
      }),
      apply(tr, value, _oldState, newState): HighlightState {
        const meta = tr.getMeta(key) as HighlightMeta | undefined;
        let { resolved, activeIds } = value;

        if (meta?.setTargets !== undefined) {
          resolved = resolveTargets(newState.doc, meta.setTargets);
        } else if (tr.docChanged) {
          resolved = resolved.map((r) => ({
            id: r.id,
            from: tr.mapping.map(r.from, 1),
            to: tr.mapping.map(r.to, -1),
          }));
        }

        if (meta?.setActive !== undefined) {
          activeIds = new Set(meta.setActive);
        }

        const decorations =
          meta?.setTargets !== undefined ||
          meta?.setActive !== undefined ||
          tr.docChanged
            ? buildDecorations(newState.doc, resolved, activeIds, classes)
            : value.decorations;

        return { resolved, activeIds, decorations };
      },
    },
    props: {
      decorations: (state) =>
        key.getState(state)?.decorations ?? DecorationSet.empty,
    },
  });
}

export function setHighlightTargets(
  key: PluginKey<HighlightState>,
  dispatch: (tr: import("prosemirror-state").Transaction) => void,
  state: import("prosemirror-state").EditorState,
  targets: AnchoredHighlightTarget[],
): void {
  dispatch(
    state.tr.setMeta(key, { setTargets: targets } satisfies HighlightMeta),
  );
}

export function setHighlightActive(
  key: PluginKey<HighlightState>,
  dispatch: (tr: import("prosemirror-state").Transaction) => void,
  state: import("prosemirror-state").EditorState,
  activeIds: string[],
): void {
  dispatch(
    state.tr.setMeta(key, { setActive: activeIds } satisfies HighlightMeta),
  );
}

/** Which target ids the resolved selection currently intersects — drives
 * `onCommentCaret`/`onSourceCaret`. Half-open range match: a caret counts
 * at `from === to === pos` when `pos` falls in `[from, to)`; a real
 * selection range counts on any overlap. */
export function caretHitIds(
  key: PluginKey<HighlightState>,
  state: import("prosemirror-state").EditorState,
  from: number,
  to: number,
): string[] {
  const highlightState = key.getState(state);
  if (!highlightState) return [];
  const collapsed = from === to;
  return highlightState.resolved
    .filter((r) =>
      collapsed ? from >= r.from && from < r.to : from < r.to && to > r.from,
    )
    .map((r) => r.id);
}
