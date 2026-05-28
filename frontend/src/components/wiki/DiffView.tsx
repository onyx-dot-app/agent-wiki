import { SelectButton, Text } from "@onyx-ai/opal/components";
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
          <Text
            font="main-ui-action"
            color="text-04"
            as="p"
            nowrap
            maxLines={1}
          >
            {commit?.message || "(no message)"}
          </Text>
          <Text
            font="secondary-body"
            color="text-03"
            as="p"
            nowrap
            maxLines={1}
          >
            {`${data.sha.slice(0, 7)} · ${commit?.author ?? "?"} · ${
              commit?.ts ?? ""
            }`}
          </Text>
        </div>
        <div role="tablist" aria-label="View mode" className={styles.toggle}>
          <SelectButton
            size="sm"
            state={mode === "diff" ? "selected" : "empty"}
            onClick={() => pickMode("diff")}
          >
            Diff
          </SelectButton>
          <SelectButton
            size="sm"
            state={mode === "doc" ? "selected" : "empty"}
            onClick={() => void pickMode("doc")}
          >
            Doc
          </SelectButton>
        </div>
      </div>
      <div className={styles.body}>
        {mode === "diff" ? (
          data.hunks.length === 0 ? (
            <div className={styles.empty}>
              <Text font="secondary-body" color="text-03">
                No changes for this file in that commit.
              </Text>
            </div>
          ) : (
            data.hunks.map((hunk, idx) => <DiffHunk key={idx} hunk={hunk} />)
          )
        ) : loading ? (
          <div className={styles.empty}>
            <Text font="secondary-body" color="text-03">
              Loading…
            </Text>
          </div>
        ) : bodyError ? (
          <div className={styles.empty}>
            <Text font="secondary-body" color="text-03">
              {bodyError}
            </Text>
          </div>
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
