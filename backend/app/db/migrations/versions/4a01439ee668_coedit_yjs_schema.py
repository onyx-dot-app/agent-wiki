"""coedit_yjs_schema

Migrates ``coedit_sessions``/``coedit_ops`` from the OT (plain-text
buffer + range-op log) shape to a Yjs (CRDT doc + binary-update log) shape:
drops ``buffer_text`` in favor of ``ydoc_snapshot`` (bytea) +
``ydoc_snapshot_seq`` (the ``ydoc_seq`` those bytes represent — any reader
rebuilds a throwaway ``Doc`` from the snapshot plus every ``coedit_updates``
row with ``seq`` in ``(ydoc_snapshot_seq, ydoc_seq]``), renames
``version``/``checkpointed_version`` to ``ydoc_seq``/``ydoc_checkpointed_seq``
(same monotonic-watermark semantics, renamed for clarity), and replaces
``coedit_ops`` (JSONB range-op log) with ``coedit_updates`` (bytea Yjs-update
log). Also adds ``ydoc_snapshot_body`` (text) — the exact raw markdown
``ydoc_snapshot`` was seeded from, kept in lockstep with it everywhere it
advances (``set_initial_snapshot``/``advance_checkpoint`` in
``app/wiki/coedit.py``). A checkpoint's diff base has to come from *here*, not
a git read at ``base_sha``: a live-rebase fold has no git commit of its own, so
``base_sha`` can't always resolve to the right content the way a real commit ref
can. No
coexistence period — this app is pre-production, one clean cut, per
``app/db/models.py``'s ``CoeditSession``/``CoeditUpdate``. Guarded with the
inspector because ``0001_initial`` builds fresh databases from the current
models.

Revision ID: 4a01439ee668
Revises: c2e7a4d9f1b8
Create Date: 2026-07-27 00:00:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "4a01439ee668"
down_revision: str | None = "d7f2c8b4a1e6"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _rename_or_drop(table: str, old: str, new: str) -> None:
    """Rename ``old``→``new``, guarded for a case unique to this repo's
    migration chain: ``0001_initial`` materializes the table from *current*
    ``models.py`` via ``create_all``, so a fresh database already has ``new``
    from the very first migration — then a later, individually-guarded
    historical migration (written when ``models.py`` still had ``old``)
    re-adds ``old`` right back, since its own guard only checks for ``old``'s
    absence, with no idea ``new`` exists two revisions ahead of it. Confirmed
    by direct reproduction against a fresh database, not assumed: a plain
    ``old in cols`` guard collided with an already-present ``new``. When both
    exist, ``old`` is the redundant one (dropped); a rename only makes sense
    when exactly one of the two is present.
    """
    cols = _columns(table)
    if old in cols and new in cols:
        op.drop_column(table, old)
    elif old in cols:
        op.alter_column(table, old, new_column_name=new)


def upgrade() -> None:
    cols = _columns("coedit_sessions")
    if "buffer_text" in cols:
        op.drop_column("coedit_sessions", "buffer_text")
    if "ydoc_snapshot" not in cols:
        op.add_column(
            "coedit_sessions", sa.Column("ydoc_snapshot", sa.LargeBinary(), nullable=True)
        )
    if "ydoc_snapshot_seq" not in cols:
        op.add_column(
            "coedit_sessions",
            sa.Column(
                "ydoc_snapshot_seq", sa.BigInteger(), server_default=sa.text("0"), nullable=False
            ),
        )
    if "ydoc_snapshot_body" not in cols:
        op.add_column(
            "coedit_sessions",
            sa.Column(
                "ydoc_snapshot_body", sa.Text(), server_default=sa.text("''"), nullable=False
            ),
        )
    _rename_or_drop("coedit_sessions", "version", "ydoc_seq")
    _rename_or_drop("coedit_sessions", "checkpointed_version", "ydoc_checkpointed_seq")

    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("coedit_ops"):
        op.drop_table("coedit_ops")
    if not inspector.has_table("coedit_updates"):
        op.create_table(
            "coedit_updates",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column(
                "session_id",
                sa.BigInteger(),
                sa.ForeignKey("coedit_sessions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("seq", sa.BigInteger(), nullable=False),
            # Nullable — a server-produced update (a live-rebase fold) has no
            # human author. The OT-era coedit_ops column it replaces was NOT
            # NULL because every op came from a client.
            sa.Column(
                "author_user_id",
                sa.Text(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column("client_id", sa.Text(), nullable=True),
            sa.Column("update_payload", sa.LargeBinary(), nullable=False),
            sa.Column(
                "created_at",
                sa.Text(),
                nullable=False,
                server_default=sa.text(
                    "to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"
                ),
            ),
            sa.UniqueConstraint("session_id", "seq", name="idx_coedit_updates_session_seq"),
        )
    else:
        # Already present — either from 0001_initial's create_all, or from an
        # earlier run of this migration when the column was still NOT NULL.
        # Idempotent in the first case.
        op.alter_column(
            "coedit_updates", "author_user_id", existing_type=sa.Text(), nullable=True
        )

    # Retire any session that predates this schema, and zero its watermarks.
    #
    # Its live content was `buffer_text` plus the `coedit_ops` log, both dropped
    # above: an OT range-op log can't be converted into a Yjs update log (the
    # CRDT lineage it would need doesn't exist), so an edit that hadn't reached
    # git is gone. That loss is accepted — pre-production, one clean cut. What
    # is *not* acceptable is leaving the row ACTIVE with its old counters:
    # `ydoc_seq`/`ydoc_checkpointed_seq` carry the renamed OT values, so the row
    # reads as permanently dirty against an empty update log. The periodic scan
    # would re-enqueue a checkpoint for it every minute forever — skipped on the
    # no-snapshot guard until someone opens the page, and then committing on
    # every pass, since `set_initial_snapshot` stamps `ydoc_snapshot_seq = 0`
    # and never lowers `ydoc_seq` to match. Closing the row ends that: the
    # closed+dirty guard in `checkpoint_session` refuses to commit it, and
    # opening the page mints a fresh session seeded from git HEAD.
    op.execute(
        sa.text(
            "UPDATE coedit_sessions SET status = 'closed', ydoc_seq = 0,"
            " ydoc_checkpointed_seq = 0 WHERE status = 'active'"
        )
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("coedit_updates"):
        op.drop_table("coedit_updates")
    if not inspector.has_table("coedit_ops"):
        op.create_table(
            "coedit_ops",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column(
                "session_id",
                sa.BigInteger(),
                sa.ForeignKey("coedit_sessions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("seq", sa.BigInteger(), nullable=False),
            sa.Column(
                "author_user_id",
                sa.Text(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("base_version", sa.BigInteger(), nullable=False),
            sa.Column("client_id", sa.Text(), nullable=True),
            sa.Column("op_payload", postgresql.JSONB(), nullable=False),
            sa.Column(
                "created_at",
                sa.Text(),
                nullable=False,
                server_default=sa.text(
                    "to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"
                ),
            ),
            sa.UniqueConstraint("session_id", "seq", name="idx_coedit_ops_session_seq"),
        )

    cols = _columns("coedit_sessions")
    if "ydoc_checkpointed_seq" in cols:
        op.alter_column(
            "coedit_sessions", "ydoc_checkpointed_seq", new_column_name="checkpointed_version"
        )
    if "ydoc_seq" in cols:
        op.alter_column("coedit_sessions", "ydoc_seq", new_column_name="version")
    if "ydoc_snapshot_body" in cols:
        op.drop_column("coedit_sessions", "ydoc_snapshot_body")
    if "ydoc_snapshot_seq" in cols:
        op.drop_column("coedit_sessions", "ydoc_snapshot_seq")
    if "ydoc_snapshot" in cols:
        op.drop_column("coedit_sessions", "ydoc_snapshot")
    if "buffer_text" not in cols:
        op.add_column(
            "coedit_sessions",
            sa.Column("buffer_text", sa.Text(), nullable=False, server_default=sa.text("''")),
        )
