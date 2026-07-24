"""Layer-1 dedup — finding identity for Wiki Auto Management.

The dedup component answers one question for every detected draft: *have we
already asked this?* Its unit is the **finding**, identified by

    finding_key = (detector, op, the touched pages by stable doc id,
                   the detector's premise)

and its content-free prefix, the **subject**

    subject_key = (detector, op, the touched pages by stable doc id)

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
- a rejected *subject* also quiets **new premises** for a cooldown window
  (``SUBJECT_COOLDOWN_DAYS``) — content churn on the same pages can't turn
  into weekly re-asks right after a human said no.

Deliberately mechanical — deterministic keys, no LLM. The soft judgment
("is 'merge A+B+C' a variant of the rejected 'merge A+B'?") is the future
rejected-variant gate, which will slot in as one more decision branch here.
Full doctrine: the "Wiki Auto Management — Dedup" design page.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from enum import Enum

from pydantic import BaseModel, ConfigDict

from app.wiki import change_proposals, doc_ids
from app.wiki.automanage.detectors.base import ProposalDraft

log = logging.getLogger(__name__)

# The post-rejection quiet window on a subject: long enough that periodic
# content syncs don't nag, short enough that a real re-duplication resurfaces
# within a quarter.
SUBJECT_COOLDOWN_DAYS = 30

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
    SKIP_COOLDOWN = "skip_cooldown"  # new premise, but the subject is quiet


class DedupDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    action: DedupAction
    finding_key: str
    subject_key: str
    # The row that decided a REVIVE / SKIP_* outcome, when one exists.
    existing_id: int | None = None


class Deduper:
    """Per-run dedup: build once from the run's tracked paths, then `decide`
    each draft. Construction cost is one set; each decision is at most two
    indexed queries."""

    def __init__(
        self,
        tracked_paths: Iterable[str],
        *,
        cooldown_days: int = SUBJECT_COOLDOWN_DAYS,
    ) -> None:
        self._tracked = set(tracked_paths)
        self._cooldown = timedelta(days=cooldown_days)

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

    def subject_key(self, detector: str, draft: ProposalDraft) -> str:
        terms = sorted(
            {self._id_term(p) for p in draft.source_paths + draft.target_paths}
        )
        return f"{detector}|{draft.op.value}|{','.join(terms)}"

    def finding_key(self, detector: str, draft: ProposalDraft) -> str:
        return f"{self.subject_key(detector, draft)}|{draft.premise or ''}"

    def decide(self, detector: str, draft: ProposalDraft) -> DedupDecision:
        subject = self.subject_key(detector, draft)
        finding = f"{subject}|{draft.premise or ''}"

        row = change_proposals.latest_by_finding_key(finding)
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
                finding_key=finding,
                subject_key=subject,
                existing_id=row["id"],
            )

        # New premise on a subject a human recently declined: stay quiet for
        # the cooldown window, then ask normally.
        rejected = change_proposals.latest_rejection_for_subject(subject)
        if rejected is not None:
            rejected_id, rejected_at = rejected
            try:
                ts = datetime.strptime(rejected_at, "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=UTC
                )
            except ValueError:
                ts = None
            if ts is not None and datetime.now(UTC) - ts < self._cooldown:
                return DedupDecision(
                    action=DedupAction.SKIP_COOLDOWN,
                    finding_key=finding,
                    subject_key=subject,
                    existing_id=rejected_id,
                )

        return DedupDecision(
            action=DedupAction.CREATE, finding_key=finding, subject_key=subject
        )
