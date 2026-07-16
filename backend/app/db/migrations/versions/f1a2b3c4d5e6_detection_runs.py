"""detection_runs

Adds ``detection_runs`` — the Wiki Auto Management detection-run ledger (one
row per sweep / single-page check: trigger, lifecycle status, who triggered
it, scan stats). Its ``id`` is what ``change_proposals.run_id`` references, so
a run joins to everything it emitted. Guarded with the inspector because
``0001_initial`` builds fresh databases from the current models.

Revision ID: f1a2b3c4d5e6
Revises: 76caae98c2b2
Create Date: 2026-07-16 00:00:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "76caae98c2b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("detection_runs"):
        return
    op.create_table(
        "detection_runs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column(
            "status", sa.Text(), nullable=False, server_default=sa.text("'running'")
        ),
        sa.Column(
            "triggered_by_user_id",
            sa.Text(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "paths_scanned", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "proposals_emitted",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.Text(),
            nullable=False,
            server_default=sa.text(
                "to_char((now() AT TIME ZONE 'UTC'), 'YYYY-MM-DD HH24:MI:SS')"
            ),
        ),
        sa.Column("finished_at", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "trigger IN ('sweep', 'on_create', 'on_write')",
            name="detection_runs_trigger_check",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="detection_runs_status_check",
        ),
    )
    op.create_index(
        "idx_detection_runs_status",
        "detection_runs",
        ["status", "started_at"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("detection_runs"):
        op.drop_table("detection_runs")
