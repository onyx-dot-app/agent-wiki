import type { FileDiffResponse } from "@/lib/wiki";

import { DiffHunk } from "./DiffHunk";
import styles from "./DiffView.module.css";

interface CommitMeta {
  sha: string;
  message: string;
  author: string;
  ts: string;
}

export function DiffView({
  data,
  commit,
}: {
  data: FileDiffResponse;
  commit: CommitMeta | undefined;
}) {
  return (
    <div className={styles.view}>
      <div className={styles.header}>
        <div className={styles.title}>{commit?.message || "(no message)"}</div>
        <div className={styles.meta}>
          {data.sha.slice(0, 7)} · {commit?.author ?? "?"} · {commit?.ts ?? ""}
        </div>
      </div>
      <div className={styles.body}>
        {data.hunks.length === 0 ? (
          <div className={styles.empty}>
            No changes for this file in that commit.
          </div>
        ) : (
          data.hunks.map((hunk, idx) => <DiffHunk key={idx} hunk={hunk} />)
        )}
      </div>
    </div>
  );
}
