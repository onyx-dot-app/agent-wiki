import type { JSX } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { remarkBareSpaceLinks } from "@/lib/remarkBareSpaceLinks";

import type { WordDiff } from "@/lib/wiki/types";

import type { AnnotatedEntry } from "./diffEntries";
import styles from "./DiffHunk.module.css";

const HEADING_RE = /^(#{1,6})\s+(.*)$/;
const LIST_RE = /^(\s*)([-*+])\s+(.*)$/;
const ORDERED_LIST_RE = /^(\s*)(\d+\.)\s+(.*)$/;
const BLOCKQUOTE_RE = /^(>+)\s*(.*)$/;

/** Wraps the changed run inside a line. Carries the navigator scroll anchor
    so jumping lands on the changed words, not the top of a wrapped line. */
function WordChips({ w, anchor }: { w: WordDiff | null; anchor?: number }) {
  if (!w) return null;
  return (
    <span className={styles.wordChange} data-change-index={anchor}>
      {w.removed ? <del className={styles.wordRemoved}>{w.removed}</del> : null}
      {w.added ? <ins className={styles.wordAdded}>{w.added}</ins> : null}
    </span>
  );
}

function WordLine({ w, anchor }: { w: WordDiff | null; anchor?: number }) {
  if (!w) return null;

  const headingMatch = HEADING_RE.exec(w.prefix);
  if (headingMatch && headingMatch[1] !== undefined) {
    const level = headingMatch[1].length;
    const Tag = `h${level}` as keyof JSX.IntrinsicElements;
    const headingPrefixText = headingMatch[2] ?? "";
    return (
      <Tag>
        {headingPrefixText}
        <WordChips w={w} anchor={anchor} />
        {w.suffix}
      </Tag>
    );
  }

  const listMatch = LIST_RE.exec(w.prefix);
  if (listMatch) {
    const indent = listMatch[1] ?? "";
    const listPrefixText = listMatch[3] ?? "";
    return (
      <ul>
        <li>
          {indent}
          {listPrefixText}
          <WordChips w={w} anchor={anchor} />
          {w.suffix}
        </li>
      </ul>
    );
  }

  const orderedListMatch = ORDERED_LIST_RE.exec(w.prefix);
  if (orderedListMatch) {
    const indent = orderedListMatch[1] ?? "";
    const listPrefixText = orderedListMatch[3] ?? "";
    return (
      <ol>
        <li>
          {indent}
          {listPrefixText}
          <WordChips w={w} anchor={anchor} />
          {w.suffix}
        </li>
      </ol>
    );
  }

  const blockquoteMatch = BLOCKQUOTE_RE.exec(w.prefix);
  if (blockquoteMatch) {
    const quotePrefixText = blockquoteMatch[2] ?? "";
    return (
      <blockquote>
        <p>
          {quotePrefixText}
          <WordChips w={w} anchor={anchor} />
          {w.suffix}
        </p>
      </blockquote>
    );
  }

  return (
    <p>
      {w.prefix}
      <WordChips w={w} anchor={anchor} />
      {w.suffix}
    </p>
  );
}

export function DiffHunk({ entries }: { entries: AnnotatedEntry[] }) {
  return (
    <section className={styles.hunk}>
      {entries.map(({ entry, changeIndex }, idx) => {
        const anchor = changeIndex ?? undefined;
        if (entry.kind === "word") {
          // Single-line edits render inline (no full-line band) so only the
          // changed words are highlighted, not the whole line.
          return (
            <div key={idx} className={`${styles.wordLine} markdown`}>
              <WordLine w={entry.line.word_diff} anchor={anchor} />
            </div>
          );
        }
        if (entry.kind === "context") {
          return (
            <div key={idx} className={`${styles.context} markdown`}>
              <ReactMarkdown remarkPlugins={[remarkGfm, remarkBareSpaceLinks]}>
                {entry.text}
              </ReactMarkdown>
            </div>
          );
        }
        const cls = entry.kind === "add" ? styles.add : styles.remove;
        return (
          <div key={idx} className={cls} data-change-index={anchor}>
            <div className={`${styles.blockContent} markdown`}>
              <ReactMarkdown remarkPlugins={[remarkGfm, remarkBareSpaceLinks]}>
                {entry.text}
              </ReactMarkdown>
            </div>
          </div>
        );
      })}
    </section>
  );
}
