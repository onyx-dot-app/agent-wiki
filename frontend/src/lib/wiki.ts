import { apiFetch } from "@/lib/api";

/** Last path segment as a display name — drops a trailing `.md`. Shared by the
 * share + transfer dialogs so the two copies don't drift. */
export function lastSegment(path: string): string {
  const clean = path.replace(/\/+$/, "");
  if (!clean) return "Wiki";
  const seg = clean.split("/").pop() ?? clean;
  return seg.endsWith(".md") ? seg.slice(0, -3) : seg;
}

/** Shape of a home "Recent Pages" card; the grid fetches `/wiki/recent` directly. */
export interface RecentPage {
  path: string;
  title: string;
  updated_at: string;
  preview: string;
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

const AGENT_LABELS: Record<Exclude<CommitAgent, null>, string> = {
  "claude-code": "Claude Code",
  codex: "Codex",
  onyx: "Onyx Craft",
};

/**
 * Split a git author string into the human + the coding agent that drove
 * the edit. Launcher commits author as "Nik via launcher-claude-code";
 * direct edits are just "Nik".
 */
export function parseCommitAuthor(author: string): CommitAuthor {
  const m = author.match(/^(.*?)\s+via\s+(.+)$/i);
  if (!m)
    return { person: author.trim() || "Unknown", agent: null, agentLabel: "" };
  const person = m[1].trim() || "Unknown";
  const raw = m[2]
    .trim()
    .toLowerCase()
    .replace(/^launcher-/, "");
  let agent: CommitAgent = null;
  if (raw.includes("claude")) agent = "claude-code";
  else if (raw.includes("codex") || raw.includes("openai")) agent = "codex";
  else if (raw.includes("onyx") || raw.includes("craft")) agent = "onyx";
  return {
    person,
    agent,
    agentLabel: agent ? AGENT_LABELS[agent] : "",
  };
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
