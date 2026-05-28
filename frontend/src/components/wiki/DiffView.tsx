import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { FileDiffResponse } from "@/lib/wiki";

import { DiffHunk } from "./DiffHunk";
import styles from "./DiffView.module.css";

interface CommitMeta {
  sha: string;
  message: string;
  author: string;
  ts: string;
}

type Mode = "diff" | "doc";

export function DiffView({
  data,
  commit,
  loadBody,
}: {
  data: FileDiffResponse;
  commit: CommitMeta | undefined;
  loadBody: () => Promise<string>;
}) {
  const [mode, setMode] = useState<Mode>("diff");
  const [body, setBody] = useState<string | null>(null);
  const [bodyError, setBodyError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function pickMode(next: Mode) {
    if (next === mode) return;
    setMode(next);
    if (next === "doc" && body === null) {
      setLoading(true);
      setBodyError(null);
      try {
        const text = await loadBody();
        setBody(text);
      } catch (e) {
        setBodyError(e instanceof Error ? e.message : "failed to load doc");
      } finally {
        setLoading(false);
      }
    }
  }

  return (
    <div className={styles.view}>
      <div className={styles.header}>
        <div className={styles.headerInfo}>
          <div className={styles.title}>
            {commit?.message || "(no message)"}
          </div>
          <div className={styles.meta}>
            {data.sha.slice(0, 7)} · {commit?.author ?? "?"} ·{" "}
            {commit?.ts ?? ""}
          </div>
        </div>
        <div className={styles.toggle} role="tablist" aria-label="View mode">
          <button
            type="button"
            role="tab"
            aria-selected={mode === "diff"}
            className={`${styles.toggleBtn} ${
              mode === "diff" ? styles.toggleBtnActive : ""
            }`}
            onClick={() => pickMode("diff")}
          >
            Diff
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === "doc"}
            className={`${styles.toggleBtn} ${
              mode === "doc" ? styles.toggleBtnActive : ""
            }`}
            onClick={() => void pickMode("doc")}
          >
            Doc
          </button>
        </div>
      </div>
      <div className={styles.body}>
        {mode === "diff" ? (
          data.hunks.length === 0 ? (
            <div className={styles.empty}>
              No changes for this file in that commit.
            </div>
          ) : (
            data.hunks.map((hunk, idx) => <DiffHunk key={idx} hunk={hunk} />)
          )
        ) : loading ? (
          <div className={styles.empty}>Loading…</div>
        ) : bodyError ? (
          <div className={styles.empty}>{bodyError}</div>
        ) : (
          <article className="markdown">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {body ?? ""}
            </ReactMarkdown>
          </article>
        )}
      </div>
    </div>
  );
}
