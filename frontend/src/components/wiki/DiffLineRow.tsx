import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { DiffLine } from "@/lib/wiki";

import styles from "./DiffLineRow.module.css";

const MD_COMPONENTS = {
  p: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
};

export function DiffLineRow({ line }: { line: DiffLine }) {
  if (line.kind === "word") {
    const w = line.word_diff;
    return (
      <div className={`${styles.row} ${styles.context}`}>
        <span className={styles.gutterSign} />
        <span className={styles.gutterLineno}>{line.old_lineno ?? ""}</span>
        <span className={styles.gutterLineno}>{line.new_lineno ?? ""}</span>
        <span className={styles.content}>
          {w?.prefix ? (
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={MD_COMPONENTS}
            >
              {w.prefix}
            </ReactMarkdown>
          ) : null}
          {w?.removed ? (
            <del className={styles.wordRemoved}>{w.removed}</del>
          ) : null}
          {w?.added ? <ins className={styles.wordAdded}>{w.added}</ins> : null}
          {w?.suffix ? (
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={MD_COMPONENTS}
            >
              {w.suffix}
            </ReactMarkdown>
          ) : null}
        </span>
      </div>
    );
  }

  const sign = line.kind === "add" ? "+" : line.kind === "remove" ? "-" : "";
  const rowClass =
    line.kind === "add"
      ? styles.add
      : line.kind === "remove"
        ? styles.remove
        : styles.context;
  const text = line.text ?? "";

  return (
    <div className={`${styles.row} ${rowClass}`}>
      <span className={styles.gutterSign}>{sign}</span>
      <span className={styles.gutterLineno}>{line.old_lineno ?? ""}</span>
      <span className={styles.gutterLineno}>{line.new_lineno ?? ""}</span>
      <span className={styles.content}>
        {text ? (
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>
            {text}
          </ReactMarkdown>
        ) : null}
      </span>
    </div>
  );
}
