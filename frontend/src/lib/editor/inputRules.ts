/**
 * Typed-pattern shortcuts (`"# "` -> heading, `"**x**"` -> bold, etc.) —
 * this codebase's whole reason for building on raw ProseMirror instead of
 * Tiptap's StarterKit is losing exactly this behavior for free, so it's
 * rebuilt by hand here rather than skipped. `prosemirror-inputrules` only
 * ships block-level helpers (`wrappingInputRule`/`textblockTypeInputRule`);
 * `markInputRule` (bold/italic/code) is a small hand-rolled addition below,
 * the standard pattern for this gap.
 */

import type { Attrs, MarkType, Node as PMNode } from "prosemirror-model";
import {
  InputRule,
  inputRules,
  textblockTypeInputRule,
  wrappingInputRule,
} from "prosemirror-inputrules";
import { TextSelection, type Plugin } from "prosemirror-state";
import { agentWikiSchema as schema } from "./schema";

/** Wraps a delimited-text pattern (`**bold**`, `` `code` ``, ...) into a
 * mark application. `regexp`'s single capture group is the inner text
 * (without delimiters); the delimiters + inner text together are
 * `match[0]`. Deletes the trailing delimiter before the leading one so the
 * still-untouched leading position stays valid across both deletes (a
 * transaction's position args aren't auto-remapped between steps you add
 * to it — only earlier, unaffected ranges stay safe to reference as-is). */
function markInputRule(regexp: RegExp, markType: MarkType): InputRule {
  return new InputRule(regexp, (state, match, start, end) => {
    const inner = match[1];
    if (!inner) return null;
    const full = match[0];
    const innerOffset = full.lastIndexOf(inner);
    const innerStart = start + innerOffset;
    const innerEnd = innerStart + inner.length;
    const tr = state.tr;
    if (end > innerEnd) tr.delete(innerEnd, end);
    if (innerStart > start) tr.delete(start, innerStart);
    tr.addMark(start, start + inner.length, markType.create());
    tr.removeStoredMark(markType);
    return tr;
  });
}

const headingRule = textblockTypeInputRule(
  /^(#{1,6})\s$/,
  schema.nodes.heading,
  (match) => ({
    level: match[1].length,
  }),
);

const blockquoteRule = wrappingInputRule(/^\s*>\s$/, schema.nodes.blockquote);

const codeBlockRule = textblockTypeInputRule(
  /^```\s?$/,
  schema.nodes.codeBlock,
);

/** `---`/`***`/`___` on an otherwise-empty line -> a thematic break, plus a
 * fresh empty paragraph after it to keep typing in (a thematic break holds
 * only its own raw source text — `markdown_yjs.py`'s opaque-verbatim
 * round-trip — so it isn't a place to keep typing into). Unlike the
 * `textblockTypeInputRule`-based rules above, this replaces the whole
 * matched textblock with two nodes, so it's hand-rolled rather than reusing
 * that helper (mirrors the standard `$start.before()`/`$start.after()`
 * "replace this whole textblock" idiom other editors use for the same
 * rule). */
const thematicBreakRule = new InputRule(
  /^(?:---|\*\*\*|___)$/,
  (state, match, start) => {
    const $start = state.doc.resolve(start);
    if (
      !$start
        .node(-1)
        .canReplaceWith(
          $start.index(-1),
          $start.indexAfter(-1),
          schema.nodes.thematic_break,
        )
    ) {
      return null;
    }
    const hr = schema.nodes.thematic_break.create({}, schema.text(match[0]));
    const para = schema.nodes.paragraph.create();
    const before = $start.before();
    const tr = state.tr.replaceWith(before, $start.after(), [hr, para]);
    return tr.setSelection(
      TextSelection.create(tr.doc, before + hr.nodeSize + 1),
    );
  },
);

const bulletListRule = wrappingInputRule(
  /^\s*([-+*])\s$/,
  schema.nodes.bulletList,
);

// "- [ ] "/"- [x] " before the plain bullet rule so it wins on that exact
// pattern (both regexes would otherwise match "- " as a prefix — input
// rule order is match-first, and this one is strictly more specific).
// checked always starts false via the schema default: capturing an
// already-checked "[x]" at type time isn't handled here — toggle the
// rendered checkbox after — same "input rules cover creation, not every
// attribute" tradeoff as ordered-list start-number continuation below.
const taskListRule = wrappingInputRule(
  /^\s*-\s\[[ xX]?\]\s$/,
  schema.nodes.taskList,
);

const orderedListRule = wrappingInputRule(
  /^(\d+)\.\s$/,
  schema.nodes.orderedList,
  (match): Attrs => ({ start: Number(match[1]) }),
  (match, node: PMNode) =>
    node.childCount + Number(node.attrs.start) === Number(match[1]),
);

const boldRule = markInputRule(
  /(?:^|\s)\*\*(?!\s+\*\*)([^*]+)\*\*(?!\s+\*\*)$/,
  schema.marks.bold,
);
const boldRuleAlt = markInputRule(
  /(?:^|\s)__(?!\s+__)([^_]+)__(?!\s+__)$/,
  schema.marks.bold,
);
const italicRule = markInputRule(
  /(?:^|\s)\*(?!\s+\*)([^*]+)\*(?!\s+\*)$/,
  schema.marks.italic,
);
const italicRuleAlt = markInputRule(
  /(?:^|\s)_(?!\s+_)([^_]+)_(?!\s+_)$/,
  schema.marks.italic,
);
const codeRule = markInputRule(
  /(?:^|\s)`(?!\s+`)([^`]+)`(?!\s+`)$/,
  schema.marks.code,
);

export function agentWikiInputRules(): Plugin {
  return inputRules({
    rules: [
      headingRule,
      blockquoteRule,
      codeBlockRule,
      thematicBreakRule,
      taskListRule,
      bulletListRule,
      orderedListRule,
      boldRule,
      boldRuleAlt,
      italicRule,
      italicRuleAlt,
      codeRule,
    ],
  });
}
