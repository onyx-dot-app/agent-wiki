"""Per-page CRDT document rows (``wiki_documents``) — dual-write phase.

One row per page holding the page's Yjs document state, keyed by the page's
``wiki_doc_ids`` id (see the model docstring in ``app/db/models.py``).
Opens attach from here (``_seed_snapshot_sync`` transplants the row's
snapshot as the new session's seq-0 state); the ``mirror_*`` functions keep
each row in lockstep with its page's session snapshot state, and
``advance_offline`` folds out-of-band commits in while no session is open.

Keying by id is what removes the move hook: renames re-key the *registry*
(``doc_ids.on_path_moved``) and never touch a document row, so a missed
re-key can't strand one. The registry is also why the delete/trash hook
resolves ids *before* ``doc_ids`` tombstones them — see ``on_pages_deleted``
and its call sites in ``app/wiki/notify.py``.

The mirror **resolves ids, never mints them**. Every page a session can
exist for already has a live registry row (``GET /wiki/file`` lazily mints
on read, page creation mints in the lifecycle hook), so a failed resolution
means the path is *transiently wrong*, not the page unknown — the window
during a move where the registry is re-keyed but the session row isn't yet.
Minting there would stamp a phantom live id (and a stray document row) onto
a path the page just left. Instead the mirror write is skipped, and the
session re-key itself repairs it: ``coedit.on_path_moved`` re-mirrors each
re-keyed page's newest session snapshot (``mirror_session_state``) in the
same transaction, so a skipped write is restored by the move fan-out that
caused it — not left waiting on a further edit.

Two kinds of entry point, split by transaction ownership:

- ``mirror_seed`` / ``mirror_checkpoint`` / ``mirror_base_sha`` take the
  caller's open ORM ``Session`` and run inside *its* transaction — the
  mirror must commit or roll back atomically with the session-row write it
  shadows, or the two stores could disagree about which snapshot exists.
  Called only from ``app/wiki/coedit.py``.
- ``on_pages_deleted`` / ``get`` open their own session per call, like every
  other repo.

The mirror *follows* the session, reseed included: while sessions own
document state, whatever snapshot the live session carries is the page's
document, so ``mirror_seed`` overwrites any existing row. Seed-once
semantics begin at cutover.
"""

from __future__ import annotations

import logging
import zlib
from datetime import datetime, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models import CoeditSession, WikiDocument
from app.db.session import advisory_xact_lock, session
from app.wiki import doc_ids

log = logging.getLogger(__name__)


def _now_iso() -> str:
    # Same format as ``coedit._iso`` — these rows shadow session rows, so
    # their timestamps should read the same way.
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _row_dict(row: WikiDocument) -> dict[str, object]:
    return {
        "doc_id": row.doc_id,
        "ydoc_snapshot": row.ydoc_snapshot,
        "ydoc_snapshot_seq": row.ydoc_snapshot_seq,
        "ydoc_snapshot_body": row.ydoc_snapshot_body,
        "ydoc_seq": row.ydoc_seq,
        "base_sha": row.base_sha,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def mirror_seed(
    s: Session, path: str, *, snapshot: bytes, body: str, base_sha: str | None
) -> None:
    """Mirror a session's initial snapshot: upsert the page's document row at
    seq 0. Overwrites an existing row — a fresh seed means the session layer
    just minted a new lineage for this page, and while sessions own document
    state the mirror's job is to record whichever lineage is live, not to
    defend the old one.
    """
    _upsert(s, path, snapshot=snapshot, seq=0, body=body, base_sha=base_sha)


def mirror_checkpoint(
    s: Session, path: str, *, seq: int, snapshot: bytes, body: str, base_sha: str
) -> None:
    """Mirror a checkpoint's advanced snapshot state onto the page's document
    row. Upsert, not update: the row can be missing for a session that
    predates the table (opened before the migration ran), and the checkpoint
    state is complete in itself — snapshot, body, and base all move together.
    """
    _upsert(s, path, snapshot=snapshot, seq=seq, body=body, base_sha=base_sha)


def _upsert(
    s: Session, path: str, *, snapshot: bytes, seq: int, body: str, base_sha: str | None
) -> None:
    doc_id = doc_ids.id_for_path_in(s, path)
    if doc_id is None:
        # Mid-move window (registry re-keyed, session row not yet) — skip
        # rather than mint a phantom id at the old path; the move's session
        # re-key re-mirrors the row (see the module docstring).
        log.info("wiki_documents: no live doc id at %r; mirror write skipped", path)
        return
    now = _now_iso()
    stmt = pg_insert(WikiDocument).values(
        doc_id=doc_id,
        ydoc_snapshot=snapshot,
        ydoc_snapshot_seq=seq,
        ydoc_snapshot_body=body,
        ydoc_seq=seq,
        base_sha=base_sha,
        created_at=now,
        updated_at=now,
    )
    s.execute(
        stmt.on_conflict_do_update(
            index_elements=[WikiDocument.doc_id],
            set_={
                "ydoc_snapshot": stmt.excluded.ydoc_snapshot,
                "ydoc_snapshot_seq": stmt.excluded.ydoc_snapshot_seq,
                "ydoc_snapshot_body": stmt.excluded.ydoc_snapshot_body,
                "ydoc_seq": stmt.excluded.ydoc_seq,
                "base_sha": stmt.excluded.base_sha,
                "updated_at": stmt.excluded.updated_at,
            },
        )
    )


def mirror_session_state(
    s: Session, path: str, *, seq: int, snapshot: bytes, body: str, base_sha: str | None
) -> None:
    """Re-mirror a session's snapshot state wholesale — the move re-key's
    repair for the window where a checkpoint resolved the page's old path,
    found no live id, and skipped its mirror write (see
    ``coedit.on_path_moved``). Runs after the registry re-key, so ``path``
    is the page's live location and resolves its real id.
    """
    _upsert(s, path, snapshot=snapshot, seq=seq, body=body, base_sha=base_sha)


def mirror_base_sha(s: Session, path: str, base_sha: str) -> None:
    """Mirror a rebase's merge-base move (``coedit.set_base_sha``). Update-if-
    exists only: a NOOP rebase can land on a session whose snapshot hasn't
    been seeded yet, and a document row can't exist before its snapshot does
    — so no mint either.
    """
    doc_id = doc_ids.id_for_path_in(s, path)
    if doc_id is None:
        return
    s.execute(
        update(WikiDocument)
        .where(WikiDocument.doc_id == doc_id)
        .values(base_sha=base_sha, updated_at=_now_iso())
        .execution_options(synchronize_session=False)
    )


# Distinct from coedit's checkpoint-lock namespace (0xC0ED): this one keys
# on the *document*, serializing the attach read+transplant against the
# offline fold.
_ATTACH_LOCK_NS = 0xA77A


def attach_lock(s: Session, doc_id: str) -> None:
    """Transaction-scoped advisory lock on the page's document.

    Held by both writers whose interleaving could fork row and session onto
    different snapshots: ``advance_offline`` (offline fold: active-session
    check + row write) and ``coedit.transplant_from_document`` (attach: row
    read + session write). Under the lock, a transplant either waits out an
    in-flight fold and reads the advanced row, or commits first — in which
    case the fold's own active-session re-check sees the session and yields.
    """
    advisory_xact_lock(
        s, (_ATTACH_LOCK_NS << 32) | (zlib.crc32(doc_id.encode()) & 0xFFFFFFFF)
    )


def advance_offline(
    path: str,
    *,
    snapshot: bytes,
    body: str,
    base_sha: str,
    expected_base_sha: str | None,
) -> bool:
    """Advance a row folded outside any session (``rebase_document_row``).

    Compare-and-swap on ``base_sha``: a checkpoint or move re-mirror that
    landed since the caller read the row wins, and this write reports False
    instead of clobbering the newer state. Seqs stay untouched — the fold is
    not a logged update, and a transplant adopts the snapshot at seq 0
    regardless.

    The active-session re-check runs inside this same transaction, not just
    in the caller: a session opening between the caller's check and this
    write would transplant the pre-advance row, and the write landing anyway
    would fork row and session onto different snapshots of the lineage until
    the session's next checkpoint overwrote it. Checked here, either this
    write commits before the open (the transplant reads the advanced row) or
    the open wins and this write yields.
    """
    with session() as s:
        doc_id = doc_ids.id_for_path_in(s, path)
        if doc_id is None:
            return False
        attach_lock(s, doc_id)
        # "active" matches the coedit_sessions status constraint; the enum
        # lives in app.wiki.coedit, which imports this module.
        active = s.scalar(
            select(CoeditSession.id).where(
                CoeditSession.path == path,
                CoeditSession.status == "active",
            )
        )
        if active is not None:
            return False
        updated = s.scalars(
            update(WikiDocument)
            .where(
                WikiDocument.doc_id == doc_id,
                WikiDocument.base_sha.is_(None)
                if expected_base_sha is None
                else WikiDocument.base_sha == expected_base_sha,
            )
            .values(
                ydoc_snapshot=snapshot,
                ydoc_snapshot_body=body,
                base_sha=base_sha,
                updated_at=_now_iso(),
            )
            .returning(WikiDocument.doc_id)
        ).one_or_none()
        return updated is not None


def on_pages_deleted(paths: list[str]) -> None:
    """Drop the pages' document rows on delete or trash. No tombstone: the
    row is operational live-editing state, and page history lives in git.

    Must run while the registry rows at ``paths`` are still live — the ids
    are resolved here, and ``doc_ids.on_deleted`` tombstones them (see the
    ordering at the ``app/wiki/notify.py`` call sites). Dropping matters
    even though a tombstoned id already makes the row unreachable: a restore
    *re-binds* the same id (``doc_ids.on_restored``), and it must come back
    to no document row, reseeding a fresh lineage from HEAD — safe precisely
    because deletion means no client can still hold this document.
    """
    md_paths = [p for p in paths if p.endswith(".md")]
    if not md_paths:
        return
    with session() as s:
        ids = list(doc_ids.ids_for_paths_in(s, md_paths).values())
        if ids:
            s.execute(delete(WikiDocument).where(WikiDocument.doc_id.in_(ids)))


def get(path: str) -> dict[str, object] | None:
    """The document row for the live page at ``path``, or None."""
    with session() as s:
        doc_id = doc_ids.id_for_path_in(s, path)
        if doc_id is None:
            return None
        row = s.scalar(select(WikiDocument).where(WikiDocument.doc_id == doc_id))
        return None if row is None else _row_dict(row)
