import type { CommitAgent, CommitAuthor, UpdateHealth } from "@/lib/wiki/types";

export type UpdateWarnLevel = "over" | "near" | null;

/** The page's auto-update warning level: "over" once the 24h cap is hit,
 * "near" once activity reaches the alert threshold. Single predicate so
 * every surface rendering update-health chrome agrees. */
export function updateWarnLevel(
  health: UpdateHealth | null | undefined,
): UpdateWarnLevel {
  if (!health) return null;
  if (health.cap_24h > 0 && health.count_24h >= health.cap_24h) return "over";
  if (health.count_24h > 0 && health.count_24h >= health.threshold_24h)
    return "near";
  return null;
}

/** Last path segment as a display name — drops a trailing `.md`. Shared by the
 * share + transfer dialogs so the two copies don't drift. See also
 * {@link pageTitle}, which covers the FileView title case (a `.md` file path,
 * always non-empty, no trailing-slash/root handling needed). */
export function lastSegment(path: string): string {
  const clean = path.replace(/\/+$/, "");
  if (!clean) return "Wiki";
  const seg = clean.split("/").pop() ?? clean;
  return seg.endsWith(".md") ? seg.slice(0, -3) : seg;
}

/** Strip the directory prefix and `.md` extension from a wiki file path to
 * get the human-readable page title. See also {@link lastSegment} for the
 * generic (possibly folder/root) path case. */
export function pageTitle(path: string): string {
  return (path.split("/").pop() ?? path).replace(/\.md$/i, "");
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
