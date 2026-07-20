import {
  Button,
  Divider,
  EndOfList,
  InputTypeIn,
  Text,
} from "@onyx-ai/opal/components";
import { SvgArrowUpRight, SvgHistory } from "@onyx-ai/opal/icons";
import { SvgClaude, SvgOnyxLogo, SvgOpenai } from "@onyx-ai/opal/logos";
import type { IconProps } from "@onyx-ai/opal/types";
import { useMemo, useState, type ComponentType } from "react";

import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { relativeTime } from "@/lib/time";
import type { CommitAgent, CommitInfo } from "@/lib/wiki/types";
import { parseCommitAuthor, parseCommitSource } from "@/lib/wiki/utils";

const AGENT_LOGO: Record<
  Exclude<CommitAgent, null>,
  ComponentType<IconProps>
> = {
  "claude-code": SvgClaude,
  codex: SvgOpenai,
  onyx: SvgOnyxLogo,
};

// Rows at least this old fall below the "Older" section divider.
const OLDER_MS = 24 * 60 * 60 * 1000;

export interface VersionHistoryListProps {
  commits: CommitInfo[] | null;
  error: string | null;
  /** Newest commit for the file. Its row gets the blue "Current" marker. */
  headSha: string | null;
  /** Version being viewed, highlighted as a raised card. */
  viewingSha: string | null;
  /** Current user's display name, for the "(you)" suffix on their rows. */
  selfName: string | null;
  onPick: (sha: string) => void;
}

/**
 * Searchable version list for a wiki page, the shared body of the Updates
 * tab's expanded Update History card and the version-mode rail. Mock rows
 * 1912:355501 (Current) / 1912:355463 (selected): avatar pair, name,
 * Current/relative-time marker, action line with +added -removed stats.
 */
export function VersionHistoryList({
  commits,
  error,
  headSha,
  viewingSha,
  selfName,
  onPick,
}: VersionHistoryListProps) {
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    if (!commits) return null;
    const q = query.trim().toLowerCase();
    if (!q) return commits;
    return commits.filter((c) => {
      const { person, agentLabel } = parseCommitAuthor(c.author);
      return (
        person.toLowerCase().includes(q) ||
        agentLabel.toLowerCase().includes(q) ||
        c.message.toLowerCase().includes(q)
      );
    });
  }, [commits, query]);

  // Recent above, older-than-24h below an "Older" divider (mock 1912:357384).
  const { recent, older } = useMemo(() => {
    const cutoff = Date.now() - OLDER_MS;
    const recent: CommitInfo[] = [];
    const older: CommitInfo[] = [];
    for (const c of filtered ?? []) {
      (new Date(c.ts).getTime() >= cutoff ? recent : older).push(c);
    }
    return { recent, older };
  }, [filtered]);

  const row = (c: CommitInfo) => (
    <VersionRow
      key={c.sha}
      commit={c}
      current={c.sha === headSha}
      selected={c.sha === viewingSha}
      selfName={selfName}
      onClick={() => onPick(c.sha)}
    />
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <SearchField value={query} onChange={setQuery} />
      <div className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto">
        {error && <ListMessage>{error}</ListMessage>}
        {!error && filtered === null && (
          <div className="p-3">
            <LoadingSpinner />
          </div>
        )}
        {!error && filtered && filtered.length === 0 && (
          <ListMessage>
            {query ? "No versions match." : "No history yet."}
          </ListMessage>
        )}
        {!error && filtered && filtered.length > 0 && (
          <>
            {recent.map(row)}
            {older.length > 0 && (
              <div className="py-1">
                <Divider title="Older" />
              </div>
            )}
            {older.map(row)}
            <EndOfList
              title={`${filtered.length} Version${filtered.length === 1 ? "" : "s"}`}
            />
          </>
        )}
      </div>
    </div>
  );
}

export interface UpdateHistoryRailProps extends VersionHistoryListProps {
  /** Back to Current / Restore This Version / Exit Update History cluster,
   * rendered in the rail's own 48px header row. Mock Side Section
   * 1912:355400 keeps these out of the app header. */
  headerActions: React.ReactNode;
}

/**
 * The version-mode rail: the tab strip is replaced by a single Update
 * History surface. A header action row, then a tinted card holding the
 * title row, search, and version list (mock 1912:355447).
 */
export function UpdateHistoryRail({
  headerActions,
  ...listProps
}: UpdateHistoryRailProps) {
  return (
    <div className="flex h-full w-[360px] max-w-[100vw] flex-col">
      <div className="flex h-12 shrink-0 items-center justify-end gap-2 px-3 py-2">
        {headerActions}
      </div>
      <div className="mx-2 mb-2 flex min-h-0 flex-1 flex-col rounded-(--radius-12) bg-(--background-tint-01) p-1">
        <div className="flex shrink-0 items-center gap-1 p-2">
          <span className="flex size-5 shrink-0 items-center justify-center text-(--text-04)">
            <SvgHistory size={16} />
          </span>
          <Text font="main-ui-action" color="text-04">
            Update History
          </Text>
        </div>
        <Divider paddingParallel="fit" paddingPerpendicular="fit" />
        <VersionHistoryList {...listProps} />
      </div>
    </div>
  );
}

/** Borderless search field over the card tint (mock 1912:355461),
 *  InputTypeIn's chromeless internal variant. */
function SearchField({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    // The 14px override lives in globals.css (.version-history-search).
    <div className="version-history-search shrink-0 p-[1px]">
      <InputTypeIn
        variant="internal"
        searchIcon
        placeholder="Search update history…"
        spellCheck={false}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}

function ListMessage({ children }: { children: string }) {
  return (
    <div className="p-3">
      <Text font="secondary-body" color="text-03">
        {children}
      </Text>
    </div>
  );
}

interface VersionRowProps {
  commit: CommitInfo;
  current: boolean;
  selected: boolean;
  selfName: string | null;
  onClick: () => void;
}

function VersionRow({
  commit,
  current,
  selected,
  selfName,
  onClick,
}: VersionRowProps) {
  const { person, agent, agentLabel } = parseCommitAuthor(commit.author);
  const { url, title: srcTitle } = parseCommitSource(commit.body);
  const changed = commit.added + commit.removed;
  const lines = `${changed} line${changed === 1 ? "" : "s"}`;
  const action = agentLabel
    ? `${agentLabel} updated ${lines}`
    : `Updated ${lines}`;
  const AgentLogo = agent ? AGENT_LOGO[agent] : null;
  const name = person === selfName ? `${person} (you)` : person;
  return (
    // raw-ok: SelectCard's selected state is a flat selection tint while the mock's viewed row is a raised tint-00 card with shadow-box-01 elevation, and the row hosts a nested source link, which a native button cannot contain
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick();
        }
      }}
      className={`flex w-full cursor-pointer flex-col rounded-(--radius-08) p-1 text-left ${
        selected
          ? "bg-(--background-tint-00) shadow-(--shadow-box-01)"
          : "bg-transparent hover:bg-(--background-tint-02)"
      }`}
    >
      <div className="flex w-full items-start gap-1 p-1">
        <div className="flex shrink-0 items-center px-[2px]">
          <Avatar initial={person.charAt(0).toUpperCase()} />
          {AgentLogo ? <LogoAvatar Logo={AgentLogo} /> : null}
        </div>
        <div className="min-w-0 flex-1 overflow-hidden px-[2px] text-ellipsis whitespace-nowrap">
          <Text font="main-ui-action" color="text-04" nowrap maxLines={1}>
            {name}
          </Text>
        </div>
        <div className="flex min-h-5 shrink-0 items-center gap-1 p-[2px]">
          {current ? (
            <>
              <span className="px-[2px] text-[12px] leading-4 text-(--status-text-info-05)">
                Current
              </span>
              <span className="flex size-4 items-center justify-center">
                <span className="size-[6px] rounded-full bg-(--status-info-05)" />
              </span>
            </>
          ) : (
            <>
              <span className="px-[2px] text-[12px] leading-4 text-(--text-03)">
                {relativeTime(commit.ts, "long")}
              </span>
              <span className="flex size-4 items-center justify-center">
                <span className="size-2 rounded-full border border-(--border-02)" />
              </span>
            </>
          )}
        </div>
      </div>
      <div className="flex w-full items-start gap-[2px] p-1">
        <div className="min-w-0 flex-1 overflow-hidden px-[2px] py-[2px] text-ellipsis whitespace-nowrap">
          <Text font="secondary-body" color="text-03" nowrap maxLines={1}>
            {action}
          </Text>
        </div>
        <div className="flex shrink-0 items-center gap-[2px]">
          {changed > 0 && (
            <span className="px-[2px] text-[12px] leading-4 whitespace-nowrap text-(--text-03)">
              {`+${commit.added} -${commit.removed}`}
            </span>
          )}
          {url && (
            <span
              className="flex shrink-0 items-center"
              onClick={(e) => e.stopPropagation()}
            >
              <Button
                size="2xs"
                prominence="tertiary"
                icon={SvgArrowUpRight}
                href={url}
                target="_blank"
                tooltip={srcTitle ?? "Open source"}
              />
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

/** Single-initial 20px avatar. Inverts per theme via Opal's neutral-inverted
 *  background + inverted text tokens. box-border keeps the 1px border inside
 *  the box so it matches LogoAvatar's circle exactly. */
function Avatar({ initial }: { initial: string }) {
  return (
    <div
      aria-hidden
      className="box-border flex size-5 shrink-0 items-center justify-center overflow-hidden rounded-full border border-(--border-01) bg-(--background-neutral-inverted-00) text-xs font-semibold text-(--text-inverted-05)"
    >
      {initial}
    </div>
  );
}

/** Agent logo chip in the avatar stack, the same circle as Avatar,
 *  overlapping it slightly per the Figma stacked-avatar treatment. */
function LogoAvatar({ Logo }: { Logo: ComponentType<IconProps> }) {
  return (
    <div className="-ml-1 box-border flex size-5 shrink-0 items-center justify-center overflow-hidden rounded-full border border-(--border-01) bg-(--background-tint-00)">
      <Logo size={16} />
    </div>
  );
}
