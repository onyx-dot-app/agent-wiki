"""Repo for the provenance ledger. One append-only row per
``(commit_sha, doc_path)`` in ``provenance_ledger`` recording who produced a
wiki commit and, for ingestion, the source document.

All provenance DB access lives here. Callers pass primitives and get ids or
plain dicts back. The service in ``app/wiki/provenance.py`` classifies the
writer, falls back to the git author, and maps to the pydantic read shapes on
top of these functions.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models import ProvenanceLedger, User
from app.db.session import session


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
    """Re-key ledger rows from a moved page's old path to its new one, so readers
    that key on ``doc_path`` still reach commits made under the old name."""
    with session() as s:
        s.execute(
            update(ProvenanceLedger)
            .where(ProvenanceLedger.doc_path == old_path)
            .values(doc_path=new_path)
        )


def delete_for_doc(doc_path: str) -> None:
    """Delete every ledger row for a path. Source ranges cascade via their FK.

    For a page that left ``.md`` space, where there is no new doc to carry its
    provenance onto and a row left behind would misattribute whatever later
    takes the old path.
    """
    with session() as s:
        s.execute(delete(ProvenanceLedger).where(ProvenanceLedger.doc_path == doc_path))
