"""coedit doc pointers

Binds the live-session layer to document identity, ahead of the cutover that
moves document state to ``wiki_documents``:

- ``coedit_sessions.doc_id`` — the page's ``wiki_doc_ids`` id, stamped at
  open; backfilled here from the live registry row at each session's path.
- ``coedit_updates.doc_id`` — the document-keyed view of the update log,
  copied from the session's binding at write time; backfilled here from each
  row's session. Indexed on (doc_id, seq) for the document-keyed rebuild.
- ``wiki_documents.last_checkpoint_at`` — overdue-detection input for the
  post-cutover checkpoint scan; starts NULL.

Sessions whose path has no live registry row (trashed paths, never-read
pages) keep a NULL ``doc_id``, as do their updates — the same resolve-only
rule as the ``wiki_documents`` mirror.

Guarded with the inspector because ``0001_initial`` builds fresh databases
from the current models.

Revision ID: b6f3a1d8c2e7
Revises: c5d8e2f7a941
Create Date: 2026-08-04 00:00:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "b6f3a1d8c2e7"
down_revision: str | None = "c5d8e2f7a941"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    sessions_cols = {c["name"] for c in inspector.get_columns("coedit_sessions")}
    updates_cols = {c["name"] for c in inspector.get_columns("coedit_updates")}
    documents_cols = {c["name"] for c in inspector.get_columns("wiki_documents")}

    if "doc_id" not in sessions_cols:
        op.add_column("coedit_sessions", sa.Column("doc_id", sa.Text(), nullable=True))
        sessions = sa.table(
            "coedit_sessions",
            sa.column("path", sa.Text()),
            sa.column("doc_id", sa.Text()),
        )
        doc_ids = sa.table(
            "wiki_doc_ids",
            sa.column("id", sa.Text()),
            sa.column("path", sa.Text()),
            sa.column("deleted_at", sa.Text()),
        )
        live_id = (
            sa.select(doc_ids.c.id)
            .where(
                doc_ids.c.path == sessions.c.path,
                doc_ids.c.deleted_at.is_(None),
            )
            .scalar_subquery()
        )
        op.execute(sessions.update().values(doc_id=live_id))

    if "doc_id" not in updates_cols:
        op.add_column("coedit_updates", sa.Column("doc_id", sa.Text(), nullable=True))
        updates = sa.table(
            "coedit_updates",
            sa.column("session_id", sa.BigInteger()),
            sa.column("doc_id", sa.Text()),
        )
        sessions_for_updates = sa.table(
            "coedit_sessions",
            sa.column("id", sa.BigInteger()),
            sa.column("doc_id", sa.Text()),
        )
        session_doc = (
            sa.select(sessions_for_updates.c.doc_id)
            .where(sessions_for_updates.c.id == updates.c.session_id)
            .scalar_subquery()
        )
        op.execute(updates.update().values(doc_id=session_doc))
        op.create_index("idx_coedit_updates_doc_seq", "coedit_updates", ["doc_id", "seq"])

    if "last_checkpoint_at" not in documents_cols:
        op.add_column(
            "wiki_documents", sa.Column("last_checkpoint_at", sa.Text(), nullable=True)
        )


def downgrade() -> None:
    op.drop_column("wiki_documents", "last_checkpoint_at")
    op.drop_index("idx_coedit_updates_doc_seq", table_name="coedit_updates")
    op.drop_column("coedit_updates", "doc_id")
    op.drop_column("coedit_sessions", "doc_id")
