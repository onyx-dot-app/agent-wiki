"""Repo for the provenance ledger and its source ranges. One append-only row per
``(commit_sha, doc_path)`` in ``provenance_ledger`` records who produced a wiki
commit and, for ingestion, the source document; ``source_ranges`` maps the spans
an ingest commit changed to that document, anchored like a comment.

All provenance DB access lives here. Callers pass primitives and get ids or plain
dicts back. The service in ``app/wiki/provenance.py`` classifies the writer,
falls back to the git author, and maps to the pydantic read shapes on top.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models import ProvenanceLedger, SourceRange, User
from app.db.session import session
from app.models.wiki import ActorKind, SourceRangeStatus

# Ledger columns a reader needs; returned as a plain dict so callers don't hold
# an ORM row past the session.
_LEDGER_COLUMNS = (
    "id",
    "commit_sha",
    "doc_path",
    "actor_kind",
    "user_id",
    "agent_name",
    "agent_session_id",
    "source_document_id",
    "source_type",
    "source_url",
    "source_title",
    "source_snippet",
    "created_at",
)


def _ledger_dict(row: ProvenanceLedger) -> dict[str, Any]:
    return {c: getattr(row, c) for c in _LEDGER_COLUMNS}


def user_kind(user_id: str) -> str | None:
    """The ``users.kind`` for a bound user, for classifying their write."""
    with session() as s:
        return s.scalar(select(User.kind).where(User.id == user_id))


def insert_ledger(
    *,
    commit_sha: str,
    doc_path: str,
    actor_kind: str,
    user_id: str | None,
    agent_name: str | None,
    agent_session_id: str | None,
    source_values: dict[str, Any],
) -> int | None:
    """Insert one ledger row, idempotent on ``(commit_sha, doc_path)``. Returns
    the row id whether it was inserted now or already present, so a caller can
    attach source ranges to it."""
    with session() as s:
        inserted = s.execute(
            pg_insert(ProvenanceLedger)
            .values(
                commit_sha=commit_sha,
                doc_path=doc_path,
                actor_kind=actor_kind,
                user_id=user_id,
                agent_name=agent_name,
                agent_session_id=agent_session_id,
                **source_values,
            )
            .on_conflict_do_nothing(index_elements=["commit_sha", "doc_path"])
            .returning(ProvenanceLedger.id)
        ).scalar_one_or_none()
        if inserted is not None:
            return inserted
        return s.scalar(
            select(ProvenanceLedger.id).where(
                ProvenanceLedger.commit_sha == commit_sha,
                ProvenanceLedger.doc_path == doc_path,
            )
        )


def rename_doc(old_path: str, new_path: str) -> None:
    """Re-key ledger rows and source ranges from a moved page's old path to its
    new one, so readers that key on ``doc_path`` still reach commits made under
    the old name."""
    with session() as s:
        for model in (ProvenanceLedger, SourceRange):
            s.execute(update(model).where(model.doc_path == old_path).values(doc_path=new_path))


def delete_for_doc(doc_path: str) -> None:
    """Delete every ledger row for a path. Source ranges cascade via their FK.

    For a page that left ``.md`` space, where there is no new doc to carry its
    provenance onto and a row left behind would misattribute whatever later
    takes the old path.
    """
    with session() as s:
        s.execute(delete(ProvenanceLedger).where(ProvenanceLedger.doc_path == doc_path))


def ledger_rows_for_commits(commit_shas: list[str], doc_path: str) -> list[dict[str, Any]]:
    """Ledger rows for the given commits on a path, each with the owner's display
    name (name or email) under ``owner_display``. Batched to one query."""
    if not commit_shas:
        return []
    owner_display = func.coalesce(User.name, User.email)
    with session() as s:
        rows = s.execute(
            select(ProvenanceLedger, owner_display.label("owner_display"))
            .join(User, User.id == ProvenanceLedger.user_id, isouter=True)
            .where(
                ProvenanceLedger.doc_path == doc_path,
                ProvenanceLedger.commit_sha.in_(commit_shas),
            )
        ).all()
    return [{**_ledger_dict(row), "owner_display": display} for row, display in rows]


def ingestion_source_rows(doc_path: str, limit: int) -> list[dict[str, Any]]:
    """Ingestion ledger rows still credited to a page, newest first, capped at
    ``limit`` — the raw rows behind the Sources list, before dedup. A row is kept
    while it has a live span or has captured no spans at all, and drops only once
    every span it captured is retired."""
    live_span = (
        select(SourceRange.id)
        .where(
            SourceRange.provenance_id == ProvenanceLedger.id,
            SourceRange.status == SourceRangeStatus.LIVE.value,
        )
        .exists()
    )
    any_span = (
        select(SourceRange.id).where(SourceRange.provenance_id == ProvenanceLedger.id).exists()
    )
    with session() as s:
        rows = (
            s.execute(
                select(ProvenanceLedger)
                .where(
                    ProvenanceLedger.doc_path == doc_path,
                    ProvenanceLedger.actor_kind == ActorKind.INGESTION.value,
                    or_(live_span, ~any_span),
                )
                .order_by(ProvenanceLedger.id.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
    return [_ledger_dict(r) for r in rows]


def live_spans_for_doc(doc_path: str, anchor_sha: str) -> list[dict[str, Any]]:
    """Live source ranges on a page anchored at ``anchor_sha``, with the source
    facts of the ingest that produced each, ordered by start offset. Callers
    pass HEAD so a span whose anchor lags (an unremapped leftover) is omitted
    rather than returned with offsets against an older body."""
    with session() as s:
        rows = s.execute(
            select(
                SourceRange.start_offset,
                SourceRange.end_offset,
                ProvenanceLedger.source_document_id,
                ProvenanceLedger.source_type,
                ProvenanceLedger.source_url,
                ProvenanceLedger.source_title,
                ProvenanceLedger.source_snippet,
            )
            .join(ProvenanceLedger, ProvenanceLedger.id == SourceRange.provenance_id)
            .where(
                SourceRange.doc_path == doc_path,
                SourceRange.status == SourceRangeStatus.LIVE.value,
                SourceRange.anchor_sha == anchor_sha,
            )
            .order_by(SourceRange.start_offset)
        ).mappings().all()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Source ranges                                                                 #
# --------------------------------------------------------------------------- #


def replace_source_ranges(provenance_id: int, rows: list[dict[str, Any]]) -> None:
    """Replace the source ranges linked to a ledger row with ``rows`` (each a
    span dict). Idempotent per ledger row: existing ranges are cleared first, so
    re-processing a commit does not duplicate them."""
    with session() as s:
        s.execute(delete(SourceRange).where(SourceRange.provenance_id == provenance_id))
        if rows:
            s.execute(insert(SourceRange), [{"provenance_id": provenance_id, **r} for r in rows])


def live_ranges_needing_remap(doc_path: str, head_sha: str) -> list[dict[str, Any]]:
    """Live source ranges on a page whose anchor is not at HEAD, for the remap
    pass to re-derive against the current body."""
    with session() as s:
        rows = s.scalars(
            select(SourceRange).where(
                SourceRange.doc_path == doc_path,
                SourceRange.status == SourceRangeStatus.LIVE.value,
                SourceRange.anchor_sha != head_sha,
            )
        ).all()
        return [
            {
                "id": r.id,
                "anchor_sha": r.anchor_sha,
                "start_offset": r.start_offset,
                "end_offset": r.end_offset,
            }
            for r in rows
        ]


def apply_range_remap(
    range_id: int, *, start_offset: int, end_offset: int, quoted_text: str, anchor_sha: str
) -> None:
    """Advance a source range's anchor to a new commit after a successful remap."""
    with session() as s:
        r = s.get(SourceRange, range_id)
        if r is None:
            return
        r.start_offset = start_offset
        r.end_offset = end_offset
        r.quoted_text = quoted_text
        r.anchor_sha = anchor_sha


def retire_range(range_id: int) -> None:
    """Mark a source range retired: its span was rewritten, so it no longer
    points at content its source produced. The row and its span stay for history,
    but a source drops off a page's list once none of its spans are live."""
    with session() as s:
        r = s.get(SourceRange, range_id)
        if r is None:
            return
        r.status = SourceRangeStatus.RETIRED.value
