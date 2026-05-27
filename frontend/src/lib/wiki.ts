import { apiFetch } from "@/lib/api";

export interface WordDiff {
  prefix: string;
  removed: string;
  added: string;
  suffix: string;
}

export interface DiffLine {
  kind: "context" | "add" | "remove" | "word";
  text?: string;
  word_diff?: WordDiff;
  old_lineno?: number;
  new_lineno?: number;
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
