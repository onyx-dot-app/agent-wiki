"""Layer-1 dedup — finding identity for Wiki Auto Management.

The dedup component answers one question for every detected draft: *have we
already asked this?* Its unit is the **finding**, identified by

    dedup_key = (detector, op, the touched pages by stable doc id,
                   the detector's premise)

and its content-free prefix (everything before the premise) is the
**cooldown scope** — the unit the post-rejection cooldown will quiet. The
cooldown itself is deliberately NOT here: dedup answers *"have we asked
this?"*; *"is now the right time to ask?"* is the selection step's question
(see the Dedup design page's pipeline), and it lands with the selection/
reconciliation work as an unselectable-until marking, not a dedup skip.

Doc ids (not paths) make identity rename/move-proof; paths that don't exist
yet — a rename's reserved destination — fall back to a case-folded path term
(case-folded so a reserved name can't dodge identity by casing). The premise
is contributed per detector (``ProposalDraft.premise``): the identity-side
twin of the ``validate()`` seam — only the authoring detector knows what
makes two occurrences "the same ask". Structural detectors contribute none.

One finding = one proposal row for life:

- a live row (pending/approved/applied) **carries** the finding — skip;
- a rejected row suppresses it **forever** — same ask, already declined;
- a stale/expired row **revives** — same row returns to pending, keeping
  its id (and with it, any notification/impression history);

Because keys are persisted, the strings inside them are **durable
identifiers**: detector names and ``ProposalOp`` values must never change
spelling — a rename would silently orphan identity history (rejected
findings re-proposed, live findings duplicated). If a rename is ever truly
needed, it ships with a data migration rewriting the stored keys.

Deliberately mechanical — deterministic keys, no LLM. The soft judgment
("is 'merge A+B+C' a variant of the rejected 'merge A+B'?") is the future
rejected-variant gate, which will slot in as one more decision branch here.
Full doctrine: the "Wiki Auto Management — Dedup" design page.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from enum import Enum

from pydantic import BaseModel, ConfigDict, model_validator

from app.wiki import change_proposals, doc_ids
from app.wiki.automanage.detectors.base import ProposalDraft

log = logging.getLogger(__name__)

_LIVE_STATUSES = frozenset(
    {
        change_proposals.ProposalStatus.PENDING.value,
        change_proposals.ProposalStatus.APPROVED.value,
        change_proposals.ProposalStatus.APPLIED.value,
    }
)

_REVIVABLE_STATUSES = frozenset(
    {
        change_proposals.ProposalStatus.STALE.value,
        change_proposals.ProposalStatus.EXPIRED.value,
    }
)


class DedupAction(str, Enum):
    """What the runner should do with a draft."""

    CREATE = "create"  # new finding — persist a new proposal row
    REVIVE = "revive"  # known finding, resolved row — return it to pending
    SKIP_LIVE = "skip_live"  # carried — a live row already represents it
    SKIP_REJECTED = "skip_rejected"  # same ask was declined — never again


class DedupDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    action: DedupAction
    dedup_key: str
    # The row that decided the outcome. Present on every action except
    # CREATE (enforced below) — a REVIVE without a row to revive is a bug
    # at construction, not something callers should re-check.
    existing_id: int | None = None

    @model_validator(mode="after")
    def _row_backed_actions_carry_the_row(self) -> "DedupDecision":
        if self.action is not DedupAction.CREATE and self.existing_id is None:
            raise ValueError(f"{self.action.value} decision requires existing_id")
        return self


class Deduper:
    """Per-run dedup: build once from the run's tracked paths, then `decide`
    each draft. Construction cost is one set; each decision is at most two
    indexed queries."""

    def __init__(self, tracked_paths: Iterable[str]) -> None:
        self._tracked = set(tracked_paths)

    def _exists(self, path: str) -> bool:
        """A page (tracked file) or a folder that holds tracked files."""
        if path in self._tracked:
            return True
        prefix = path + "/"
        return any(p.startswith(prefix) for p in self._tracked)

    def _id_term(self, path: str) -> str:
        """The identity term for one path: its stable doc id when the path
        exists (rename/move-proof), else a case-folded path literal for
        reserved not-yet-existing names."""
        if self._exists(path):
            return f"id:{doc_ids.get_or_mint(path)}"
        return f"path:{path.casefold()}"

    def resolution(self, draft: ProposalDraft) -> dict[str, str]:
        """``{doc id: path}`` for the draft's existing paths — the same
        emit-time snapshot the keys embed, in queryable form (stored on the
        row as ``doc_ids``). Keyed by the id — the stable term — with the
        emit-time path as its label; reserved not-yet-existing names are
        absent."""
        return {
            doc_ids.get_or_mint(p): p
            for p in dict.fromkeys(draft.source_paths + draft.target_paths)
            if self._exists(p)
        }

    def _cooldown_prefix(self, detector: str, draft: ProposalDraft) -> str:
        terms = sorted(
            {self._id_term(p) for p in draft.source_paths + draft.target_paths}
        )
        return f"{detector}|{draft.op.value}|{','.join(terms)}|"

    def dedup_key(self, detector: str, draft: ProposalDraft) -> str:
        return f"{self._cooldown_prefix(detector, draft)}{draft.premise or ''}"

    def decide(self, detector: str, draft: ProposalDraft) -> DedupDecision:
        prefix = self._cooldown_prefix(detector, draft)
        finding = f"{prefix}{draft.premise or ''}"

        row = change_proposals.get_by_dedup_key(finding)
        if row is not None:
            status = row["status"]
            if status in _LIVE_STATUSES:
                action = DedupAction.SKIP_LIVE
            elif status == change_proposals.ProposalStatus.REJECTED.value:
                action = DedupAction.SKIP_REJECTED
            elif status in _REVIVABLE_STATUSES:
                action = DedupAction.REVIVE
            else:  # a status this component doesn't know — fail closed
                log.warning(
                    "dedup: finding %s has row %s in unknown status %r — skipping",
                    finding,
                    row["id"],
                    status,
                )
                action = DedupAction.SKIP_LIVE
            return DedupDecision(
                action=action,
                dedup_key=finding,
                existing_id=row["id"],
            )

        return DedupDecision(action=DedupAction.CREATE, dedup_key=finding)
