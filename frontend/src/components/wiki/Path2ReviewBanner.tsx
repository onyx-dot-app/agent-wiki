"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { Button, MessageCard, Text } from "@onyx-ai/opal/components";
import {
  SvgCheckAll,
  SvgCheckCircle,
  SvgSettings,
  SvgSlash,
} from "@onyx-ai/opal/icons";
import { ContentAction, Section } from "@onyx-ai/opal/layouts";
import { timeAgo } from "@onyx-ai/opal/time";

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

interface Props {
  /** The current wiki page or folder path. */
  path: string;
  /** The caller's write capability, used only to skip the fetch/flash for
   * viewers — the server write-scopes the list regardless, so this is an
   * optimization, not the authority. Defaults to true (always fetch). */
  canWrite?: boolean;
  /** How the floating card pins to the top-right of the content area.
   * `"sticky"` (default) suits a mount at the top of a scrolling container
   * (folder view). `"absolute"` suits a mount inside a `relative` doc area
   * whose scrolling happens in a nested element (FileView — sticky there
   * would sit at the mount's natural flow position instead of the top). */
  pin?: "sticky" | "absolute";
}

type RowAction = "approve" | "reject" | "dismiss";

/** UTC second-granular DB text ("YYYY-MM-DD HH:MM:SS") → relative age. */
function scanAge(ts: string | null): string | null {
  if (!ts) return null;
  return timeAgo(ts.replace(" ", "T") + "Z");
}

/**
 * Path-2 review banner: surfaces the pending Auto Organize cleanup proposals
 * touching this page/folder to users who can act on them. A page carries at
 * most one live proposal (sweep selection guarantees it); a folder aggregates
 * its subtree, and the live set is pairwise-disjoint on claims, so the batch
 * buttons can loop the rows in any order. The list is write-scoped server-side
 * (a read-only viewer gets nothing), so we simply render nothing when it's
 * empty. Execution is async, so after an approve we poll the proposal to
 * report the applied / went-stale outcome rather than leaving it as a silent
 * fire-and-forget.
 *
 * Verbs: approve (do it) · dismiss (clear the card — may return if the finding
 * is still detected) · reject (durable — won't be suggested again). The X only
 * closes the card for this visit; the proposals persist.
 *
 * Placement: a floating card pinned to the top-right of the content column
 * (sticky inside the scroll container, zero layout height). The right inset
 * follows `--cm-gutter` when the mount's container defines it (FileView's
 * editor gutter) and falls back to 0 where the container's own padding
 * already insets the content (folder view).
 */
export function Path2ReviewBanner({
  path,
  canWrite = true,
  pin = "sticky",
}: Props) {
  const { proposals, refresh } = useProposalsByPath(path, canWrite);
  const { user } = useAuth();
  const router = useRouter();
  const [closed, setClosed] = useState(false);
  // Rows register their action dispatcher here so the footer's batch buttons
  // can drive them without lifting each row's outcome state machine up.
  const rowActions = useRef(new Map<number, (kind: RowAction) => void>());

  if (closed || proposals.length === 0) return null;

  const n = proposals.length;
  // Every sweep re-stamps carried pendings, so the newest stamp is "the last
  // scan that confirmed these findings against current wiki state".
  const newest = proposals.reduce<string | null>(
    (acc, p) =>
      (p.last_emitted_at ?? "") > (acc ?? "") ? p.last_emitted_at : acc,
    null,
  );
  const age = scanAge(newest);

  function actAll(kind: "approve" | "dismiss") {
    // Rows that have already been actioned ignore the call (act() guards).
    for (const act of rowActions.current.values()) act(kind);
  }

  return (
    <div
      className={`pointer-events-none z-30 ${
        pin === "absolute" ? "absolute inset-x-0 top-0" : "sticky top-0 h-0"
      }`}
    >
      <div className="pointer-events-auto mr-[var(--cm-gutter,0px)] ml-auto w-[400px] max-w-full rounded-(--radius-12) bg-(--background-01) shadow-(--shadow-modal)">
        <MessageCard
          variant="info"
          title={`Auto Organize suggests ${n} change${n === 1 ? "" : "s"} here`}
          description={age ? `Confirmed by the last scan · ${age}` : undefined}
          onClose={() => setClosed(true)}
          bottomChildren={
            <Section flexDirection="column" gap={0.25} width="full">
              {proposals.map((p) => (
                <ProposalRow
                  key={p.id}
                  proposal={p}
                  onActioned={refresh}
                  actions={rowActions.current}
                />
              ))}
              <Section
                flexDirection="row"
                gap={0.5}
                alignItems="center"
                justifyContent="between"
                width="full"
              >
                <Section flexDirection="row" alignItems="center">
                  {user?.is_admin && (
                    <Button
                      icon={SvgSettings}
                      size="sm"
                      prominence="tertiary"
                      tooltip="Auto Organize settings"
                      onClick={() => router.push("/admin/auto-organize")}
                    />
                  )}
                </Section>
                <Section flexDirection="row" gap={0.5} alignItems="center">
                  <Button
                    icon={SvgSlash}
                    size="sm"
                    prominence="secondary"
                    tooltip="Clear these suggestions — they may return if still detected"
                    onClick={() => actAll("dismiss")}
                  >
                    Dismiss all
                  </Button>
                  <Button
                    icon={SvgCheckAll}
                    size="sm"
                    prominence="primary"
                    onClick={() => actAll("approve")}
                  >
                    Approve all
                  </Button>
                </Section>
              </Section>
            </Section>
          }
        />
      </div>
    </div>
  );
}

type Outcome =
  | "idle"
  | "working"
  | "applied"
  | "rejected"
  | "dismissed"
  | "stale"
  | "applying"
  | "error";

const TERMINAL: Outcome[] = [
  "applied",
  "rejected",
  "dismissed",
  "stale",
  "applying",
];

const STATUS_LABEL: Record<string, string> = {
  applied: "Applied ✓",
  rejected: "Rejected",
  dismissed: "Dismissed",
  stale: "Skipped — the page changed since this was proposed",
  applying: "Applying…",
};

/** The full operation, spelled out: which paths go away, which survives.
 * The summary alone truncates on long paths — a reviewer deciding a merge
 * must see both sides, so ops with a target always render this detail. */
function operationDetail(p: Proposal): string | undefined {
  if (p.target_paths.length === 0) return undefined;
  if (p.op === "move") {
    // A move keeps every page — nothing is retired. Folder paths in
    // source_paths are the chain being flattened; the shortest one is the
    // destination the pages move up into.
    const pages = p.source_paths.filter((s) => s.endsWith(".md"));
    const folders = p.source_paths.filter((s) => !s.endsWith(".md"));
    const dest =
      folders.length > 0
        ? folders.reduce((a, b) => (b.length < a.length ? b : a))
        : (p.target_paths[0].split("/").slice(0, -1).join("/") ?? "");
    return `Moves ${pages.length} page${pages.length === 1 ? "" : "s"} into “${dest}” — links, permissions, and comments follow each page.`;
  }
  const retire = p.source_paths.map((s) => `“${s}”`).join(", ");
  const keep = p.target_paths.map((t) => `“${t}”`).join(", ");
  return `Keeps ${keep} — retires ${retire} (restorable from Trash; links to it will point at the surviving page).`;
}

function ProposalRow({
  proposal,
  onActioned,
  actions,
}: {
  proposal: Proposal;
  onActioned: () => void;
  /** Shared registry the footer's batch buttons dispatch through. */
  actions: Map<number, (kind: RowAction) => void>;
}) {
  const [outcome, setOutcome] = useState<Outcome>("idle");
  const [error, setError] = useState<string | null>(null);
  const alive = useRef(true);
  useEffect(() => () => void (alive.current = false), []);

  // Once a row has shown its terminal outcome, briefly leave it up, then refresh
  // the list so it drops out (it has left `pending`). `onActioned` is SWR's
  // stable mutate, so this effect doesn't churn.
  useEffect(() => {
    if (
      outcome !== "applied" &&
      outcome !== "stale" &&
      outcome !== "rejected" &&
      outcome !== "dismissed"
    ) {
      return;
    }
    const t = setTimeout(() => {
      if (alive.current) onActioned();
    }, 4000);
    return () => clearTimeout(t);
  }, [outcome, onActioned]);

  // Execution runs async on the automanage_nearline worker. Show "Applying…"
  // and poll for the terminal status. A transient fetch error must NOT abort
  // the poll (that would strand the row), and if it doesn't settle within the
  // budget we drop back to the list — the proposal has left `pending`, so a
  // refresh removes the row rather than freezing it at "Applying…".
  async function pollApplied() {
    setOutcome("applying");
    for (let i = 0; i < 20; i++) {
      await new Promise((r) => setTimeout(r, Math.min(1000 + i * 250, 3000)));
      if (!alive.current) return;
      try {
        const fresh = await fetchProposal(proposal.id);
        if (fresh.status === "applied") return setOutcome("applied");
        if (fresh.status === "stale") return setOutcome("stale");
        // still pending/approved → keep polling
      } catch {
        // transient failure (or the row was purged) — keep polling
      }
    }
    if (alive.current) onActioned(); // didn't settle in-window → refresh the list
  }

  async function act(kind: RowAction) {
    if (outcome !== "idle" && outcome !== "error") return; // already actioned
    setOutcome("working");
    setError(null);
    try {
      if (kind === "approve") {
        await approveProposal(proposal.id);
        if (alive.current) await pollApplied();
      } else if (kind === "reject") {
        await rejectProposal(proposal.id);
        if (alive.current) setOutcome("rejected");
      } else {
        await dismissProposal(proposal.id);
        if (alive.current) setOutcome("dismissed");
      }
    } catch (e) {
      if (!alive.current) return;
      // 409 → someone else already actioned it; drop it from the list.
      if (e instanceof ApiError && e.status === 409) return onActioned();
      setOutcome("error");
      setError(e instanceof Error ? e.message : "failed");
    }
  }

  // Keep the registry pointing at this render's `act` (it closes over
  // `outcome`); drop the entry when the row unmounts.
  useEffect(() => {
    actions.set(proposal.id, (kind) => void act(kind));
    return () => void actions.delete(proposal.id);
  });

  return (
    <ContentAction
      sizePreset="main-ui"
      variant="section"
      title={proposal.summary}
      titleMaxLines={2}
      auxIcon={outcome === "error" ? "error" : undefined}
      description={
        outcome === "error"
          ? (error ?? undefined)
          : // Every row names its location, like the mock's path chip — ops
            // with a target get the full spelled-out operation instead.
            (operationDetail(proposal) ?? proposal.source_paths[0])
      }
      rightChildren={
        TERMINAL.includes(outcome) ? (
          <Text
            font="secondary-body"
            color={outcome === "applied" ? "status-success-05" : "text-03"}
          >
            {STATUS_LABEL[outcome]}
          </Text>
        ) : (
          <Section flexDirection="row" gap={0.25} alignItems="center">
            <Button
              icon={SvgSlash}
              size="sm"
              prominence="tertiary"
              disabled={outcome === "working"}
              tooltip="Reject — won't be suggested again"
              onClick={() => void act("reject")}
            />
            <Button
              icon={SvgCheckCircle}
              size="sm"
              prominence="tertiary"
              disabled={outcome === "working"}
              tooltip="Approve — applies this change"
              onClick={() => void act("approve")}
            />
          </Section>
        )
      }
    />
  );
}
