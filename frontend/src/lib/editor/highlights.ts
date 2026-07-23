/** Anchored highlight fields: `[from, to)` offset ranges decorated into the
 * doc, held in editor state so local edits map them onto the text they were
 * anchored to. Comments and source attribution are the two instantiations,
 * differing only in mark classes. */
import type { Range } from "@codemirror/state";
import { StateEffect, StateField } from "@codemirror/state";
import { Decoration, type DecorationSet, EditorView } from "@codemirror/view";

export interface AnchoredHighlightTarget {
  /** Owner id (comment thread root or source dedupe key), matched against
   * the active-id effect. Several targets may share one id. */
  id: string;
  startOffset: number;
  endOffset: number;
}

interface HighlightClasses {
  idle: string;
  active: string;
}

function buildDecorations(
  targets: AnchoredHighlightTarget[],
  activeIds: string[],
  classes: HighlightClasses,
  docLen: number,
): DecorationSet {
  const ranges: Range<Decoration>[] = [];
  for (const t of targets) {
    const from = Math.max(0, Math.min(t.startOffset, docLen));
    const to = Math.max(from, Math.min(t.endOffset, docLen));
    if (from === to) continue;
    ranges.push(
      Decoration.mark({
        class: activeIds.includes(t.id) ? classes.active : classes.idle,
      }).range(from, to),
    );
  }
  return Decoration.set(ranges, true);
}

/** Build a highlight field plus its two effects. Doc changes map the held
 * offsets through the edit so a highlight stays on the text it was anchored
 * to. Fresh server offsets and active ids only arrive via the effects. */
export function anchoredHighlightField(classes: HighlightClasses) {
  const setTargets = StateEffect.define<AnchoredHighlightTarget[]>();
  const setActive = StateEffect.define<string[]>();
  const field = StateField.define<{
    targets: AnchoredHighlightTarget[];
    activeIds: string[];
    deco: DecorationSet;
  }>({
    create: () => ({ targets: [], activeIds: [], deco: Decoration.none }),
    update(value, tr) {
      let targets = value.targets;
      let activeIds = value.activeIds;
      if (tr.docChanged) {
        // Boundary-typed text stays outside the range (start maps after an
        // insertion at the start, end maps before one at the end).
        targets = targets.map((t) => ({
          ...t,
          startOffset: tr.changes.mapPos(t.startOffset, 1),
          endOffset: tr.changes.mapPos(t.endOffset, -1),
        }));
      }
      for (const e of tr.effects) {
        if (e.is(setTargets)) targets = e.value;
        if (e.is(setActive)) activeIds = e.value;
      }
      if (targets === value.targets && activeIds === value.activeIds)
        return value;
      return {
        targets,
        activeIds,
        deco: buildDecorations(
          targets,
          activeIds,
          classes,
          tr.state.doc.length,
        ),
      };
    },
    provide: (f) => EditorView.decorations.from(f, (v) => v.deco),
  });
  return { field, setTargets, setActive };
}

export const commentHighlights = anchoredHighlightField({
  idle: "cm-comment-highlight",
  active: "cm-comment-highlight-active",
});

// The sources view paints every attributed span at Highlight/Active (mock
// 1832:81274), so the active class never differs from idle.
export const sourceHighlights = anchoredHighlightField({
  idle: "cm-source-highlight",
  active: "cm-source-highlight",
});
