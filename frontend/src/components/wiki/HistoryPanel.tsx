import { SelectCard, Text } from "@onyx-ai/opal/components";
import { SvgClaude, SvgOnyxLogo, SvgOpenai } from "@onyx-ai/opal/logos";
import type { IconProps } from "@onyx-ai/opal/types";
import type { ComponentType } from "react";

import { color } from "@/lib/theme";
import { relativeTime } from "@/lib/time";
import {
  type CommitAgent,
  type CommitInfo,
  parseCommitAuthor,
  parseCommitSource,
} from "@/lib/wiki";

export interface HistoryPanelProps {
  commits: CommitInfo[] | null;
  error: string | null;
  headSha: string | null;
  viewingSha: string | null;
  onPick: (sha: string) => void;
  onClose: () => void;
  /** When true (mobile sheet mode), fill the entire host container
   *  edge-to-edge instead of rendering as a fixed-width rounded card. */
  fullHeight?: boolean;
}

const AGENT_LOGO: Record<
  Exclude<CommitAgent, null>,
  ComponentType<IconProps>
> = {
  "claude-code": SvgClaude,
  codex: SvgOpenai,
  onyx: SvgOnyxLogo,
};

/**
 * Activity / version history side panel for a wiki page. Mirrors the
 * Onyx Wiki history-feed mock: each entry shows who edited, an
 * avatar + agent-logo stack, a relative timestamp, and an action line
 * ("Claude Code updated 45 lines") with `+added -removed` stats and a
 * jump-to-source link. The working tree is pinned at the top as the
 * current version.
 */
export function HistoryPanel({
  commits,
  error,
  headSha,
  viewingSha,
  onPick,
  onClose,
  fullHeight = false,
}: HistoryPanelProps) {
  const latestActive = viewingSha === null;
  return (
    <aside
      style={{
        width: fullHeight ? "100%" : 400,
        height: fullHeight ? "100%" : undefined,
        flexShrink: 0,
        background: color.bg.panel,
        borderRadius: fullHeight ? 0 : 12,
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
        padding: 8,
        gap: 8,
      }}
    >
      <div
        style={{
          display: "flex",
          flexDirection: "row",
          alignItems: "center",
          gap: 4,
          padding: 4,
          flexShrink: 0,
        }}
      >
        <div style={{ flex: 1, minWidth: 0 }}>
          <Text font="main-ui-action" color="text-04">
            History
          </Text>
        </div>
        <button
          onClick={onClose}
          aria-label="Close history"
          style={{
            appearance: "none",
            background: "transparent",
            border: "none",
            color: color.text.muted,
            cursor: "pointer",
            fontSize: 18,
            lineHeight: 1,
            padding: 4,
            borderRadius: 4,
            flexShrink: 0,
          }}
        >
          ×
        </button>
      </div>
      <div
        style={{
          overflowY: "auto",
          flex: 1,
          display: "flex",
          flexDirection: "column",
          gap: 4,
        }}
      >
        {error && <PanelMessage>{error}</PanelMessage>}
        {!error && commits === null && <PanelMessage>Loading…</PanelMessage>}
        {!error && commits && commits.length === 0 && (
          <PanelMessage>No history yet.</PanelMessage>
        )}
        {!error && commits && commits.length > 0 && (
          <>
            {commits.map((c) => {
              const isHead = c.sha === headSha;
              return (
                <CommitRow
                  key={c.sha}
                  commit={c}
                  active={viewingSha === c.sha || (latestActive && isHead)}
                  onClick={() => onPick(c.sha)}
                />
              );
            })}
          </>
        )}
      </div>
    </aside>
  );
}

function PanelMessage({ children }: { children: string }) {
  return (
    <div style={{ padding: 12 }}>
      <Text font="secondary-body" color="text-03">
        {children}
      </Text>
    </div>
  );
}

function CommitRow({
  commit,
  active,
  onClick,
}: {
  commit: CommitInfo;
  active: boolean;
  onClick: () => void;
}) {
  const { person, agent, agentLabel } = parseCommitAuthor(commit.author);
  const { url, title: srcTitle } = parseCommitSource(commit.body);
  const changed = commit.added + commit.removed;
  const action = agentLabel
    ? `${agentLabel} updated ${changed} lines`
    : `Updated ${changed} lines`;
  const AgentLogo = agent ? AGENT_LOGO[agent] : null;
  return (
    <SelectCard
      state={active ? "selected" : "empty"}
      onClick={onClick}
      padding="xs"
      rounding="md"
      border="none"
    >
      <Row>
        <HeaderLine
          avatars={
            <>
              <Avatar initial={person.charAt(0).toUpperCase()} />
              {AgentLogo ? <LogoAvatar Logo={AgentLogo} /> : null}
            </>
          }
          title={person}
          right={
            <Text font="secondary-body" color="text-03" nowrap>
              {relativeTime(commit.ts, "long")}
            </Text>
          }
        />
        <ActionLine
          label={action}
          stats={
            changed > 0
              ? { added: commit.added, removed: commit.removed }
              : null
          }
          sourceUrl={url}
          sourceTitle={srcTitle}
        />
        {commit.triggered > 0 ? (
          <ActionLine
            label={`⚡ Triggered ${commit.triggered} automation${
              commit.triggered === 1 ? "" : "s"
            }`}
          />
        ) : null}
      </Row>
    </SelectCard>
  );
}

function Row({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 4,
        width: "100%",
        minWidth: 0,
      }}
    >
      {children}
    </div>
  );
}

function HeaderLine({
  avatars,
  title,
  right,
}: {
  avatars: React.ReactNode;
  title: string;
  right: React.ReactNode;
}) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "row",
        alignItems: "center",
        gap: 8,
        width: "100%",
        minWidth: 0,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", flexShrink: 0 }}>
        {avatars}
      </div>
      <div
        style={{
          flex: 1,
          minWidth: 0,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        <Text font="main-ui-action" color="text-04" nowrap maxLines={1}>
          {title}
        </Text>
      </div>
      <div style={{ flexShrink: 0 }}>{right}</div>
    </div>
  );
}

function ActionLine({
  label,
  stats,
  sourceUrl,
  sourceTitle,
}: {
  label: string;
  stats?: { added: number; removed: number } | null;
  sourceUrl?: string | null;
  sourceTitle?: string | null;
}) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "row",
        alignItems: "center",
        gap: 8,
        width: "100%",
        minWidth: 0,
        paddingLeft: 28,
      }}
    >
      <div
        style={{
          flex: 1,
          minWidth: 0,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        <Text font="secondary-body" color="text-03" nowrap maxLines={1}>
          {label}
        </Text>
      </div>
      {stats ? (
        <div style={{ display: "flex", gap: 4, flexShrink: 0 }}>
          <Text font="secondary-mono" color="text-03" nowrap>
            {`+${stats.added}`}
          </Text>
          <Text font="secondary-mono" color="text-03" nowrap>
            {`-${stats.removed}`}
          </Text>
        </div>
      ) : null}
      {sourceUrl ? (
        <a
          href={sourceUrl}
          target="_blank"
          rel="noopener noreferrer"
          onClick={(e) => e.stopPropagation()}
          aria-label={sourceTitle ?? "Open source"}
          style={{
            display: "inline-flex",
            alignItems: "center",
            color: "inherit",
            textDecoration: "none",
            flexShrink: 0,
          }}
        >
          <Text font="secondary-body" color="text-03">
            ↗
          </Text>
        </a>
      ) : null}
    </div>
  );
}

// Shared circle geometry so the person initial and the agent logo chip
// render at identical size. box-sizing: border-box keeps the 1px border
// inside the 20px box (otherwise content-box would inflate one of them).
const AVATAR_SIZE = 20;
const avatarBase = {
  boxSizing: "border-box" as const,
  width: AVATAR_SIZE,
  height: AVATAR_SIZE,
  borderRadius: 9999,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  flexShrink: 0,
  overflow: "hidden" as const,
};

/** Single-initial avatar. Inverts per theme via the --diff-avatar-*
 *  CSS vars defined in globals.css. */
function Avatar({ initial }: { initial: string }) {
  return (
    <div
      aria-hidden
      style={{
        ...avatarBase,
        background: "var(--diff-avatar-bg)",
        color: "var(--diff-avatar-fg)",
        border: "1px solid var(--diff-avatar-border)",
        fontSize: 12,
        fontWeight: 600,
      }}
    >
      {initial}
    </div>
  );
}

/** Agent logo chip in the avatar stack — same circle as Avatar,
 *  overlapping it slightly per the Figma stacked-avatar treatment. */
function LogoAvatar({ Logo }: { Logo: ComponentType<IconProps> }) {
  return (
    <div
      style={{
        ...avatarBase,
        marginLeft: -6,
        background: color.bg.page,
        border: `1px solid ${color.border.subtle}`,
      }}
    >
      <Logo style={{ width: 12, height: 12 }} />
    </div>
  );
}
