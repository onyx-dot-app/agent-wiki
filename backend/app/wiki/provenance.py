"""Provenance service. Classifies who produced each wiki commit and records it
through the ``app/db/provenance`` repo.

One append-only ledger row per ``(commit_sha, doc_path)`` is written from the
commit gateway (``commit_and_fan_out``) right after a commit lands, so human
saves, agent tool calls, and connector ingestion are all captured in one place.
Commits that bypass the gateway (seeding, ``.gitkeep`` creates, move and trash
commits) have no row, and readers fall back to the git author. This is the
structured record the byline, the Sources tab, and the source-to-pages reverse
lookup read from.
"""

from __future__ import annotations

from app.auth.users import UserKind
from app.db import provenance as repo
from app.models.wiki import ActorKind, WriteProvenance


def _actor_kind(
    user_kind: str | None, agent_name: str | None, source: WriteProvenance | None
) -> ActorKind:
    """Classify a write from the identity facts in scope at commit time.

    A ``WriteProvenance`` marks an ingestion write and wins over everything,
    even when the connector omitted the source document id. Otherwise a write
    with no bound user, or one made by a non-person principal, is ``system``,
    and a bound agent name separates an agent write from a plain human save.
    """
    if source is not None:
        return ActorKind.INGESTION
    if user_kind is None or user_kind == UserKind.SYSTEM.value:
        return ActorKind.SYSTEM
    if agent_name:
        return ActorKind.AGENT
    return ActorKind.HUMAN


def record(
    *,
    commit_sha: str,
    doc_path: str,
    user_id: str | None,
    agent_name: str | None,
    agent_session_id: str | None,
    source: WriteProvenance | None,
) -> int | None:
    """Record one provenance row for a landed commit and return its ledger id.

    Idempotent on ``(commit_sha, doc_path)`` via the repo: a re-delivered task
    reuses the existing row's id rather than erroring on the write path.
    """
    kind = repo.user_kind(user_id) if user_id else None
    return repo.insert_ledger(
        commit_sha=commit_sha,
        doc_path=doc_path,
        actor_kind=_actor_kind(kind, agent_name, source).value,
        user_id=user_id,
        agent_name=agent_name,
        agent_session_id=agent_session_id,
        source_values=source.model_dump() if source else {},
    )
