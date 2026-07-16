import { Button, SelectCard, Text } from "@onyx-ai/opal/components";
import { SvgWorkflow, SvgX } from "@onyx-ai/opal/icons";
import { SvgClaude, SvgOnyxLogo, SvgOpenai } from "@onyx-ai/opal/logos";
import type { IconProps } from "@onyx-ai/opal/types";
import type { ComponentType } from "react";

import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { relativeTime } from "@/lib/time";
import type { CommitAgent, CommitInfo } from "@/lib/wiki/types";
import { parseCommitAuthor, parseCommitSource } from "@/lib/wiki/utils";

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
        borderRadius: fullHeight ? 0 : 12,
      }}
      className="flex min-h-0 shrink-0 flex-col gap-2 bg-(--background-tint-01) p-2"
    >
      <div className="flex shrink-0 flex-row items-center gap-1 p-1">
        <div className="min-w-0 flex-1">
          <Text font="main-ui-action" color="text-04">
            History
          </Text>
        </div>
        <Button
          icon={SvgX}
          prominence="tertiary"
          size="sm"
          tooltip="Close history"
          onClick={onClose}
        />
      </div>
      <div className="flex flex-1 flex-col gap-1 overflow-y-auto">
        {error && <PanelMessage>{error}</PanelMessage>}
        {!error && commits === null && (
          <div className="p-3">
            <LoadingSpinner />
          </div>
        )}
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
    <div className="p-3">
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
          <TriggeredLine count={commit.triggered} />
        ) : null}
      </Row>
    </SelectCard>
  );
}

function Row({ children }: { children: React.ReactNode }) {
  return <div className="flex w-full min-w-0 flex-col gap-2">{children}</div>;
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
    <div className="flex w-full min-w-0 flex-row items-center gap-2">
      <div className="flex shrink-0 items-center">{avatars}</div>
      <div className="min-w-0 flex-1 overflow-hidden text-ellipsis whitespace-nowrap">
        <Text font="main-ui-action" color="text-04" nowrap maxLines={1}>
          {title}
        </Text>
      </div>
      <div className="shrink-0">{right}</div>
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
    <div className="flex w-full min-w-0 flex-row items-center gap-2 text-xs leading-4">
      <div className="min-w-0 flex-1 overflow-hidden text-ellipsis whitespace-nowrap">
        <Text font="secondary-body" color="text-03" nowrap maxLines={1}>
          {label}
        </Text>
      </div>
      {stats ? (
        <div className="flex shrink-0 gap-1">
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
          className="inline-flex shrink-0 items-center text-inherit no-underline"
        >
          <Text font="secondary-body" color="text-03">
            ↗
          </Text>
        </a>
      ) : null}
    </div>
  );
}

function TriggeredLine({ count }: { count: number }) {
  return (
    <div className="flex w-full min-w-0 flex-row items-center gap-1 text-xs leading-4">
      <Text font="secondary-body" color="text-03" nowrap>
        Triggered
      </Text>
      <span className="inline-flex shrink-0 items-center text-(--text-03)">
        <SvgWorkflow style={{ width: 12, height: 12 }} />
      </span>
      <span className="font-semibold">
        <Text font="secondary-action" color="text-03" nowrap>
          {`${count} automation${count === 1 ? "" : "s"}`}
        </Text>
      </span>
    </div>
  );
}

// Shared circle geometry so the person initial and the agent logo chip
// render at identical size. box-sizing: border-box keeps the 1px border
// inside the 20px box (otherwise content-box would inflate one of them).
const AVATAR_SIZE = 20;

/** Single-initial avatar. Inverts per theme via Opal's neutral-inverted
 *  background + inverted text tokens. */
function Avatar({ initial }: { initial: string }) {
  return (
    <div
      aria-hidden
      style={{
        width: AVATAR_SIZE,
        height: AVATAR_SIZE,
      }}
      className="box-border flex shrink-0 items-center justify-center overflow-hidden rounded-full border border-(--border-01) bg-(--background-neutral-inverted-00) text-xs font-semibold text-(--text-inverted-05)"
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
        width: AVATAR_SIZE,
        height: AVATAR_SIZE,
      }}
      className="-ml-[6px] box-border flex shrink-0 items-center justify-center overflow-hidden rounded-full border border-(--border-01) bg-(--background-tint-00)"
    >
      <Logo style={{ width: 16, height: 16 }} />
    </div>
  );
}
