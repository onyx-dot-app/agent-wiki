import type { DiffHunk as DiffHunkData } from "@/lib/wiki";

import { DiffLineRow } from "./DiffLineRow";
import styles from "./DiffHunk.module.css";

export function DiffHunk({ hunk }: { hunk: DiffHunkData }) {
  const header = `@@ -${hunk.old_start},${hunk.old_count} +${hunk.new_start},${hunk.new_count} @@`;
  return (
    <section className={styles.hunk}>
      <div className={styles.header}>{header}</div>
      {hunk.lines.map((line, idx) => (
        <DiffLineRow key={idx} line={line} />
      ))}
    </section>
  );
}
