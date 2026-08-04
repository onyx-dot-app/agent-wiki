"""wiki_documents

Adds ``wiki_documents`` — one row per page holding the page's CRDT (Yjs)
document state, keyed by the page's ``wiki_doc_ids`` id, so the Yjs lineage
becomes a property of the page rather than of whichever ``coedit_sessions``
row happens to exist (see the model docstring). Dual-write phase:
checkpoints and seeds mirror session snapshot state here; nothing reads the
table until cutover.

Backfills from ``coedit_sessions``: per path, the newest row carrying a
snapshot — the same row session reuse would adopt, so the backfilled lineage
is the one live clients can still hold — joined to the live registry row for
its id. A dirty session's snapshot is still its last-checkpointed document
state, so no cleanliness filter is needed. Paths with no live registry row
(and pages with no snapshot-bearing session) get no row; the dual-write
mirrors one in, minting the id if needed, at their next seed or checkpoint.

Guarded with the inspector because ``0001_initial`` builds fresh databases
from the current models.

Revision ID: c5d8e2f7a941
Revises: f2c9a41e7b06
Create Date: 2026-08-04 00:00:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "c5d8e2f7a941"
down_revision: str | None = "f2c9a41e7b06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW_TEXT_DEFAULT = sa.text(
    "to_char((now() AT TIME ZONE 'UTC'), 'YYYY-MM-DD HH24:MI:SS')"
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("wiki_documents"):
        return
    op.create_table(
        "wiki_documents",
        sa.Column("doc_id", sa.Text(), primary_key=True),
        sa.Column("ydoc_snapshot", sa.LargeBinary(), nullable=False),
        sa.Column(
            "ydoc_snapshot_seq",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "ydoc_snapshot_body",
            sa.Text(),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column(
            "ydoc_seq", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("base_sha", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.Text(), nullable=False, server_default=_NOW_TEXT_DEFAULT
        ),
        sa.Column(
            "updated_at", sa.Text(), nullable=False, server_default=_NOW_TEXT_DEFAULT
        ),
    )

    documents = sa.table(
        "wiki_documents",
        sa.column("doc_id", sa.Text()),
        sa.column("ydoc_snapshot", sa.LargeBinary()),
        sa.column("ydoc_snapshot_seq", sa.BigInteger()),
        sa.column("ydoc_snapshot_body", sa.Text()),
        sa.column("ydoc_seq", sa.BigInteger()),
        sa.column("base_sha", sa.Text()),
    )
    sessions = sa.table(
        "coedit_sessions",
        sa.column("id", sa.BigInteger()),
        sa.column("path", sa.Text()),
        sa.column("ydoc_snapshot", sa.LargeBinary()),
        sa.column("ydoc_snapshot_seq", sa.BigInteger()),
        sa.column("ydoc_snapshot_body", sa.Text()),
        sa.column("base_sha", sa.Text()),
    )
    doc_ids = sa.table(
        "wiki_doc_ids",
        sa.column("id", sa.Text()),
        sa.column("path", sa.Text()),
        sa.column("deleted_at", sa.Text()),
    )
    # Per path, the newest snapshot-bearing session (Postgres DISTINCT ON),
    # joined to the live registry row for its id. ``ydoc_seq`` is set to the
    # snapshot seq, not the session's live seq: in the dual-write phase the
    # update log stays session-keyed, so the document row represents the page
    # as of its last checkpoint.
    newest = (
        sa.select(
            sessions.c.path,
            sessions.c.ydoc_snapshot,
            sessions.c.ydoc_snapshot_seq,
            sessions.c.ydoc_snapshot_body,
            sessions.c.base_sha,
        )
        .where(sessions.c.ydoc_snapshot.isnot(None))
        .distinct(sessions.c.path)
        .order_by(sessions.c.path, sessions.c.id.desc())
        .subquery("newest")
    )
    joined = sa.select(
        doc_ids.c.id,
        newest.c.ydoc_snapshot,
        newest.c.ydoc_snapshot_seq,
        newest.c.ydoc_snapshot_body,
        newest.c.ydoc_snapshot_seq.label("ydoc_seq"),
        newest.c.base_sha,
    ).select_from(
        newest.join(
            doc_ids,
            sa.and_(
                doc_ids.c.path == newest.c.path,
                doc_ids.c.deleted_at.is_(None),
            ),
        )
    )
    op.execute(
        documents.insert().from_select(
            [
                "doc_id",
                "ydoc_snapshot",
                "ydoc_snapshot_seq",
                "ydoc_snapshot_body",
                "ydoc_seq",
                "base_sha",
            ],
            joined,
        )
    )


def downgrade() -> None:
    op.drop_table("wiki_documents")
