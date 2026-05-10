"""rename wiki_bm25 pgmq queue → lightweight_maintenance

The queue formerly known as ``wiki_bm25`` now hosts more than just BM25
reindex — agent-activity expiration cleanup moved off ``triggers_queue``
onto it, and the queue's placement rule (sub-second, no LLM, no external
HTTP, no wiki commits) was generalized. Renamed to
``lightweight_maintenance`` to match.

For fresh installs the bootstrap migration ``0001_initial`` already
creates the queue under the new name (``_PGMQ_QUEUES`` was updated in
the same change), so this migration's create is a no-op there. For
existing installs we create the new queue, drop the old one, and
abandon any pending messages: BM25 reindex is idempotent (the next
commit re-enqueues), and any in-flight expiration cleanups will be
re-scheduled at boot by ``schedule_all_pending_cleanups``.

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-10 00:08:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()

    # Create the new queue if not already present. Wrapped in a savepoint
    # because pgmq.create raises on duplicate.
    try:
        with bind.begin_nested():
            bind.execute(
                sa.text("SELECT pgmq.create(:q)"),
                {"q": "lightweight_maintenance"},
            )
    except Exception:
        pass

    # Drop the old queue. ``pgmq.drop_queue`` removes ``pgmq.q_<name>`` and
    # ``pgmq.a_<name>`` (archive). On fresh installs the queue was never
    # created (0001 was updated to use the new name), so the call is a no-op
    # — guarded by a savepoint either way.
    try:
        with bind.begin_nested():
            bind.execute(
                sa.text("SELECT pgmq.drop_queue(:q)"),
                {"q": "wiki_bm25"},
            )
    except Exception:
        pass


def downgrade() -> None:
    bind = op.get_bind()
    try:
        with bind.begin_nested():
            bind.execute(
                sa.text("SELECT pgmq.create(:q)"),
                {"q": "wiki_bm25"},
            )
    except Exception:
        pass
    try:
        with bind.begin_nested():
            bind.execute(
                sa.text("SELECT pgmq.drop_queue(:q)"),
                {"q": "lightweight_maintenance"},
            )
    except Exception:
        pass
