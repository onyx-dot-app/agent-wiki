"""Change-proposal repo — the Wiki Auto Management proposal lifecycle.

A proposal is the typed record a detection run emits and a human (or delegation)
approves before anything touches the wiki: op kind, exact paths, the base
SHAs it was computed against, and an audience fingerprint. Approval binds to
these fields — execution re-validates and marks the proposal ``stale`` on
drift rather than acting on something nobody previewed. See the PRD
(``design/PRD: Wiki Auto Management.md``) and the engineering page.

Free functions over ``ChangeProposal``; each opens its own session and
returns plain dicts. Status changes go through conditional UPDATEs so a
concurrent transition can't be double-applied (the loser's guard matches no
row and returns ``False``).
"""
from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from sqlalchemy import select, update

from app.db.models import ChangeProposal
from app.db.session import execute_dml, session


class ProposalOp(str, Enum):
    """Single source of truth for ``change_proposals.op``; the DB CHECK
    constraint in ``app/db/models.py`` mirrors these."""

    MOVE = "move"
    RENAME = "rename"
    MERGE = "merge"
    SPLIT = "split"
    CREATE_FOLDER = "create_folder"
    DELETE_EMPTY_FOLDER = "delete_empty_folder"
    DELETE_PAGE = "delete_page"


class ProposalStatus(str, Enum):
    """Lifecycle: ``pending → approved → applied``, with ``rejected`` /
    ``expired`` / ``stale`` as terminal exits. The DB CHECK mirrors these."""

    PENDING = "pending"
    APPROVED = "approved"
    APPLIED = "applied"
    REJECTED = "rejected"
    EXPIRED = "expired"
    STALE = "stale"


class ProposalCreatedVia(str, Enum):
    """How the proposal was created — the PRD's two entry points ("two entry
    points feed one pipeline"). The DB CHECK mirrors these; today both are AI
    detection paths — a human-initiated ``manual`` value is a plausible later
    addition (one-line CHECK widening)."""

    SWEEP = "sweep"
    ON_CREATE = "on_create"


def _now() -> str:
    """UTC timestamp matching the ``YYYY-MM-DD HH:MM:SS`` column format."""
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _to_dict(row: ChangeProposal) -> dict[str, Any]:
    return {
        "id": row.id,
        "op": row.op,
        "status": row.status,
        "source_paths": row.source_paths,
        "target_paths": row.target_paths,
        "base_shas": row.base_shas,
        "acl_fingerprint_before": row.acl_fingerprint_before,
        "acl_fingerprint_after": row.acl_fingerprint_after,
        "proposed_bodies": row.proposed_bodies,
        "summary": row.summary,
        "instruction": row.instruction,
        "created_via": row.created_via,
        "detector": row.detector,
        "run_id": row.run_id,
        "acting_user_id": row.acting_user_id,
        "reviewed_by_user_id": row.reviewed_by_user_id,
        "doc_ids": row.doc_ids,
        "revive_count": row.revive_count,
        "last_emitted_at": row.last_emitted_at,
        "dedup_key": row.dedup_key,
        "status_reason": row.status_reason,
        "applied_sha": row.applied_sha,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "expires_at": row.expires_at,
    }


def create(
    *,
    op: ProposalOp,
    source_paths: list[str],
    target_paths: list[str],
    base_shas: dict[str, str],
    summary: str,
    created_via: ProposalCreatedVia,
    detector: str | None = None,
    instruction: str | None = None,
    proposed_bodies: dict[str, str] | None = None,
    run_id: str | None = None,
    acting_user_id: str | None = None,
    acl_fingerprint_before: str | None = None,
    acl_fingerprint_after: str | None = None,
    expires_at: str | None = None,
    dedup_key: str | None = None,
    doc_ids: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Insert a ``pending`` proposal and return it.

    Arity varies by op: ``create_folder`` has no source (0 → 1) and
    ``delete_empty_folder`` no target (1 → 0); everything else needs both
    (merge is N → 1, split 1 → N). ``base_shas`` should cover every source
    path (the drift anchors execution re-validates against).

    Content-bearing ops (merge/split) should carry ``proposed_bodies`` — the
    exact markdown per target path that the approver previews and execution
    applies; regeneration only happens through the staleness path.
    """
    if not source_paths and op is not ProposalOp.CREATE_FOLDER:
        raise ValueError(f"source_paths required for op {op.value!r}")
    if not target_paths and op not in (
        ProposalOp.DELETE_EMPTY_FOLDER,
        ProposalOp.DELETE_PAGE,
    ):
        raise ValueError(f"target_paths required for op {op.value!r}")
    now = _now()
    with session() as s:
        row = ChangeProposal(
            op=op.value,
            status=ProposalStatus.PENDING.value,
            source_paths=source_paths,
            target_paths=target_paths,
            base_shas=base_shas,
            summary=summary,
            instruction=instruction,
            proposed_bodies=proposed_bodies,
            created_via=created_via.value,
            detector=detector,
            run_id=run_id,
            acting_user_id=acting_user_id,
            acl_fingerprint_before=acl_fingerprint_before,
            acl_fingerprint_after=acl_fingerprint_after,
            dedup_key=dedup_key,
            doc_ids=doc_ids,
            last_emitted_at=now,
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
        )
        s.add(row)
        s.flush()
        return _to_dict(row)


def get(proposal_id: int) -> dict[str, Any] | None:
    with session() as s:
        row = s.get(ChangeProposal, proposal_id)
        return _to_dict(row) if row is not None else None


def list_by_status(
    status: ProposalStatus, *, limit: int | None = 100
) -> list[dict[str, Any]]:
    """Proposals in ``status``, oldest first (queue order). ``limit=None``
    returns all — the pending-cleanups list uses that so its ACL filter runs
    over the whole set (a hard cap *before* filtering would silently hide a
    caller's readable proposals sitting past it)."""
    with session() as s:
        q = (
            select(ChangeProposal)
            .where(ChangeProposal.status == status.value)
            .order_by(ChangeProposal.created_at.asc(), ChangeProposal.id.asc())
        )
        if limit is not None:
            q = q.limit(limit)
        return [_to_dict(r) for r in s.scalars(q).all()]


def list_for_run(run_id: str) -> list[dict[str, Any]]:
    """Every proposal a detection run emitted (batched notifications)."""
    with session() as s:
        rows = s.scalars(
            select(ChangeProposal)
            .where(ChangeProposal.run_id == run_id)
            .order_by(ChangeProposal.id.asc())
        ).all()
        return [_to_dict(r) for r in rows]


def taken_dedupe_keys(statuses: tuple[ProposalStatus, ...]) -> set[str]:
    """Dedupe keys (``op`` + sorted source+target path-set) of every proposal
    currently in one of ``statuses`` — the detection runner's do-not-re-propose
    set. Pass ``(pending, approved, applied, rejected)`` to avoid re-emitting an
    in-flight, already-applied, or human-rejected change; ``expired``/``stale``
    are intentionally omitted so a timed-out or drifted proposal can recur.

    Key format matches ``ProposalDraft.dedupe_key`` in the detector seam."""
    with session() as s:
        # Project only the three columns the key needs — full rows would
        # deserialize proposed_bodies / base_shas we immediately discard.
        rows = s.execute(
            select(
                ChangeProposal.op,
                ChangeProposal.source_paths,
                ChangeProposal.target_paths,
            ).where(ChangeProposal.status.in_([st.value for st in statuses]))
        ).all()
        keys: set[str] = set()
        for op, source_paths, target_paths in rows:
            paths = ",".join(sorted(list(source_paths) + list(target_paths)))
            keys.add(f"{op}:{paths}")
        return keys


def _transition(
    proposal_id: int,
    *,
    from_statuses: tuple[ProposalStatus, ...],
    to: ProposalStatus,
    **fields: Any,
) -> bool:
    """Conditional status move. Returns False when the proposal isn't in one
    of ``from_statuses`` (already transitioned by someone else, or missing) —
    the concurrency guard for approve/apply racing expiry/staleness."""
    with session() as s:
        changed = execute_dml(
            s,
            update(ChangeProposal)
            .where(
                ChangeProposal.id == proposal_id,
                ChangeProposal.status.in_([f.value for f in from_statuses]),
            )
            .values(status=to.value, updated_at=_now(), **fields),
        )
        return changed > 0


def approve(proposal_id: int, *, user_id: str) -> bool:
    """``pending → approved`` by a human. The reviewer becomes the acting
    user — they must cover the whole operation, so execution runs as them."""
    return _transition(
        proposal_id,
        from_statuses=(ProposalStatus.PENDING,),
        to=ProposalStatus.APPROVED,
        reviewed_by_user_id=user_id,
        acting_user_id=user_id,
    )


def auto_approve(proposal_id: int, *, acting_user_id: str) -> bool:
    """``pending → approved`` without a human: every path the proposal touches
    is inside AI-managed scope (``ai_management_allowed`` effective, or the
    page is AI-owned), so no approval is required. ``reviewed_by_user_id``
    stays NULL — that's how digests and the queue UI tell "auto-applied
    (AI-managed scope)" from "approved by <person>". ``acting_user_id`` is the
    authorizing principal: the AI system user for AI-managed/AI-owned scopes,
    or the delegating owner under per-user delegation.

    The caller (the engine) is responsible for the scope check across *all*
    source and target paths — one path outside AI management drops the
    proposal to the human queue instead."""
    return _transition(
        proposal_id,
        from_statuses=(ProposalStatus.PENDING,),
        to=ProposalStatus.APPROVED,
        acting_user_id=acting_user_id,
    )


def reject(proposal_id: int, *, user_id: str, reason: str | None = None) -> bool:
    return _transition(
        proposal_id,
        from_statuses=(ProposalStatus.PENDING,),
        to=ProposalStatus.REJECTED,
        reviewed_by_user_id=user_id,
        status_reason=reason,
    )


def mark_applied(proposal_id: int, *, applied_sha: str) -> bool:
    """``approved → applied`` with the commit that executed it."""
    return _transition(
        proposal_id,
        from_statuses=(ProposalStatus.APPROVED,),
        to=ProposalStatus.APPLIED,
        applied_sha=applied_sha,
    )


def mark_stale(proposal_id: int, *, reason: str) -> bool:
    """``pending | approved → stale`` — the world drifted past the record
    (base SHA moved, ACLs changed, a path disappeared). Never executed."""
    return _transition(
        proposal_id,
        from_statuses=(ProposalStatus.PENDING, ProposalStatus.APPROVED),
        to=ProposalStatus.STALE,
        status_reason=reason,
    )


def expire_pending(*, older_than: str) -> int:
    """Expire pending proposals whose ``expires_at`` is set and past
    ``older_than`` (ISO-ish text compare, matching the column format).
    Returns how many expired — the queue-rot TTL from the PRD."""
    with session() as s:
        return execute_dml(
            s,
            update(ChangeProposal)
            .where(
                ChangeProposal.status == ProposalStatus.PENDING.value,
                ChangeProposal.expires_at.is_not(None),
                ChangeProposal.expires_at <= older_than,
            )
            .values(
                status=ProposalStatus.EXPIRED.value,
                status_reason="expired: unactioned past TTL",
                updated_at=_now(),
            ),
        )
