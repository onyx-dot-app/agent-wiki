"use client";

import { useEffect, useRef, useState } from "react";

import { Button, MessageCard } from "@onyx-ai/opal/components";

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
        variant="warning"
        title={`Auto Organize suggests ${n} cleanup${n === 1 ? "" : "s"} here`}
        description="Approve a change to apply it, or reject to dismiss it (rejection is durable — it won't be suggested again)."
        bottomChildren={
          <ul className="m-0 flex list-none flex-col gap-2 pl-0">
            {proposals.map((p) => (
              <ProposalRow
                key={p.id}
                proposal={p}
                onActioned={() => void refresh()}
              />
            ))}
          </ul>
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

  // Execution runs async on the automanage_nearline worker; poll the proposal a
  // few times so we can report applied / went-stale instead of just "approved".
  async function pollOutcome() {
    for (let i = 0; i < 8; i++) {
      await new Promise((r) => setTimeout(r, 1000));
      if (!alive.current) return;
      try {
        const fresh = await fetchProposal(proposal.id);
        if (fresh.status === "applied") return setOutcome("applied");
        if (fresh.status === "stale") return setOutcome("stale");
      } catch {
        return; // gone (e.g. purged) — stop polling
      }
    }
    if (alive.current) setOutcome("applying"); // approved, still applying
  }

  async function act(kind: "approve" | "reject") {
    setOutcome("working");
    setError(null);
    try {
      if (kind === "approve") {
        await approveProposal(proposal.id);
        if (alive.current) await pollOutcome();
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

  const statusLabel: Record<string, string> = {
    applied: "Applied ✓",
    rejected: "Rejected",
    stale: "Skipped — the page changed since this was proposed",
    applying: "Applying…",
  };

  return (
    <li className="flex items-center justify-between gap-3 text-[13px] text-(--text-04)">
      <span className="min-w-0 truncate">{proposal.summary}</span>
      {TERMINAL.includes(outcome) ? (
        <span className="shrink-0 text-(--text-03)">
          {statusLabel[outcome]}
        </span>
      ) : (
        <span className="flex shrink-0 items-center gap-2">
          {outcome === "error" && error && (
            <span className="text-(--status-danger-05)">{error}</span>
          )}
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
        </span>
      )}
    </li>
  );
}
