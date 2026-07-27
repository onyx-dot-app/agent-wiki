/** Anchored highlight plugins: `[startOffset, endOffset)` ranges decorated
 * into the doc, held in ProseMirror plugin state so edits remap them onto
 * the text they were anchored to. Comments and source attribution are the
 * two instantiations, differing only in mark classes — port of
 * `lib/editor/highlights.ts`'s CodeMirror `StateField`/`StateEffect` version
 * to ProseMirror's plugin-state/transaction-meta equivalent (there's no
 * `StateEffect` here; `tr.setMeta(key, value)` / `tr.getMeta(key)` is the
 * out-of-band channel, and a `DecorationSet` remaps itself through edits via
 * `.map(tr.mapping, tr.doc)` instead of hand-mapping each offset). */
import { Extension, type Editor } from "@tiptap/core";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { Decoration, DecorationSet } from "@tiptap/pm/view";
import type { AnchoredHighlightTarget } from "@/lib/tiptapEditor/types";

interface HighlightClasses {
  idle: string;
  active: string;
}

interface HighlightMeta {
  targets: AnchoredHighlightTarget[];
  activeIds: string[];
}

interface HighlightPluginState extends HighlightMeta {
  deco: DecorationSet;
}

function buildDecorations(
  targets: AnchoredHighlightTarget[],
  activeIds: string[],
  classes: HighlightClasses,
  doc: Parameters<typeof DecorationSet.create>[0],
): DecorationSet {
  const docSize = doc.content.size;
  const decorations: Decoration[] = [];
  for (const t of targets) {
    const from = Math.max(0, Math.min(t.startOffset, docSize));
    const to = Math.max(from, Math.min(t.endOffset, docSize));
    if (from === to) continue;
    decorations.push(
      Decoration.inline(from, to, {
        class: activeIds.includes(t.id) ? classes.active : classes.idle,
      }),
    );
  }
  return DecorationSet.create(doc, decorations);
}

/** A highlight plugin's exported handle: the plugin itself (register via
 * `addProseMirrorPlugins`) plus typed helpers for pushing new data in from
 * React and reading the current target list back out. */
export interface HighlightPlugin {
  key: PluginKey<HighlightPluginState>;
  plugin: Plugin<HighlightPluginState>;
  setTargets: (editor: Editor, targets: AnchoredHighlightTarget[]) => void;
  setActiveIds: (editor: Editor, ids: string[]) => void;
  targets: (editor: Editor) => AnchoredHighlightTarget[];
}

export function anchoredHighlightPlugin(
  name: string,
  classes: HighlightClasses,
): HighlightPlugin {
  const key = new PluginKey<HighlightPluginState>(name);

  const plugin = new Plugin<HighlightPluginState>({
    key,
    state: {
      init: () => ({ targets: [], activeIds: [], deco: DecorationSet.empty }),
      apply(tr, value, _oldState, newState) {
        let { targets, activeIds } = value;
        let deco = value.deco;
        if (tr.docChanged) {
          deco = deco.map(tr.mapping, tr.doc);
          // Boundary-typed text stays outside the range — mirrors the CM6
          // version's mapPos(assoc) bias (start maps after an insertion at
          // the start, end maps before one at the end). DecorationSet.map
          // doesn't expose per-boundary assoc, so the plain offsets are
          // remapped the same way for the *next* full rebuild (a fresh
          // setTargets/setActiveIds call), keeping the two representations
          // (targets list vs. live decorations) from drifting apart.
          targets = targets.map((t) => ({
            ...t,
            startOffset: tr.mapping.map(t.startOffset, 1),
            endOffset: tr.mapping.map(t.endOffset, -1),
          }));
        }
        const meta = tr.getMeta(key) as Partial<HighlightMeta> | undefined;
        if (meta) {
          if (meta.targets) targets = meta.targets;
          if (meta.activeIds) activeIds = meta.activeIds;
          deco = buildDecorations(targets, activeIds, classes, newState.doc);
        }
        if (
          targets === value.targets &&
          activeIds === value.activeIds &&
          deco === value.deco
        ) {
          return value;
        }
        return { targets, activeIds, deco };
      },
    },
    props: {
      decorations: (state) => key.getState(state)?.deco ?? DecorationSet.empty,
    },
  });

  return {
    key,
    plugin,
    setTargets(editor, targets) {
      editor.view.dispatch(
        editor.view.state.tr.setMeta(key, {
          targets,
        } satisfies Partial<HighlightMeta>),
      );
    },
    setActiveIds(editor, ids) {
      editor.view.dispatch(
        editor.view.state.tr.setMeta(key, {
          activeIds: ids,
        } satisfies Partial<HighlightMeta>),
      );
    },
    targets(editor) {
      return key.getState(editor.state)?.targets ?? [];
    },
  };
}

export const commentHighlights = anchoredHighlightPlugin("commentHighlights", {
  idle: "tt-comment-highlight",
  active: "tt-comment-highlight-active",
});

export const sourceHighlights = anchoredHighlightPlugin("sourceHighlights", {
  idle: "tt-source-highlight",
  active: "tt-source-highlight-active",
});

/** Registers both highlight plugins on the editor. One Tiptap `Extension`
 * for both, not two, since neither has any config of its own worth
 * splitting over — `addProseMirrorPlugins` just returns the raw `Plugin`s
 * built above. */
export const AnchoredHighlights = Extension.create({
  name: "anchoredHighlights",
  addProseMirrorPlugins() {
    return [commentHighlights.plugin, sourceHighlights.plugin];
  },
});
