import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { DiffHunk as DiffHunkData, DiffLine } from "@/lib/wiki";

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

export function DiffHunk({ hunk }: { hunk: DiffHunkData }) {
  const entries = groupEntries(hunk.lines);
  return (
    <section className={styles.hunk}>
      {entries.map((entry, idx) => {
        if (entry.kind === "word") {
          const w = entry.line.word_diff;
          return (
            <div key={idx} className={`${styles.wordLine} markdown`}>
              <p>
                {w?.prefix ?? ""}
                {w?.removed ? (
                  <del className={styles.wordRemoved}>{w.removed}</del>
                ) : null}
                {w?.added ? (
                  <ins className={styles.wordAdded}>{w.added}</ins>
                ) : null}
                {w?.suffix ?? ""}
              </p>
            </div>
          );
        }
        const cls =
          entry.kind === "add"
            ? styles.add
            : entry.kind === "remove"
              ? styles.remove
              : styles.context;
        return (
          <div key={idx} className={`${cls} markdown`}>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {entry.text}
            </ReactMarkdown>
          </div>
        );
      })}
    </section>
  );
}
