"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Button,
  Divider,
  IconContainer,
  LineItemButton,
  Tag,
  Text,
} from "@onyx-ai/opal/components";
import { Section } from "@onyx-ai/opal/layouts";
import {
  SvgArrowUpRight,
  SvgOnyxOctagon,
  SvgTextLinesSmall,
} from "@onyx-ai/opal/icons";
import type { IconFunctionComponent } from "@onyx-ai/opal/types";
import { SvgAnthropic, SvgOpenai } from "@onyx-ai/opal/logos";

import {
  AnchoredPanel,
  AutoGlyph,
  PolicyPopover,
  type OpenUpdatesPanelOpts,
} from "@/components/wiki/policyPanels";
import { relativeTime } from "@/lib/users";
import { fetchFileHistory } from "@/lib/wiki/svc";
import { useUpdateHealth } from "@/lib/wiki/hooks";
import type { CommitInfo } from "@/lib/wiki/types";
import { parseCommitAuthor, updateWarnLevel } from "@/lib/wiki/utils";
import type { CoeditPeer } from "@/lib/editor/hooks";
import type { CoeditParticipant } from "@/lib/editor/svc";
import type { DocumentActivity } from "@/types";

// Identity hues cycled per user. Semantic hues stay reserved: red/green/
// orange for status, blue/amber/sky for selection. The mock's mint/coral/
// violet have no Opal tokens, so the cycle substitutes shipped hues.
const USER_COLORS = [
  "var(--neon-cyan-50)",
  "var(--neon-yellow-50)",
  "var(--neon-lime-60)",
  "var(--neon-magenta-50)",
  "var(--purple-50)",
];

const MAX_CHIPS = 5;

function agentGlyph(name: string | null): IconFunctionComponent | null {
  const key = name?.trim().toLowerCase();
  if (!key) return null;
  if (key.includes("claude")) return SvgAnthropic;
  if (key.includes("codex") || key.includes("openai")) return SvgOpenai;
  if (key.includes("onyx") || key.includes("craft")) return SvgOnyxOctagon;
  return null;
}

interface PresenceEntry {
  userId: string;
  display: string;
  color: string;
  editing: boolean;
  /** Live agent writing for this user on the page, when one is. */
  agentName: string | null;
  /** Caret offset to scroll to when this entry is editing. */
  caretHead: number | null;
}

interface PresenceAvatarsProps {
  path: string;
  /** Whether the current user may edit this page (policy writes gate on it). */
  canWrite: boolean;
  participants: CoeditParticipant[];
  peers: CoeditPeer[];
  typing: string[];
  myUserId: string | null;
  agents: DocumentActivity[];
  /** Scrolls the doc to a live caret offset (card click, mock: "Click to
   * scroll to cursor"). */
  onScrollToOffset?: (offset: number) => void;
  /** Opens the given commit in the history view (edit-row click). */
  onOpenCommit?: (sha: string) => void;
  /** Opens the updates side panel. */
  onOpenUpdatesPanel?: (opts?: OpenUpdatesPanelOpts) => void;
}

interface AvatarCircleProps {
  entry: PresenceEntry;
  /** IconContainer preset: main-content 24px, main-ui 20px, secondary 16px. */
  size: "main-content" | "main-ui" | "secondary";
}

/** The identity-colored ring: Opal's avatar circles carry no color
 * prop, so the ring overlays without touching the primitive's box. */
function IdentityRing({ color }: { color: string }) {
  return (
    <span
      aria-hidden
      className="pointer-events-none absolute inset-0 rounded-full border-2"
      style={{ borderColor: color }}
    />
  );
}

function AvatarCircle({ entry, size }: AvatarCircleProps) {
  return (
    <Section gap={0} width="fit" height="fit" className="relative shrink-0">
      <IconContainer size={size} avatar="user" name={entry.display} />
      <IdentityRing color={entry.color} />
    </Section>
  );
}

function AgentBadge({ entry, size }: AvatarCircleProps) {
  const Glyph = agentGlyph(entry.agentName);
  if (!Glyph) return null;
  return (
    <Section
      gap={0}
      width="fit"
      height="fit"
      className="relative shrink-0 text-(--text-05)"
    >
      <IconContainer size={size} avatar="icon" icon={Glyph} />
      <IdentityRing color={entry.color} />
    </Section>
  );
}

interface PresenceCardProps {
  entry: PresenceEntry;
  commits: CommitInfo[];
  onScrollToOffset?: (offset: number) => void;
  onOpenCommit?: (sha: string) => void;
}

/** One user's card: identity, live state, and their recent edits on this
 * page (mock 2079:379824 / 2079:381324). */
function PresenceCard({
  entry,
  commits,
  onScrollToOffset,
  onOpenCommit,
}: PresenceCardProps) {
  const edits = commits.slice(0, 3);
  const scrollable = entry.editing && entry.caretHead !== null;
  // Unmapped agent names get a subtitle but no badge. Gating the slot on
  // the glyph keeps the title row from holding a blank 16px box.
  const hasBadge = !!agentGlyph(entry.agentName);
  return (
    // Mock anatomy (node 2079:380115 "Activity Panel"): the elevated white
    // title card sits on the shared panel surface, and the Recent Edits
    // list renders BELOW the card on the panel background.
    <Section
      gap={0}
      justifyContent="start"
      alignItems="stretch"
      height="fit"
      data-presence-card={entry.userId}
      className="w-full"
    >
      <Section
        flexDirection="row"
        justifyContent="start"
        alignItems="start"
        height="fit"
        gap={0.25}
        padding={0.5}
        className={`w-full rounded-(--radius-08) bg-(--background-tint-00) shadow-[0px_2px_6px_var(--shadow-02),0px_0px_2px_var(--shadow-01)] ${scrollable ? "cursor-pointer" : ""}`}
        onClick={
          scrollable
            ? () => onScrollToOffset?.(entry.caretHead as number)
            : undefined
        }
      >
        {/* Node 2079:380987: three nested 4/2/2 insets collapse to p-2,
            putting the avatar pair 8px off the card edge. */}
        <Section
          gap={0}
          flexDirection="row"
          alignItems="center"
          width="fit"
          height="fit"
          padding={0.125}
          className="shrink-0"
        >
          {/* 16px slots under the 20px circles give the avatar/badge
              pair the header stack's 4px overlap. */}
          <Section
            flexDirection="row"
            alignItems="center"
            justifyContent="center"
            width={1}
            height="fit"
            gap={0}
          >
            <AvatarCircle entry={entry} size="main-ui" />
          </Section>
          {hasBadge && (
            <Section
              flexDirection="row"
              alignItems="center"
              justifyContent="center"
              width={1}
              height="fit"
              gap={0}
            >
              <AgentBadge entry={entry} size="main-ui" />
            </Section>
          )}
        </Section>
        <Section
          gap={0}
          justifyContent="start"
          alignItems="stretch"
          height="fit"
          className="min-w-0 flex-1"
        >
          <Text font="main-ui-action" color="text-04" as="p" nowrap>
            {entry.display}
          </Text>
          {entry.agentName && (
            <Text font="secondary-body" color="text-03" as="p" nowrap>
              {`${entry.editing ? "Editing" : "Viewing"} with ${entry.agentName}`}
            </Text>
          )}
        </Section>
        <Section
          gap={0.125}
          flexDirection="row"
          alignItems="center"
          width="fit"
          height={1.25}
          className="shrink-0 text-(--status-text-info-05)"
        >
          {/* raw-ok: no Opal Text color maps to the mock's status-text-info-05 state chip */}
          <span className="px-[2px] text-[12px] leading-4">
            {entry.editing ? "Editing" : "Viewing"}
          </span>
          {entry.editing ? (
            <SvgArrowUpRight size={12} className="mx-[2px]" />
          ) : (
            <span className="mx-[2px] size-[6px] rounded-full bg-(--status-text-info-05)" />
          )}
        </Section>
      </Section>
      {edits.length > 0 && (
        <Section
          gap={0}
          justifyContent="start"
          alignItems="stretch"
          height="fit"
          className="my-1"
        >
          <Divider title="Recent Edits" />
          {edits.map((c) => {
            const { agentLabel } = parseCommitAuthor(c.author);
            const changed = c.added + c.removed;
            const lines = `${changed} line${changed === 1 ? "" : "s"}`;
            // Pure additions read "Added", anything else "Updated"
            // (mock rows "Added 40 lines" / "Updated 45 lines with Codex").
            const verb = c.removed === 0 ? "Added" : "Updated";
            return (
              <LineItemButton
                key={c.sha}
                title={
                  agentLabel
                    ? `${verb} ${lines} with ${agentLabel}`
                    : `${verb} ${lines}`
                }
                sizePreset="secondary"
                variant="body"
                rightChildren={
                  <Section
                    gap={0.125}
                    flexDirection="row"
                    alignItems="center"
                    width="fit"
                    className="text-(--text-03)"
                  >
                    <Text font="secondary-body" color="text-03" nowrap>
                      {relativeTime(c.ts)}
                    </Text>
                    <SvgArrowUpRight size={12} className="mx-[2px]" />
                  </Section>
                }
                onClick={() => onOpenCommit?.(c.sha)}
              />
            );
          })}
        </Section>
      )}
    </Section>
  );
}

/**
 * The header's "who is on this page" cluster (mocks 2079:379824,
 * 2079:381324, 2079:383512, 1929:361938): every coedit participant as
 * overlapping colored chips, agents badged onto the user they act for,
 * a +N overflow, and the Auto slot controlling AI auto-edits. The
 * current user is never shown.
 */
export function PresenceAvatars({
  path,
  canWrite,
  participants,
  peers,
  typing,
  myUserId,
  agents,
  onScrollToOffset,
  onOpenCommit,
  onOpenUpdatesPanel,
}: PresenceAvatarsProps) {
  const entries = useMemo<PresenceEntry[]>(() => {
    const editingIds = new Set<string>([
      ...peers.map((p) => p.user_id),
      ...typing,
    ]);
    const caretByUser = new Map(peers.map((p) => [p.user_id, p.head]));
    // One agent summary per user: a user can hold a row per agent, and any
    // writing agent outranks read rows for the badge and the state.
    const agentPresence = new Map<
      string,
      { display: string; agentName: string | null; writing: boolean }
    >();
    for (const a of agents) {
      const cur = agentPresence.get(a.user_id);
      const writing = a.activity === "wrote";
      if (!cur || (writing && !cur.writing)) {
        agentPresence.set(a.user_id, {
          display: a.owner_display,
          agentName: a.agent_name,
          writing,
        });
      }
    }
    const entry = (
      userId: string,
      display: string,
      i: number,
      editing: boolean,
      agentName: string | null,
    ): PresenceEntry => ({
      userId,
      display,
      color: USER_COLORS[i % USER_COLORS.length],
      editing,
      agentName,
      caretHead: caretByUser.get(userId) ?? null,
    });
    const roster = participants
      .filter((p) => p.user_id !== myUserId)
      .map((p, i) => {
        // A writing agent makes its user an editor even with an idle caret.
        const presence = agentPresence.get(p.user_id);
        return entry(
          p.user_id,
          p.user_display,
          i,
          editingIds.has(p.user_id) || !!presence?.writing,
          presence?.agentName ?? null,
        );
      });
    // Agents register as the user they act for, so a user whose agent is
    // active stays in the stack even without a browser session.
    const seated = new Set(roster.map((e) => e.userId));
    for (const [userId, p] of agentPresence) {
      if (userId === myUserId || seated.has(userId)) continue;
      seated.add(userId);
      roster.push(
        entry(userId, p.display, roster.length, p.writing, p.agentName),
      );
    }
    return roster;
  }, [participants, peers, typing, myUserId, agents]);

  const shown = entries.slice(0, MAX_CHIPS);
  const overflow = entries.slice(MAX_CHIPS);

  const clusterRef = useRef<HTMLDivElement | null>(null);
  // One panel at a time by construction: opening any panel replaces
  // whatever was open, so the card, Auto popover, and overflow list can
  // never stack (mocks show exactly one floating surface).
  const [openPanel, setOpenPanel] = useState<
    | { kind: "card"; userId: string }
    | { kind: "auto" }
    | { kind: "overflow" }
    | null
  >(null);
  const closeTimer = useRef<number>(0);

  const holdOpen = useCallback(
    () => window.clearTimeout(closeTimer.current),
    [],
  );
  const closeSoon = useCallback(() => {
    window.clearTimeout(closeTimer.current);
    closeTimer.current = window.setTimeout(() => setOpenPanel(null), 150);
  }, []);
  useEffect(() => () => window.clearTimeout(closeTimer.current), []);

  // Path changes drop every floating panel and the edits cache, nothing
  // from the previous doc may drive fetches or clicks on the new one.
  const [commits, setCommits] = useState<CommitInfo[] | null>(null);
  useEffect(() => {
    setCommits(null);
    setOpenPanel(null);
  }, [path]);
  const loadCommits = useCallback(() => {
    if (commits) return;
    fetchFileHistory(path)
      .then((r) => setCommits(r.commits))
      .catch(() => setCommits([]));
  }, [commits, path]);

  const commitsFor = useCallback(
    (entry: PresenceEntry) =>
      (commits ?? []).filter(
        (c) => parseCommitAuthor(c.author).person === entry.display,
      ),
    [commits],
  );

  const hovered =
    openPanel?.kind === "card"
      ? entries.find((e) => e.userId === openPanel.userId)
      : undefined;
  // A card whose user left the roster must not linger in state: the user
  // re-entering later would silently re-mount the panel with no hover.
  useEffect(() => {
    if (openPanel?.kind === "card" && !hovered) setOpenPanel(null);
  }, [openPanel, hovered]);

  // Ring color tracks the page's update-warn level (mock annotation
  // "Match warning level").
  const { health } = useUpdateHealth(path);
  const warnLevel = updateWarnLevel(health);

  return (
    <Section
      ref={clusterRef}
      flexDirection="row"
      alignItems="center"
      width="fit"
      height="fit"
      gap={0.25}
      padding={0.125}
    >
      {entries.length > 0 && (
        // DOM order runs last-chip-first so paint order stacks earlier
        // chips on top (the mock), row-reverse restores the visual order.
        <Section
          gap={0}
          flexDirection="row"
          alignItems="center"
          width="fit"
          height="fit"
          padding={0.125}
          className="isolate flex-row-reverse"
        >
          {overflow.length > 0 && (
            // raw-ok: Tag is a non-interactive div, the click needs a button wrapper
            <button
              type="button"
              aria-label={`${overflow.length} more`}
              aria-expanded={openPanel?.kind === "overflow"}
              className="ml-[2px] cursor-pointer"
              // holdOpen: a close timer pending from leaving a chip would
              // otherwise dismiss the overflow panel right after this click.
              onPointerEnter={holdOpen}
              onClick={() => {
                holdOpen();
                setOpenPanel((p) =>
                  p?.kind === "overflow" ? null : { kind: "overflow" },
                );
                loadCommits();
              }}
            >
              <Tag title={`+${overflow.length}`} color="gray" />
            </button>
          )}
          {[...shown].reverse().map((e) => (
            // Each slot is 20px wide holding a 24px chip, the 4px overlap.
            <Section
              key={e.userId}
              flexDirection="row"
              alignItems="center"
              justifyContent="center"
              width={1.25}
              height="fit"
              gap={0}
              data-presence-chip={e.display}
              className="relative"
              onPointerEnter={() => {
                holdOpen();
                setOpenPanel({ kind: "card", userId: e.userId });
                loadCommits();
              }}
              onPointerLeave={closeSoon}
            >
              <AvatarCircle entry={e} size="main-content" />
              {agentGlyph(e.agentName) && (
                <Section
                  gap={0}
                  width="fit"
                  height="fit"
                  className="absolute top-[12px] left-[10px]"
                >
                  <AgentBadge entry={e} size="secondary" />
                </Section>
              )}
            </Section>
          ))}
        </Section>
      )}
      {entries.length > 0 && (
        <Section gap={0} height={1} width="fit" className="self-center">
          <Divider
            orientation="vertical"
            paddingParallel="fit"
            paddingPerpendicular="fit"
          />
        </Section>
      )}
      {/* raw-ok: the Auto trigger is the mock's ringed avatar circle, not a standard icon button */}
      <button
        type="button"
        aria-label="AI auto-edits"
        className="flex cursor-pointer items-center justify-center px-[2px]"
        // Hover previews the popover; click commits to the side panel
        // (mock annotation: "Clicking on the auto icon or panel will open
        // in the side panel").
        onClick={() => {
          setOpenPanel(null);
          onOpenUpdatesPanel?.();
        }}
        onPointerEnter={() => {
          holdOpen();
          setOpenPanel({ kind: "auto" });
        }}
        onPointerLeave={closeSoon}
      >
        <Section gap={0} width="fit" height="fit" className="relative">
          <IconContainer size="main-content" avatar="icon" icon={AutoGlyph} />
          <span
            aria-hidden
            className={`pointer-events-none absolute inset-0 rounded-full border ${
              warnLevel === "over"
                ? "border-(--status-warning-02)"
                : warnLevel === "near"
                  ? "border-(--theme-amber-02)"
                  : "border-(--theme-blue-05)"
            }`}
          />
        </Section>
      </button>

      {hovered && clusterRef.current && (
        <AnchoredPanel
          anchor={clusterRef.current}
          onDismiss={() => setOpenPanel(null)}
          hover={{ onEnter: holdOpen, onLeave: closeSoon }}
        >
          <PresenceCard
            entry={hovered}
            commits={commitsFor(hovered)}
            onScrollToOffset={onScrollToOffset}
            onOpenCommit={onOpenCommit}
          />
        </AnchoredPanel>
      )}
      {openPanel?.kind === "overflow" && clusterRef.current && (
        <AnchoredPanel
          anchor={clusterRef.current}
          onDismiss={() => setOpenPanel(null)}
        >
          <Section
            justifyContent="start"
            alignItems="stretch"
            height="fit"
            gap={0.25}
          >
            {overflow.map((e) => (
              <PresenceCard
                key={e.userId}
                entry={e}
                commits={commitsFor(e)}
                onScrollToOffset={onScrollToOffset}
                onOpenCommit={onOpenCommit}
              />
            ))}
          </Section>
        </AnchoredPanel>
      )}
      {openPanel?.kind === "auto" && clusterRef.current && (
        <AnchoredPanel
          anchor={clusterRef.current}
          onDismiss={() => setOpenPanel(null)}
          hover={{ onEnter: holdOpen, onLeave: closeSoon }}
        >
          <PolicyPopover
            path={path}
            canWrite={canWrite}
            // The popover hands off to the side panel in place: same UI,
            // so it dismisses as the panel opens.
            onOpenUpdatesPanel={(opts) => {
              setOpenPanel(null);
              onOpenUpdatesPanel?.(opts);
            }}
          />
        </AnchoredPanel>
      )}
    </Section>
  );
}
