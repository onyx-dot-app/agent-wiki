import type { DiffLine } from "@/lib/wiki";

import styles from "./DiffLineRow.module.css";

export function DiffLineRow({ line }: { line: DiffLine }) {
  if (line.kind === "word") {
    const w = line.word_diff;
    return (
      <div className={`${styles.row} ${styles.context}`}>
        <span className={styles.gutterSign} />
        <span className={styles.gutterLineno}>{line.old_lineno ?? ""}</span>
        <span className={styles.gutterLineno}>{line.new_lineno ?? ""}</span>
        <span className={styles.content}>
          {w?.prefix}
          {w?.removed ? (
            <del className={styles.wordRemoved}>{w.removed}</del>
          ) : null}
          {w?.added ? <ins className={styles.wordAdded}>{w.added}</ins> : null}
          {w?.suffix}
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

  return (
    <div className={`${styles.row} ${rowClass}`}>
      <span className={styles.gutterSign}>{sign}</span>
      <span className={styles.gutterLineno}>{line.old_lineno ?? ""}</span>
      <span className={styles.gutterLineno}>{line.new_lineno ?? ""}</span>
      <span className={styles.content}>{line.text ?? ""}</span>
    </div>
  );
}
