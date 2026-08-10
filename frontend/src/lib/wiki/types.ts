/** Full flat listing of every wiki doc — the `/wiki` response, used to derive
 * folder trees and directory listings. */
export interface DocEntry {
  path: string;
  updated_at: string;
}

export interface ListResponse {
  entries: DocEntry[];
}

/** Shape of a home "Recent Pages" card; the grid fetches `/wiki/recent` directly. */
export interface RecentPage {
  path: string;
  title: string;
  updated_at: string;
  preview: string;
  id?: string | null;
}

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

export interface CommitInfo {
  sha: string;
  author: string;
  ts: string;
  message: string;
  body?: string;
  added: number;
  removed: number;
  triggered: number;
}

export type CommitAgent = "claude-code" | "codex" | "onyx" | null;

export interface CommitAuthor {
  /** Human who owns the edit, e.g. "Nik". */
  person: string;
  /** Coding-agent that produced it, or null for a direct human edit. */
  agent: CommitAgent;
  /** Display label for the agent, e.g. "Claude Code". Empty when none. */
  agentLabel: string;
}

export interface FileHistoryResponse {
  path: string;
  head_sha: string | null;
  commits: CommitInfo[];
}

export interface UpdateHealth {
  path: string;
  count_24h: number;
  threshold_24h: number;
  cap_24h: number;
  auto_update_disabled: boolean;
  can_manage: boolean;
  // When over the cap, ISO-8601/UTC time auto-update resumes; null otherwise.
  cap_resets_at: string | null;
}
