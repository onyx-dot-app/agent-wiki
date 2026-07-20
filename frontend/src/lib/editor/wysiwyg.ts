/** Typora-style live-preview WYSIWYG decorations for the markdown editor.
 *
 * Walks the `@lezer/markdown` syntax tree on every doc/selection change and
 * builds a `DecorationSet` that hides markup characters (`#`, `**`, `` ` ``,
 * `> `, link brackets/URLs) and applies `cm-md-*` classes to the surrounding
 * span for visual styling. The raw markup for a span is revealed again while
 * the selection touches it, so the user can still see/edit what they typed.
 *
 * Storage is untouched — this only decorates the view; the doc string (and
 * the collab op stream, which operates on that string) never changes.
 */
import { syntaxTree } from "@codemirror/language";
import type { EditorState, Range } from "@codemirror/state";
import {
  Decoration,
  type DecorationSet,
  EditorView,
  ViewPlugin,
  type ViewUpdate,
  WidgetType,
} from "@codemirror/view";
import type { SyntaxNodeRef } from "@lezer/common";

const HEADING_CLASS: Record<string, string> = {
  ATXHeading1: "cm-md-h1",
  ATXHeading2: "cm-md-h2",
  ATXHeading3: "cm-md-h3",
  ATXHeading4: "cm-md-h4",
  ATXHeading5: "cm-md-h5",
  ATXHeading6: "cm-md-h6",
};

/** Renders a bullet in place of a `BulletList` item's `-`/`*`/`+` marker. */
class BulletWidget extends WidgetType {
  eq() {
    return true;
  }
  toDOM() {
    const span = document.createElement("span");
    span.className = "cm-md-list-bullet";
    span.textContent = "•";
    return span;
  }
}

/** Renders `N.` in place of an `OrderedList` item's digit marker, preserving
 * the actual number so the list stays readable while collapsed. */
class OrderedMarkerWidget extends WidgetType {
  constructor(readonly text: string) {
    super();
  }
  eq(other: OrderedMarkerWidget) {
    return other.text === this.text;
  }
  toDOM() {
    const span = document.createElement("span");
    span.className = "cm-md-list-number";
    span.textContent = this.text;
    return span;
  }
}

/** Renders a `<hr>` in place of a `---`/`***`/`___` horizontal rule line. */
class HrWidget extends WidgetType {
  eq() {
    return true;
  }
  toDOM() {
    return document.createElement("hr");
  }
}

/** Renders a checkbox in place of a task item's `[ ]`/`[x]` marker. Clicking
 * it rewrites the marker text in the doc — a normal local edit, so the toggle
 * flows through the collab op stream and history like any keystroke. */
class TaskCheckboxWidget extends WidgetType {
  constructor(
    readonly checked: boolean,
    readonly markerLen: number,
  ) {
    super();
  }
  eq(other: TaskCheckboxWidget) {
    return other.checked === this.checked && other.markerLen === this.markerLen;
  }
  toDOM(view: EditorView) {
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = this.checked;
    box.className = "cm-md-task-checkbox";
    box.addEventListener("mousedown", (e) => {
      // Toggle without moving the caret into the marker (which would reveal
      // the raw `[x]` mid-click).
      e.preventDefault();
      const pos = view.posAtDOM(box);
      view.dispatch({
        changes: {
          from: pos,
          to: pos + this.markerLen,
          insert: this.checked ? "[ ]" : "[x]",
        },
      });
    });
    return box;
  }
  /** Clicks are fully handled by the widget's own listener — never let the
   * editor also process them (e.g. place the caret through the checkbox). */
  ignoreEvent() {
    return true;
  }
}

/** True if any selection range overlaps `[from, to]` (touching counts). */
function spanRevealed(state: EditorState, from: number, to: number): boolean {
  return state.selection.ranges.some((r) => r.from <= to && r.to >= from);
}

/** True if any selection range overlaps the line containing `pos`. */
function lineRevealed(state: EditorState, pos: number): boolean {
  const line = state.doc.lineAt(pos);
  return spanRevealed(state, line.from, line.to);
}

/** The enclosing node whose range gates reveal for a markup-mark child (e.g.
 * `EmphasisMark`'s parent `StrongEmphasis`). Falls back to the mark itself. */
function revealScope(node: SyntaxNodeRef): { from: number; to: number } {
  const parent = node.node.parent;
  return parent ? { from: parent.from, to: parent.to } : node;
}

/** Hides `node`'s range unless the selection touches its enclosing span,
 * extending the hidden range over one trailing space (the un-tokenized gap
 * after `#`/`>` markers) so hiding it doesn't leave a stray leading space. */
function hideMark(
  state: EditorState,
  node: SyntaxNodeRef,
  ranges: Range<Decoration>[],
  swallowTrailingSpace = false,
): void {
  const scope = revealScope(node);
  if (spanRevealed(state, scope.from, scope.to)) return;
  let to = node.to;
  if (swallowTrailingSpace && state.doc.sliceString(to, to + 1) === " ") to++;
  ranges.push(Decoration.replace({}).range(node.from, to));
}

function buildDecorations(view: EditorView): DecorationSet {
  const { state } = view;
  const ranges: Range<Decoration>[] = [];

  for (const { from, to } of view.visibleRanges) {
    syntaxTree(state).iterate({
      from,
      to,
      enter(node) {
        switch (node.name) {
          case "ATXHeading1":
          case "ATXHeading2":
          case "ATXHeading3":
          case "ATXHeading4":
          case "ATXHeading5":
          case "ATXHeading6":
            ranges.push(
              Decoration.mark({ class: HEADING_CLASS[node.name] }).range(
                node.from,
                node.to,
              ),
            );
            break;
          case "StrongEmphasis":
            ranges.push(
              Decoration.mark({ class: "cm-md-strong" }).range(
                node.from,
                node.to,
              ),
            );
            break;
          case "Emphasis":
            ranges.push(
              Decoration.mark({ class: "cm-md-em" }).range(node.from, node.to),
            );
            break;
          case "InlineCode":
            ranges.push(
              Decoration.mark({ class: "cm-md-code-inline" }).range(
                node.from,
                node.to,
              ),
            );
            break;
          case "FencedCode":
            ranges.push(
              Decoration.mark({ class: "cm-md-code-block" }).range(
                node.from,
                node.to,
              ),
            );
            break;
          case "Blockquote":
            ranges.push(
              Decoration.mark({ class: "cm-md-blockquote" }).range(
                node.from,
                node.to,
              ),
            );
            break;
          case "Link":
            ranges.push(
              Decoration.mark({ class: "cm-md-link" }).range(
                node.from,
                node.to,
              ),
            );
            break;
          case "HeaderMark":
            hideMark(state, node, ranges, true);
            break;
          case "QuoteMark":
            hideMark(state, node, ranges, true);
            break;
          case "EmphasisMark":
          case "CodeMark":
          case "LinkMark":
            hideMark(state, node, ranges);
            break;
          case "URL":
            // Only the URL inside a `[text](url)` link is markup to hide —
            // a bare autolink's URL is its own visible content.
            if (node.node.parent?.name === "Link")
              hideMark(state, node, ranges);
            break;
          case "ListMark": {
            if (lineRevealed(state, node.from)) break;
            // A task item's checkbox (TaskMarker below) stands in for the
            // bullet, so hide the `-` marker and its trailing space entirely.
            if (node.node.nextSibling?.name === "Task") {
              let to = node.to;
              if (state.doc.sliceString(to, to + 1) === " ") to++;
              ranges.push(Decoration.replace({}).range(node.from, to));
              break;
            }
            const parent = node.node.parent;
            const ordered = parent?.parent?.name === "OrderedList";
            const widget = ordered
              ? new OrderedMarkerWidget(
                  state.doc.sliceString(node.from, node.to),
                )
              : new BulletWidget();
            ranges.push(
              Decoration.replace({ widget }).range(node.from, node.to),
            );
            break;
          }
          case "TaskMarker": {
            if (lineRevealed(state, node.from)) break;
            const checked = /[xX]/.test(
              state.doc.sliceString(node.from, node.to),
            );
            ranges.push(
              Decoration.replace({
                widget: new TaskCheckboxWidget(checked, node.to - node.from),
              }).range(node.from, node.to),
            );
            break;
          }
          case "HorizontalRule":
            if (lineRevealed(state, node.from)) break;
            ranges.push(
              Decoration.replace({ widget: new HrWidget(), block: true }).range(
                node.from,
                node.to,
              ),
            );
            break;
        }
      },
    });
  }

  return Decoration.set(ranges, true);
}

/** Live-preview markdown decorations: hides markup characters and applies
 * `cm-md-*` visual styling, revealing raw syntax while the caret is inside a
 * span. Requires `markdown()` (from `@codemirror/lang-markdown`) earlier in
 * the extension list so `syntaxTree` has a parse to walk. */
export function wysiwygMarkdown() {
  return ViewPlugin.fromClass(
    class {
      decorations: DecorationSet;
      constructor(view: EditorView) {
        this.decorations = buildDecorations(view);
      }
      update(update: ViewUpdate) {
        if (
          update.docChanged ||
          update.selectionSet ||
          update.viewportChanged
        ) {
          this.decorations = buildDecorations(update.view);
        }
      }
    },
    { decorations: (v) => v.decorations },
  );
}
