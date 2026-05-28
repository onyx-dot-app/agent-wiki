import type { JSX } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { DiffHunk as DiffHunkData, DiffLine, WordDiff } from "@/lib/wiki";

import styles from "./DiffHunk.module.css";

type BlockKind = "context" | "add" | "remove";

interface BlockEntry {
  kind: BlockKind;
  text: string;
}

interface WordEntry {
  kind: "word";
  line: DiffLine;
}

type Entry = BlockEntry | WordEntry;

function groupEntries(lines: DiffLine[]): Entry[] {
  const out: Entry[] = [];
  let current: BlockEntry | null = null;
  const flush = () => {
    if (current) {
      out.push(current);
      current = null;
    }
  };
  for (const line of lines) {
    if (line.kind === "word") {
      flush();
      out.push({ kind: "word", line });
      continue;
    }
    const text = line.text ?? "";
    if (current && current.kind === line.kind) {
      current.text += "\n" + text;
    } else {
      flush();
      current = { kind: line.kind, text };
    }
  }
  flush();
  return out;
}

const HEADING_RE = /^(#{1,6})\s+(.*)$/;
const LIST_RE = /^(\s*)([-*+])\s+(.*)$/;
const ORDERED_LIST_RE = /^(\s*)(\d+\.)\s+(.*)$/;
const BLOCKQUOTE_RE = /^(>+)\s*(.*)$/;

function WordChips({ w }: { w: WordDiff | null }) {
  if (!w) return null;
  return (
    <>
      {w.removed ? <del className={styles.wordRemoved}>{w.removed}</del> : null}
      {w.added ? <ins className={styles.wordAdded}>{w.added}</ins> : null}
    </>
  );
}

function WordLine({ w }: { w: WordDiff | null }) {
  if (!w) return null;

  const headingMatch = HEADING_RE.exec(w.prefix);
  if (headingMatch && headingMatch[1] !== undefined) {
    const level = headingMatch[1].length;
    const Tag = `h${level}` as keyof JSX.IntrinsicElements;
    const headingPrefixText = headingMatch[2] ?? "";
    return (
      <Tag>
        {headingPrefixText}
        <WordChips w={w} />
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
          <WordChips w={w} />
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
          <WordChips w={w} />
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
          <WordChips w={w} />
          {w.suffix}
        </p>
      </blockquote>
    );
  }

  return (
    <p>
      {w.prefix}
      <WordChips w={w} />
      {w.suffix}
    </p>
  );
}

export function DiffHunk({ hunk }: { hunk: DiffHunkData }) {
  const entries = groupEntries(hunk.lines);
  return (
    <section className={styles.hunk}>
      {entries.map((entry, idx) => {
        if (entry.kind === "word") {
          return (
            <div key={idx} className={styles.add}>
              <div className={`${styles.blockContent} markdown`}>
                <WordLine w={entry.line.word_diff} />
              </div>
            </div>
          );
        }
        if (entry.kind === "context") {
          return (
            <div key={idx} className={`${styles.context} markdown`}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {entry.text}
              </ReactMarkdown>
            </div>
          );
        }
        const cls = entry.kind === "add" ? styles.add : styles.remove;
        return (
          <div key={idx} className={cls}>
            <div className={`${styles.blockContent} markdown`}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {entry.text}
              </ReactMarkdown>
            </div>
          </div>
        );
      })}
    </section>
  );
}
