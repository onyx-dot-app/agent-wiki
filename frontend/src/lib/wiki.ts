import { apiFetch } from "@/lib/api";

export interface WordDiff {
  prefix: string;
  removed: string;
  added: string;
  suffix: string;
}

export interface DiffLine {
  kind: "context" | "add" | "remove" | "word";
  text: string | null;
  word_diff: WordDiff | null;
  old_lineno: number | null;
  new_lineno: number | null;
}

export interface DiffHunk {
  old_start: number;
  old_count: number;
  new_start: number;
  new_count: number;
  lines: DiffLine[];
}

export interface FileDiffResponse {
  path: string;
  sha: string;
  parent_sha: string | null;
  hunks: DiffHunk[];
  is_creation: boolean;
}

export async function fetchFileDiff(
  path: string,
  sha: string,
): Promise<FileDiffResponse> {
  return apiFetch<FileDiffResponse>(
    `/wiki/file/diff?path=${encodeURIComponent(path)}&sha=${encodeURIComponent(
      sha,
    )}`,
  );
}

export interface CommitInfo {
  sha: string;
  author: string;
  ts: string;
  message: string;
  body?: string;
}

export interface FileHistoryResponse {
  path: string;
  head_sha: string | null;
  commits: CommitInfo[];
}

export async function fetchFileHistory(
  path: string,
): Promise<FileHistoryResponse> {
  return apiFetch<FileHistoryResponse>(
    `/wiki/file/history?path=${encodeURIComponent(path)}`,
  );
}

/**
 * Pull the originating source URL + title out of a commit body.
 *
 * Launcher-driven commits (Claude Code, Codex, …) stamp `Source:` and
 * `Title:` trailers into the commit body so the history sidebar can link
 * back to the page/PR that drove the edit. Returns nulls when absent.
 */
export function parseCommitSource(body?: string): {
  url: string | null;
  title: string | null;
} {
  let url: string | null = null;
  let title: string | null = null;
  for (const line of (body ?? "").split("\n")) {
    if (!url) {
      const m = line.match(/^Source:\s*(\S+)/);
      if (m) url = /^https?:\/\//i.test(m[1]) ? m[1] : null;
    }
    if (!title) {
      const m = line.match(/^Title:\s*(.+)/);
      if (m) title = m[1].trim();
    }
  }
  return { url, title };
}
