"use client";

import { useEffect, useRef, useState } from "react";

import { Button, MessageCard, Text } from "@onyx-ai/opal/components";
import { ContentAction, Section } from "@onyx-ai/opal/layouts";

import { ApiError } from "@/lib/api";
import {
  type Proposal,
  approveProposal,
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
}

/**
 * Path-2 review banner: surfaces the pending Auto Organize cleanup proposals
 * touching this page/folder to users who can act on them, with per-proposal
 * approve/reject. The list is write-scoped server-side (a read-only viewer gets
 * nothing), so we simply render nothing when it's empty. Execution is async, so
 * after an approve we poll the proposal to report the applied / went-stale
 * outcome rather than leaving it as a silent fire-and-forget.
 */
export function Path2ReviewBanner({ path, canWrite = true }: Props) {
  const { proposals, refresh } = useProposalsByPath(path, canWrite);
  if (proposals.length === 0) return null;

  const n = proposals.length;
  return (
    <div className="mb-3">
      <MessageCard
        variant="info"
        title={`Auto Organize suggests ${n} cleanup${n === 1 ? "" : "s"} here`}
        description="Approve a change to apply it, or reject to dismiss it (rejection is durable — it won't be suggested again)."
        bottomChildren={
          <Section flexDirection="column" gap={0.25} width="full">
            {proposals.map((p) => (
              <ProposalRow key={p.id} proposal={p} onActioned={refresh} />
            ))}
          </Section>
        }
      />
    </div>
  );
}

type Outcome =
  | "idle"
  | "working"
  | "applied"
  | "rejected"
  | "stale"
  | "applying"
  | "error";

const TERMINAL: Outcome[] = ["applied", "rejected", "stale", "applying"];

const STATUS_LABEL: Record<string, string> = {
  applied: "Applied ✓",
  rejected: "Rejected",
  stale: "Skipped — the page changed since this was proposed",
  applying: "Applying…",
};

/** The full operation, spelled out: which paths go away, which survives.
 * The summary alone truncates on long paths — a reviewer deciding a merge
 * must see both sides, so ops with a target always render this detail. */
function operationDetail(p: Proposal): string | undefined {
  if (p.target_paths.length === 0) return undefined;
  const retire = p.source_paths.map((s) => `“${s}”`).join(", ");
  const keep = p.target_paths.map((t) => `“${t}”`).join(", ");
  return `Keeps ${keep} — retires ${retire} (restorable from Trash; links to it will point at the surviving page).`;
}

function ProposalRow({
  proposal,
  onActioned,
}: {
  proposal: Proposal;
  onActioned: () => void;
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
      outcome !== "rejected"
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

  async function act(kind: "approve" | "reject") {
    setOutcome("working");
    setError(null);
    try {
      if (kind === "approve") {
        await approveProposal(proposal.id);
        if (alive.current) await pollApplied();
      } else {
        await rejectProposal(proposal.id);
        if (alive.current) setOutcome("rejected");
      }
    } catch (e) {
      if (!alive.current) return;
      // 409 → someone else already actioned it; drop it from the list.
      if (e instanceof ApiError && e.status === 409) return onActioned();
      setOutcome("error");
      setError(e instanceof Error ? e.message : "failed");
    }
  }

  return (
    <ContentAction
      sizePreset="main-ui"
      variant="section"
      title={proposal.summary}
      titleMaxLines={2}
      auxIcon={outcome === "error" ? "error" : undefined}
      description={
        outcome === "error" ? (error ?? undefined) : operationDetail(proposal)
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
          <Section flexDirection="row" gap={0.5} alignItems="center">
            <Button
              size="sm"
              prominence="secondary"
              disabled={outcome === "working"}
              onClick={() => void act("reject")}
            >
              Reject
            </Button>
            <Button
              size="sm"
              prominence="primary"
              disabled={outcome === "working"}
              onClick={() => void act("approve")}
            >
              {outcome === "working" ? "…" : "Approve"}
            </Button>
          </Section>
        )
      }
    />
  );
}
