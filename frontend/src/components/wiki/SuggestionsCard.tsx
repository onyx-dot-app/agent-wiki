"use client";

/** Auto-Organize suggestions card (mocks 2236:78296 / 2240:59533): pending
 * change proposals for a folder subtree with per-row approve/reject, bulk
 * actions, and the admin shortcut. Acted-on rows stay visible with their
 * outcome for a few seconds before the list refreshes them away (mock
 * annotation: leave time for user confirmation). */
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { Button, Tag, Text } from "@onyx-ai/opal/components";
import { ContentAction, Section } from "@onyx-ai/opal/layouts";
import {
  SvgCheckCircle,
  SvgExpand,
  SvgFile,
  SvgFold,
  SvgFolder,
  SvgFolderIn,
  SvgSettings,
  SvgStopCircle,
  SvgX,
} from "@onyx-ai/opal/icons";
import type { IconFunctionComponent } from "@onyx-ai/opal/types";

import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import {
  type Proposal,
  approveProposal,
  dismissProposal,
  fetchProposal,
  rejectProposal,
  useProposalsByPath,
} from "@/lib/autoOrganize";

type Outcome =
  | "working"
  | "applying"
  | "applied"
  | "rejected"
  | "dismissed"
  | "stale"
  | "error";

const HANDLED: Outcome[] = ["applied", "rejected", "dismissed", "stale"];

/** Per-op row glyph; unknown ops fall back to the page icon. */
const OP_ICON: Record<string, IconFunctionComponent> = {
  move: SvgFolderIn,
  rename: SvgFolder,
  merge: SvgFolderIn,
  split: SvgFile,
  create_folder: SvgFolder,
  delete_empty_folder: SvgFolder,
  delete_page: SvgFile,
};

interface SuggestionsCardProps {
  /** Folder (or page) scope the proposals are listed for. */
  path: string;
  /** Skip fetching for viewers; the server write-scopes regardless. */
  canWrite?: boolean;
  /** Popover hosting: shows the open-in-side-panel action. */
  onOpenPanel?: () => void;
  /** Popover hosting: the header X. Hides the popup only — suggestions
   * persist and re-show (mock annotation). */
  onClose?: () => void;
  /** Row click, with the proposal's source paths (mock: highlight folder). */
  onHighlight?: (paths: string[]) => void;
}

export function SuggestionsCard({
  path,
  canWrite = true,
  onOpenPanel,
  onClose,
  onHighlight,
}: SuggestionsCardProps) {
  const router = useRouter();
  const { user } = useAuth();
  const { proposals, refresh } = useProposalsByPath(path, canWrite);
  const [open, setOpen] = useState(true);
  const [outcomes, setOutcomes] = useState<Record<number, Outcome>>({});
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => void (alive.current = false);
  }, []);

  const setOutcome = (id: number, o: Outcome) =>
    alive.current && setOutcomes((prev) => ({ ...prev, [id]: o }));

  // Execution runs async on the automanage worker: show "Applying…" and
  // poll to a terminal status. Transient failures keep polling; an
  // unsettled poll falls back to the delayed refresh below.
  async function pollApplied(id: number) {
    setOutcome(id, "applying");
    for (let i = 0; i < 20; i++) {
      await new Promise((r) => setTimeout(r, Math.min(1000 + i * 250, 3000)));
      if (!alive.current) return;
      try {
        const fresh = await fetchProposal(id);
        if (fresh.status === "applied") return setOutcome(id, "applied");
        if (fresh.status === "stale") return setOutcome(id, "stale");
      } catch {
        // transient failure (or the row was purged) — keep polling
      }
    }
    // Didn't settle in-window: the row has left `pending` server-side, so
    // a refresh reconciles it rather than freezing it at "Applying…".
    if (alive.current) void refresh();
  }

  async function act(id: number, kind: "approve" | "reject" | "dismiss") {
    setOutcome(id, "working");
    try {
      if (kind === "approve") {
        await approveProposal(id);
        if (alive.current) void pollApplied(id);
      } else if (kind === "reject") {
        await rejectProposal(id);
        setOutcome(id, "rejected");
      } else {
        await dismissProposal(id);
        setOutcome(id, "dismissed");
      }
    } catch (e) {
      if (!alive.current) return;
      // 409: someone else already actioned it; the refresh will drop it.
      if (e instanceof ApiError && e.status === 409)
        return setOutcome(id, "dismissed");
      setOutcome(id, "error");
    }
  }

  const pendingIds = proposals
    .filter((p) => {
      const o = outcomes[p.id];
      return !o || o === "error";
    })
    .map((p) => p.id);

  // Bulk actions hit every not-yet-handled row (mock annotation: "apply to
  // any that is not already selected"); rows show their outcomes in place.
  async function actAll(kind: "approve" | "dismiss") {
    for (const id of pendingIds) await act(id, kind);
  }

  // Once every row is handled, leave the outcomes visible briefly, then
  // refresh so the settled rows drop out (they have left `pending`).
  const allHandled =
    proposals.length > 0 &&
    proposals.every((p) => HANDLED.includes(outcomes[p.id] as Outcome));
  useEffect(() => {
    if (!allHandled) return;
    const t = setTimeout(() => {
      if (alive.current) void refresh();
    }, 4000);
    return () => clearTimeout(t);
  }, [allHandled, refresh]);

  if (proposals.length === 0) return null;
  const n = proposals.length;

  return (
    <Section
      gap={0}
      justifyContent="start"
      alignItems="stretch"
      height="fit"
      padding={0.25}
      data-suggestions-card
      className="w-full rounded-(--radius-12) border border-(--status-info-02) bg-(--status-info-01)"
    >
      <Section gap={0} height="fit" alignItems="stretch" padding={0.25}>
        <ContentAction
          icon={AutoSuggestIcon}
          title={`AI Auto-Edit suggests ${n} change${n === 1 ? "" : "s"}.`}
          description="based on your admin auto-organize settings."
          sizePreset="main-ui"
          variant="section"
          width="full"
          padding="fit"
          rightChildren={
            onClose ? (
              <Button
                icon={SvgX}
                size="sm"
                prominence="tertiary"
                tooltip="Hide (suggestions stay in the side panel)"
                onClick={onClose}
              />
            ) : (
              <Button
                icon={open ? SvgFold : SvgExpand}
                size="sm"
                prominence="tertiary"
                tooltip={open ? "Collapse" : "Expand"}
                onClick={() => setOpen((v) => !v)}
              />
            )
          }
        />
      </Section>
      {open && (
        <Section gap={0.25} height="fit" alignItems="stretch" className="mt-1">
          {proposals.map((p) => (
            <SuggestionRow
              key={p.id}
              proposal={p}
              outcome={outcomes[p.id]}
              onApprove={() => void act(p.id, "approve")}
              onReject={() => void act(p.id, "reject")}
              onClick={
                onHighlight ? () => onHighlight(p.source_paths) : undefined
              }
            />
          ))}
          <Section
            gap={0}
            flexDirection="row"
            alignItems="center"
            justifyContent="between"
            height="fit"
            className="mt-1"
          >
            <Section
              gap={0.25}
              flexDirection="row"
              alignItems="center"
              width="fit"
              height="fit"
            >
              {user?.is_admin && (
                <Button
                  icon={SvgSettings}
                  size="sm"
                  prominence="tertiary"
                  tooltip="Auto-organize settings"
                  onClick={() => router.push("/admin/auto-organize")}
                />
              )}
              {onOpenPanel && (
                <Button
                  icon={SvgExpand}
                  size="sm"
                  prominence="tertiary"
                  tooltip="Open in side panel"
                  onClick={onOpenPanel}
                />
              )}
            </Section>
            <Section
              gap={0.25}
              flexDirection="row"
              alignItems="center"
              width="fit"
              height="fit"
            >
              <Button
                size="sm"
                prominence="secondary"
                disabled={pendingIds.length === 0}
                onClick={() => void actAll("dismiss")}
              >
                Dismiss All
              </Button>
              <Button
                size="sm"
                icon={SvgCheckCircle}
                disabled={pendingIds.length === 0}
                onClick={() => void actAll("approve")}
              >
                Approve All
              </Button>
            </Section>
          </Section>
        </Section>
      )}
    </Section>
  );
}

/** The card's header mark reuses the octagon Auto glyph family. */
function AutoSuggestIcon({ size = 16 }: { size?: number }) {
  return <SvgCheckCircle size={size} />;
}

const OUTCOME_LABEL: Record<string, string> = {
  applying: "Applying…",
  applied: "Applied",
  stale: "Skipped",
  rejected: "Rejected",
  dismissed: "Dismissed",
  error: "Failed",
};

interface SuggestionRowProps {
  proposal: Proposal;
  outcome?: Outcome;
  onApprove: () => void;
  onReject: () => void;
  onClick?: () => void;
}

/** One suggestion (mock Line, 56px): op icon, summary over the path chip,
 * reject/approve controls. Handled rows keep their place with the outcome
 * shown (strikethrough for rejected/dismissed, check for applied). */
function SuggestionRow({
  proposal,
  outcome,
  onApprove,
  onReject,
  onClick,
}: SuggestionRowProps) {
  const Icon = OP_ICON[proposal.op] ?? SvgFile;
  const struck = outcome === "rejected" || outcome === "dismissed";
  const chipPath = proposal.source_paths[0] ?? proposal.target_paths[0] ?? "";
  return (
    <Section
      gap={0.25}
      flexDirection="row"
      alignItems="start"
      height="fit"
      padding={0.25}
      className={`w-full rounded-(--radius-08) bg-(--background-tint-00) ${
        onClick ? "cursor-pointer" : ""
      }`}
      onClick={onClick}
    >
      <Section gap={0} width="fit" height="fit" className="mt-[2px] shrink-0">
        <Icon size={16} />
      </Section>
      <Section
        gap={0.125}
        justifyContent="start"
        alignItems="start"
        height="fit"
        className="min-w-0 flex-1"
      >
        {/* raw-ok: Text drops className, so the strikethrough state wraps it */}
        <span className={struck ? "line-through opacity-60" : ""}>
          <Text font="main-ui-action" color="text-04" nowrap maxLines={1}>
            {proposal.summary}
          </Text>
        </span>
        <Tag icon={SvgFolder} title={chipPath} color="gray" size="sm" />
      </Section>
      <Section
        gap={0.125}
        flexDirection="row"
        alignItems="center"
        width="fit"
        height="fit"
        className="shrink-0"
        // Row clicks highlight the target; the action buttons must not.
        onClick={(e) => e.stopPropagation()}
      >
        {outcome && outcome !== "working" ? (
          outcome === "applied" || outcome === "applying" ? (
            <Section
              gap={0.125}
              flexDirection="row"
              alignItems="center"
              width="fit"
              height="fit"
              className="text-(--theme-blue-05)"
            >
              <Text font="secondary-body" color="inherit" nowrap>
                {OUTCOME_LABEL[outcome]}
              </Text>
              <SvgCheckCircle size={16} />
            </Section>
          ) : (
            <Text font="secondary-body" color="text-03" nowrap>
              {OUTCOME_LABEL[outcome] ?? ""}
            </Text>
          )
        ) : (
          <>
            <Button
              icon={SvgStopCircle}
              size="sm"
              prominence="tertiary"
              tooltip="Dismiss"
              disabled={outcome === "working"}
              onClick={onReject}
            />
            <Button
              icon={SvgCheckCircle}
              size="sm"
              prominence="tertiary"
              tooltip="Approve"
              disabled={outcome === "working"}
              onClick={onApprove}
            />
          </>
        )}
      </Section>
    </Section>
  );
}
