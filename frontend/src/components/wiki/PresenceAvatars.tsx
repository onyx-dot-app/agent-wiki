"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import {
  Button,
  Divider,
  IconContainer,
  LineItemButton,
  Switch,
  Tag,
  Text,
} from "@onyx-ai/opal/components";
import { InputHorizontal, Section } from "@onyx-ai/opal/layouts";
import {
  SvgAddLines,
  SvgArrowUpRight,
  SvgExpand,
  SvgSparkle,
} from "@onyx-ai/opal/icons";
import type { IconFunctionComponent } from "@onyx-ai/opal/types";
import { SvgClaude, SvgOnyxLogo, SvgOpenai } from "@onyx-ai/opal/logos";

import { toast } from "@/hooks/useToast";
import {
  getUpdatePolicy,
  patchUpdatePolicy,
  type EffectivePolicy,
} from "@/lib/updatePolicy";
import { relativeTime } from "@/lib/users";
import { fetchFileHistory } from "@/lib/wiki/svc";
import type { CommitInfo } from "@/lib/wiki/types";
import { parseCommitAuthor } from "@/lib/wiki/utils";
import type { CoeditPeer } from "@/lib/tiptapEditor/hooks";
import type { CoeditParticipant } from "@/lib/tiptapEditor/svc";
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
  if (key.includes("claude")) return SvgClaude;
  if (key.includes("codex") || key.includes("openai")) return SvgOpenai;
  if (key.includes("onyx") || key.includes("craft")) return SvgOnyxLogo;
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
  /** Opens the updates side panel (Page Instructions expand). */
  onOpenUpdatesPanel?: () => void;
}

interface AvatarCircleProps {
  entry: PresenceEntry;
  /** IconContainer preset: main-content 24px, main-ui 20px, secondary 16px. */
  size: "main-content" | "main-ui" | "secondary";
}

/** The identity-colored 1px ring: Opal's avatar circles carry no color
 * prop, so the ring overlays without touching the primitive's box. */
function IdentityRing({ color }: { color: string }) {
  return (
    <span
      aria-hidden
      className="pointer-events-none absolute inset-0 rounded-full border"
      style={{ borderColor: color }}
    />
  );
}

function AvatarCircle({ entry, size }: AvatarCircleProps) {
  return (
    <span className="relative flex shrink-0">
      <IconContainer size={size} avatar="user" name={entry.display} />
      <IdentityRing color={entry.color} />
    </span>
  );
}

function AgentBadge({ entry, size }: AvatarCircleProps) {
  const Glyph = agentGlyph(entry.agentName);
  if (!Glyph) return null;
  return (
    <span className="relative flex shrink-0">
      <IconContainer size={size} avatar="icon" icon={Glyph} />
      <IdentityRing color={entry.color} />
    </span>
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
  return (
    <Section justifyContent="start" alignItems="stretch" height="fit">
      <Section
        flexDirection="row"
        justifyContent="start"
        alignItems="start"
        height="fit"
        gap={0.25}
        data-presence-card={entry.userId}
        // Mock card chrome: white surface lifted off the panel body.
        className={`w-full rounded-(--radius-08) bg-(--background-tint-00) p-1 shadow-[0px_2px_6px_var(--shadow-02),0px_0px_2px_var(--shadow-01)] ${
          scrollable ? "cursor-pointer" : ""
        }`}
        onClick={
          scrollable
            ? () => onScrollToOffset?.(entry.caretHead as number)
            : undefined
        }
      >
        <Section
          flexDirection="row"
          alignItems="center"
          width="fit"
          height="fit"
          className="shrink-0 p-[2px]"
        >
          <AvatarCircle entry={entry} size="main-ui" />
          {entry.agentName && <AgentBadge entry={entry} size="main-ui" />}
        </Section>
        <Section
          justifyContent="start"
          alignItems="stretch"
          height="fit"
          className="min-w-0 flex-1 px-[2px]"
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
          flexDirection="row"
          alignItems="center"
          width="fit"
          height="fit"
          className="shrink-0 gap-[2px] pt-[2px]"
        >
          {/* raw-ok: no Opal Text color maps to the mock's status-text-info-05 state chip */}
          <span className="px-[2px] text-[12px] leading-4 text-(--status-text-info-05)">
            {entry.editing ? "Editing" : "Viewing"}
          </span>
          {entry.editing ? (
            <SvgArrowUpRight size={12} />
          ) : (
            <span className="mx-[2px] size-[6px] rounded-full bg-(--status-text-info-05)" />
          )}
        </Section>
      </Section>
      {edits.length > 0 && (
        <Section
          justifyContent="start"
          alignItems="stretch"
          height="fit"
          className="py-1"
        >
          <Divider title="Recent Edits" />
          {edits.map((c) => {
            const { agentLabel } = parseCommitAuthor(c.author);
            const changed = c.added + c.removed;
            const lines = `${changed} line${changed === 1 ? "" : "s"}`;
            return (
              <LineItemButton
                key={c.sha}
                title={
                  agentLabel
                    ? `Updated ${lines} with ${agentLabel}`
                    : `Updated ${lines}`
                }
                sizePreset="secondary"
                variant="body"
                rightChildren={
                  <Section
                    flexDirection="row"
                    alignItems="center"
                    width="fit"
                    className="gap-[2px]"
                  >
                    <Text font="secondary-body" color="text-03" nowrap>
                      {relativeTime(c.ts)}
                    </Text>
                    <SvgArrowUpRight size={12} />
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

interface AnchoredPanelProps {
  anchor: HTMLElement;
  onDismiss: () => void;
  /** Hover panels stay open while the pointer is inside them. */
  hover?: { onEnter: () => void; onLeave: () => void };
  children: ReactNode;
}

/** Anchors a floating panel to the header cluster: fixed-position, right
 * edges aligned, just below the anchor. */
function AnchoredPanel({
  anchor,
  onDismiss,
  hover,
  children,
}: AnchoredPanelProps) {
  const [rect, setRect] = useState(() => anchor.getBoundingClientRect());
  useEffect(() => {
    setRect(anchor.getBoundingClientRect());
  }, [anchor]);
  const panelRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (hover) return;
    const onDown = (e: PointerEvent) => {
      const el = panelRef.current;
      if (
        el &&
        !el.contains(e.target as Node) &&
        !anchor.contains(e.target as Node)
      )
        onDismiss();
    };
    window.addEventListener("pointerdown", onDown, true);
    return () => window.removeEventListener("pointerdown", onDown, true);
  }, [anchor, hover, onDismiss]);
  return createPortal(
    // raw-ok: Popover.Anchor exists but Popover.Content bakes neutral-00/rounded-12/shadow-md chrome (WithoutStyles, no escape) that the mock's tint-01 policy panel and chromeless floating cards contradict, and this wrapper also needs runtime viewport coordinates
    <div
      ref={panelRef}
      className="fixed z-50"
      style={{ top: rect.bottom + 8, right: window.innerWidth - rect.right }}
      onPointerEnter={hover?.onEnter}
      onPointerLeave={hover?.onLeave}
    >
      <Section
        justifyContent="start"
        alignItems="stretch"
        height="fit"
        className="w-(--block-width-panel-medium-small)"
      >
        {children}
      </Section>
    </div>,
    document.body,
  );
}

interface PolicyPopoverProps {
  path: string;
  /** The policy PATCH is write-gated, so read-only viewers get a
   * disabled switch instead of a doomed request. */
  canWrite: boolean;
  onOpenUpdatesPanel?: () => void;
}

/** The Auto popover (mock 1929:362227 "Policy Panel"): the AI Auto-Edits
 * switch and the page's update instruction, both live on the update
 * policy the full panel edits. */
function PolicyPopover({
  path,
  canWrite,
  onOpenUpdatesPanel,
}: PolicyPopoverProps) {
  // The policy carries the path it was fetched for: a mismatch reads as
  // unloaded in the same render a navigation lands, so a stale page's
  // value can never be shown or PATCHed against the new path.
  const [policy, setPolicy] = useState<{
    forPath: string;
    effective: EffectivePolicy;
  } | null>(null);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    let alive = true;
    getUpdatePolicy(path)
      .then(
        (p) => alive && setPolicy({ forPath: path, effective: p.effective }),
      )
      .catch(() => alive && setPolicy(null));
    return () => {
      alive = false;
    };
  }, [path]);
  const loaded = policy?.forPath === path ? policy.effective : null;

  const allowed = !!loaded?.ai_management_allowed;
  const toggle = async () => {
    if (!loaded) return;
    setSaving(true);
    setPolicy({
      forPath: path,
      effective: { ...loaded, ai_management_allowed: !allowed },
    });
    try {
      await patchUpdatePolicy(path, { ai_management_allowed: !allowed });
    } catch (e) {
      setPolicy({
        forPath: path,
        effective: { ...loaded, ai_management_allowed: allowed },
      });
      toast.error(
        e instanceof Error ? e.message : "Couldn't update the page's policy",
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <Section
      justifyContent="start"
      alignItems="stretch"
      height="fit"
      gap={0.25}
      className="rounded-(--radius-12) border border-(--border-01) bg-(--background-tint-01) p-1 shadow-[0px_2px_12px_0px_var(--shadow-02),0px_0px_4px_1px_var(--shadow-01)]"
    >
      <Section height="fit" alignItems="stretch" padding={0.5}>
        <InputHorizontal
          icon={SvgSparkle}
          title="AI Auto-Edits"
          description="Let AI update/organize this page on its own."
        >
          <Switch
            checked={allowed}
            // Held until this path's policy loads (toggling against the
            // null default would persist a wrong override) and while a
            // save is in flight (a second click would race the PATCH).
            disabled={!canWrite || !loaded || saving}
            onCheckedChange={() => void toggle()}
          />
        </InputHorizontal>
      </Section>
      <Divider />
      <Section height="fit" alignItems="stretch" padding={0.5}>
        <InputHorizontal
          icon={SvgAddLines}
          title="Page Instructions"
          description={
            loaded?.update_instruction || "How should this page be updated?"
          }
        >
          <Button
            icon={SvgExpand}
            size="md"
            prominence="tertiary"
            tooltip="Open in panel"
            onClick={onOpenUpdatesPanel}
          />
        </InputHorizontal>
      </Section>
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
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [overflowOpen, setOverflowOpen] = useState(false);
  const [autoOpen, setAutoOpen] = useState(false);
  const closeTimer = useRef<number>(0);

  const holdOpen = useCallback(
    () => window.clearTimeout(closeTimer.current),
    [],
  );
  const closeSoon = useCallback(() => {
    window.clearTimeout(closeTimer.current);
    closeTimer.current = window.setTimeout(() => setHoverId(null), 150);
  }, []);
  useEffect(() => () => window.clearTimeout(closeTimer.current), []);

  // Path changes drop every floating panel and the edits cache, nothing
  // from the previous doc may drive fetches or clicks on the new one.
  const [commits, setCommits] = useState<CommitInfo[] | null>(null);
  useEffect(() => {
    setCommits(null);
    setHoverId(null);
    setOverflowOpen(false);
    setAutoOpen(false);
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

  const hovered = entries.find((e) => e.userId === hoverId);

  return (
    <Section
      ref={clusterRef}
      flexDirection="row"
      alignItems="center"
      width="fit"
      height="fit"
      gap={0.25}
      className="px-[2px]"
    >
      {entries.length > 0 && (
        // DOM order runs last-chip-first so paint order stacks earlier
        // chips on top (the mock), row-reverse restores the visual order.
        <Section
          flexDirection="row"
          alignItems="center"
          width="fit"
          height="fit"
          className="isolate flex-row-reverse px-[2px]"
        >
          {overflow.length > 0 && (
            // raw-ok: Tag is a non-interactive div, the click needs a button wrapper
            <button
              type="button"
              aria-label={`${overflow.length} more`}
              className="ml-[2px] cursor-pointer"
              onClick={() => {
                setOverflowOpen((v) => !v);
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
              width="fit"
              height="fit"
              data-presence-chip={e.display}
              className="relative w-5"
              onPointerEnter={() => {
                holdOpen();
                setHoverId(e.userId);
                loadCommits();
              }}
              onPointerLeave={closeSoon}
            >
              <AvatarCircle entry={e} size="main-content" />
              {e.agentName && (
                <Section
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
        <Section height="fit" width="fit" className="h-4 self-center">
          <Divider
            orientation="vertical"
            paddingParallel="fit"
            paddingPerpendicular="fit"
          />
        </Section>
      )}
      {/* raw-ok: the Auto trigger is the mock's blue-ringed avatar circle, not a standard icon button */}
      <button
        type="button"
        aria-label="AI auto-edits"
        className="flex cursor-pointer items-center justify-center px-[2px]"
        onClick={() => setAutoOpen((v) => !v)}
      >
        <span className="relative flex">
          <IconContainer size="main-content" avatar="icon" icon={SvgOnyxLogo} />
          <span
            aria-hidden
            className="pointer-events-none absolute inset-0 rounded-full border border-(--theme-blue-05)"
          />
        </span>
      </button>

      {hovered && clusterRef.current && (
        <AnchoredPanel
          anchor={clusterRef.current}
          onDismiss={() => setHoverId(null)}
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
      {overflowOpen && clusterRef.current && (
        <AnchoredPanel
          anchor={clusterRef.current}
          onDismiss={() => setOverflowOpen(false)}
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
      {autoOpen && clusterRef.current && (
        <AnchoredPanel
          anchor={clusterRef.current}
          onDismiss={() => setAutoOpen(false)}
        >
          <PolicyPopover
            path={path}
            canWrite={canWrite}
            onOpenUpdatesPanel={onOpenUpdatesPanel}
          />
        </AnchoredPanel>
      )}
    </Section>
  );
}
