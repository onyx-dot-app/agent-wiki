"""initial — extensions, tables, pgmq queues.

Revision ID: 0001
Revises:
Create Date: 2026-05-08

This is the bootstrap migration for the Postgres cutover. Rather than
hand-listing every ``op.create_table`` call (which would drift the
moment the next dev edits ``app/db/models.py``), it calls
``Base.metadata.create_all(connection)`` to materialize every model
declared at the time of execution. From the *next* migration forward
we use ``alembic revision --autogenerate`` to produce explicit
``op.alter_table`` / ``op.add_column`` diffs.

Side effects beyond ORM table creation: the two extensions
(``pg_textsearch`` for BM25 search, ``pgmq`` for the task queue) and
the three pgmq queues. All idempotent so re-running against a
partially-set-up DB is safe.
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_PGMQ_QUEUES = ("documents", "triggers", "wiki_bm25")


def upgrade() -> None:
    bind = op.get_bind()

    # Extensions first — ``DocumentFts`` declares an index ``USING bm25``
    # that needs ``pg_textsearch`` registered, and the pgmq queue creation
    # below needs the ``pgmq`` schema to exist.
    bind.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_textsearch"))
    bind.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgmq"))

    # Materialize every ORM-declared table. Imported inside the function
    # so test fixtures that swap modules don't bind to a stale ``Base``.
    from app.db.models import Base
    Base.metadata.create_all(bind)

    # Create the three pgmq queues. ``pgmq.create`` errors if a queue
    # already exists; wrap each in a savepoint so the next iteration can
    # continue cleanly on rerun.
    for q in _PGMQ_QUEUES:
        try:
            with bind.begin_nested():
                bind.execute(sa.text("SELECT pgmq.create(:q)"), {"q": q})
        except Exception:
            # Already exists — fine.
            pass


def downgrade() -> None:
    # Pre-ship migration; we don't support rolling back the bootstrap.
    # When the schema becomes worth preserving, replace this with real
    # ``op.drop_table`` / ``DROP EXTENSION`` calls.
    raise NotImplementedError(
        "0001 is the bootstrap migration; downgrading would drop the entire schema."
    )
