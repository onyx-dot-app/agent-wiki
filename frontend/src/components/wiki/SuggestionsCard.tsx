"use client";

/** Auto-Organize suggestions card (mocks 2236:78296 / 2240:59533): pending
 * change proposals for a folder subtree with per-row approve/reject, bulk
 * actions, and the admin shortcut. Rows keep their outcome in place. Once
 * all rows are handled the list refreshes them away after a beat (mock
 * annotation: leave time for user confirmation). */
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { Button, Tag, Text } from "@onyx-ai/opal/components";
import { ContentAction, Section } from "@onyx-ai/opal/layouts";
import {
  SvgCheckCircle,
  SvgExpand,
  SvgFile,
  SvgFiles,
  SvgFold,
  SvgFolder,
  SvgArrowRight,
  SvgFolderIn,
  SvgFolderPlus,
  SvgSettings,
  SvgSidebar,
  SvgX,
} from "@onyx-ai/opal/icons";
import type { IconFunctionComponent } from "@onyx-ai/opal/types";
import { SvgFolderDashed, SvgSlashCircle } from "@/components/wiki/icons";
import { pathKind } from "@/lib/wiki/utils";
import { revalidateWiki } from "@/lib/wikiHref";

import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import {
  type Proposal,
  approveProposal,
  rejectProposal,
  fetchProposal,
  useProposalsByPath,
} from "@/lib/autoOrganize";

type Outcome =
  | "working"
  | "applying"
  | "applied"
  | "rejected"
  | "stale"
  | "error";

const HANDLED = new Set<Outcome>(["applied", "rejected", "stale"]);

/** Row glyph per the mock (2236:78296): merges show the scope being
 * merged (pages icon for a page, folder for a folder), empty-folder
 * deletes the dashed folder. Unknown ops fall back to the page icon. */
/** Path display for the row's tag, end-biased: CSS truncation cuts the
 * tail, but a path's tail is its payload — "Remove empty folder" needs the
 * folder's *name* visible, not its ancestry. Keeps the leaf whole (its own
 * tail trimmed only if it alone overflows), prepends as many leading
 * segments as fit, and marks anything skipped with an ellipsis segment.
 * The full path rides the tag's tooltip. */
function displayPath(path: string, budget = 28): string {
  if (path.length <= budget) return path;
  const segments = path.split("/");
  const leaf = segments[segments.length - 1]!;
  if (leaf.length + 2 >= budget) return `…/${leaf.slice(0, budget - 3)}…`;
  let head = "";
  for (const seg of segments.slice(0, -1)) {
    const candidate = head ? `${head}/${seg}` : seg;
    if (candidate.length + leaf.length + 3 > budget) break;
    head = candidate;
  }
  return head ? `${head}/…/${leaf}` : `…/${leaf}`;
}

/** The reason clause of a backend summary — the text after the " — " that
 * follows the quoted path (`Remove “x.md” — still identical to …`). The
 * first closing quote ends the path, so a quote inside the reason itself
 * doesn't confuse the split; summaries with no quoted path fall back to
 * the first separator. Null when the summary carries no reason. */
function proposalReason(summary: string): string | null {
  const afterPath = summary.indexOf("”");
  const i = summary.indexOf(" — ", afterPath >= 0 ? afterPath : 0);
  if (i < 0) return null;
  return summary.slice(i + 3).trim() || null;
}

/** Short, path-free row title — the folder tag right under it already
 * shows the path, so repeating it here (the backend summary quotes it in
 * full) only forced long rows to overflow. Unknown ops fall back to the
 * summary; the full summary always rides the row's hover tooltip. */
function opTitle(op: string, sourcePath: string, summary: string): string {
  const pageScope = pathKind(sourcePath) === "page";
  switch (op) {
    case "merge":
      return pageScope ? "Merge pages" : "Merge folders";
    case "split":
      return pageScope ? "Split page" : "Split folder";
    case "move":
      return pageScope ? "Move page" : "Move folder";
    case "rename":
      return pageScope ? "Rename page" : "Rename folder";
    case "create_folder":
      return "Create folder";
    case "delete_empty_folder":
      return "Remove empty folder";
    case "delete_page":
      return "Remove page";
    default:
      return summary;
  }
}

function opIcon(op: string, sourcePath: string): IconFunctionComponent {
  const pageScope = pathKind(sourcePath) === "page";
  switch (op) {
    case "merge":
    case "split":
      return pageScope ? SvgFiles : SvgFolder;
    case "move":
      return SvgFolderIn;
    case "rename":
      return pageScope ? SvgFile : SvgFolder;
    case "create_folder":
      return SvgFolderPlus;
    case "delete_empty_folder":
      return SvgFolderDashed;
    case "delete_page":
      return SvgFile;
    default:
      return SvgFile;
  }
}

interface SuggestionsCardProps {
  /** Folder (or page) scope the proposals are listed for. */
  path: string;
  /** Popover hosting: shows the open-in-side-panel action. */
  onOpenPanel?: () => void;
  /** Popover hosting: the header X. Hides this surface only, proposals
   * stay pending (mock annotation). */
  onClose?: () => void;
  /** Row click, with the proposal's source paths (mock: highlight folder). */
}

export function SuggestionsCard({
  path,
  onOpenPanel,
  onClose,
}: SuggestionsCardProps) {
  const router = useRouter();
  const { user } = useAuth();
  const { proposals, refresh } = useProposalsByPath(path);
  const [open, setOpen] = useState(true);
  const [outcomes, setOutcomes] = useState<Partial<Record<number, Outcome>>>(
    {},
  );
  // Rows the local user acted on, kept rendered until their outcome has
  // been shown — the proposals list now revalidates in the background
  // (another reviewer's action must reach this screen), and an acted row
  // leaves `pending` server-side immediately, so without this copy the
  // refresh would yank the row before its "Applied ✓" was ever visible.
  const [acted, setActed] = useState<Partial<Record<number, Proposal>>>({});
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => void (alive.current = false);
  }, []);

  const setOutcome = (id: number, o: Outcome) =>
    alive.current && setOutcomes((prev) => ({ ...prev, [id]: o }));

  // Execution runs async on the automanage worker: show "Applying…" and
  // poll to a terminal status. Transient failures keep polling.
  async function pollApplied(id: number) {
    setOutcome(id, "applying");
    for (let i = 0; i < 20; i++) {
      await new Promise((r) => setTimeout(r, Math.min(1000 + i * 250, 3000)));
      if (!alive.current) return;
      try {
        const fresh = await fetchProposal(id);
        if (fresh.status === "applied") {
          setOutcome(id, "applied");
          // The change just landed in the wiki (a move, a deletion): the
          // tree and listings this tab shows are now wrong — refresh them
          // rather than waiting out their poll interval.
          void revalidateWiki();
          return fadeActed(id);
        }
        if (fresh.status === "stale") {
          setOutcome(id, "stale");
          return fadeActed(id);
        }
      } catch {
        // transient failure (or the row was purged) — keep polling
      }
    }
    // Didn't settle in-window: the row has left `pending` server-side, so
    // a refresh reconciles it rather than freezing it at "Applying…".
    if (alive.current) void refresh();
  }

  // Terminal outcomes linger briefly so the human sees the result, then
  // the local copy drops; the background revalidation has removed the row
  // from the server list by then.
  function fadeActed(id: number) {
    setTimeout(() => {
      if (!alive.current) return;
      setActed((prev) => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
      setOutcomes((prev) => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
    }, 4000);
  }

  async function act(id: number, kind: "approve" | "reject") {
    const row = proposals.find((x) => x.id === id);
    if (row) setActed((prev) => ({ ...prev, [id]: row }));
    setOutcome(id, "working");
    try {
      if (kind === "approve") {
        await approveProposal(id);
        if (alive.current) void pollApplied(id);
      } else {
        await rejectProposal(id);
        setOutcome(id, "rejected");
        fadeActed(id);
      }
    } catch (e) {
      if (!alive.current) return;
      // 409: someone else already actioned it. Clear the local outcome so
      // the row cannot strand at working if the refresh fails, then sync
      // to the server's reality instead of guessing which outcome won.
      if (e instanceof ApiError && e.status === 409) {
        if (!alive.current) return;
        setOutcomes((prev) => {
          const next = { ...prev };
          delete next[id];
          return next;
        });
        setActed((prev) => {
          const next = { ...prev };
          delete next[id];
          return next;
        });
        void refresh();
        return;
      }
      setOutcome(id, "error");
    }
  }

  // Rows that disappear from the server list without a local outcome were
  // acted on by someone else — their change may have landed in the wiki,
  // so the structural views deserve the same immediate refresh the acting
  // client gives itself.
  const prevIds = useRef<Set<number>>(new Set());
  useEffect(() => {
    const ids = new Set(proposals.map((p) => p.id));
    const vanishedElsewhere = [...prevIds.current].some(
      (id) => !ids.has(id) && !outcomes[id],
    );
    prevIds.current = ids;
    if (vanishedElsewhere) void revalidateWiki();
    // outcomes intentionally unlisted: this reacts to the server list
    // changing, not to local action state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [proposals]);

  const actedOnly = Object.values(acted).filter(
    (a): a is Proposal => !!a && !proposals.some((p) => p.id === a.id),
  );
  const rows = [...proposals, ...actedOnly];

  const pendingIds = proposals
    .filter((p) => {
      const o = outcomes[p.id];
      return !o || o === "error";
    })
    .map((p) => p.id);

  // Bulk actions hit every untouched or failed row (mock annotation:
  // "apply to any that is not already selected"). Rows show their
  // outcomes in place.
  async function actAll(kind: "approve" | "reject") {
    for (const id of pendingIds) await act(id, kind);
  }

  // Once every row is handled, leave the outcomes visible briefly, then
  // refresh so the settled rows drop out (they have left `pending`).
  const allHandled =
    rows.length > 0 &&
    rows.every((p) => {
      const o = outcomes[p.id];
      return !!o && HANDLED.has(o);
    });
  useEffect(() => {
    if (!allHandled) return;
    const t = setTimeout(() => {
      if (alive.current) void refresh();
    }, 4000);
    return () => clearTimeout(t);
  }, [allHandled, refresh]);

  if (rows.length === 0) return null;
  const n = rows.length;

  return (
    <Section
      gap={0}
      justifyContent="start"
      alignItems="stretch"
      height="fit"
      padding={0.25}
      data-suggestions-card
      className="w-full rounded-(--radius-12) border border-(--status-info-02) bg-(--status-info-00)"
    >
      <Section gap={0} height="fit" alignItems="stretch" padding={0.25}>
        <ContentAction
          icon={SvgCheckCircle}
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
          {rows.map((p) => (
            <SuggestionRow
              key={p.id}
              proposal={p}
              outcome={outcomes[p.id]}
              onApprove={() => void act(p.id, "approve")}
              onDismiss={() => void act(p.id, "reject")}
              // Click opens the affected page/folder — the proposal may
              // target something nested well below the current view, and
              // inspecting a page is how you decide on its proposal. The
              // path URL redirects to the canonical id URL.
              onClick={() => {
                const target = p.source_paths[0] ?? p.target_paths[0];
                if (target) router.push(`/app/wiki/${target}`);
              }}
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
                  icon={SvgSidebar}
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
                rightIcon={SvgSlashCircle}
                disabled={pendingIds.length === 0}
                onClick={() => void actAll("reject")}
              >
                Reject All
              </Button>
              <Button
                size="sm"
                rightIcon={SvgCheckCircle}
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

const OUTCOME_LABEL: Partial<Record<Outcome, string>> = {
  applying: "Applying…",
  applied: "Applied",
  stale: "Skipped",
  rejected: "Rejected",
  error: "Failed",
};

interface SuggestionRowProps {
  proposal: Proposal;
  outcome?: Outcome;
  onApprove: () => void;
  onDismiss: () => void;
  onClick?: () => void;
}

/** One suggestion (mock Line, 56px): op icon, summary over the path chip,
 * reject/approve controls. Handled rows keep their place with the outcome
 * shown (strikethrough for rejected, check for applied). */
function SuggestionRow({
  proposal,
  outcome,
  onApprove,
  onDismiss,
  onClick,
}: SuggestionRowProps) {
  const Icon = opIcon(proposal.op, proposal.source_paths[0] ?? "");
  const struck = outcome === "rejected";
  const chipPath = proposal.source_paths[0] ?? proposal.target_paths[0] ?? "";
  // A proposal whose result lives somewhere else (a move's destination, a
  // rename's new name, a merge's survivor) shows that path as its own tag —
  // visibly, for everyone: the summary tooltip is hover-only, which keyboard
  // and touch users never see, and without the target two proposals of the
  // same kind are indistinguishable at approve time.
  const targetPath = proposal.target_paths[0] ?? "";
  const showTarget = targetPath !== "" && targetPath !== chipPath;
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
      aria-label={proposal.summary}
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
        {/* raw-ok: Text drops className, so the strikethrough/width state
            wraps it. w-full is what lets long titles wrap inside the card:
            this column aligns items start, which otherwise sizes children
            to their content width and lets them run under the action
            buttons and past the card edge. */}
        <span
          className={`block w-full min-w-0 ${struck ? "line-through opacity-60" : ""}`}
          title={proposal.summary}
        >
          <Text font="main-ui-action" color="text-04">
            {/* The reason rides the visible title — the "why" is what the
                approve decision needs, and hover-only detail never reaches
                keyboard or touch users. Long reasons wrap; nothing here
                truncates. */}
            {(() => {
              const title = opTitle(
                proposal.op,
                proposal.source_paths[0] ?? "",
                proposal.summary,
              );
              const reason =
                title === proposal.summary
                  ? null
                  : proposalReason(proposal.summary);
              return reason ? `${title} — ${reason}` : title;
            })()}
          </Text>
        </span>
        <span className="block w-full min-w-0">
          <Tag
            icon={SvgFolder}
            title={displayPath(chipPath)}
            tooltip={chipPath}
            color="gray"
            size="sm"
            truncate
          />
        </span>
        {showTarget && (
          <span className="block w-full min-w-0">
            <Tag
              icon={SvgArrowRight}
              title={displayPath(targetPath)}
              tooltip={targetPath}
              color="gray"
              size="sm"
              truncate
            />
          </span>
        )}
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
        {!outcome || outcome === "working" ? (
          <>
            <Button
              icon={SvgSlashCircle}
              size="sm"
              prominence="tertiary"
              tooltip="Reject"
              disabled={outcome === "working"}
              onClick={onDismiss}
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
        ) : outcome === "applied" || outcome === "applying" ? (
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
            {OUTCOME_LABEL[outcome]}
          </Text>
        )}
      </Section>
    </Section>
  );
}
