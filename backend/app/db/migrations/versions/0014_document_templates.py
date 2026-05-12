"""document templates + drafts

Adds two tables backing the "new doc from template" feature:

- ``document_templates`` — admin-managed library of named markdown
  templates with optional description and chat system prompt.
- ``document_drafts`` — per-page row recording that a wiki page was
  seeded from a template and the user is still "drafting initial
  version". Deleted when the body diverges from the snapshot.

Revision ID: 0014
Revises: 433c24868299
Create Date: 2026-05-11 00:00:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0014"
down_revision: str | None = "433c24868299"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_NOW_TEXT_DEFAULT = sa.text(
    "to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    if "document_templates" not in existing:
        op.create_table(
            "document_templates",
            sa.Column("id", sa.Text, primary_key=True),
            sa.Column("name", sa.Text, nullable=False, unique=True),
            sa.Column("body", sa.Text, nullable=False),
            sa.Column("description", sa.Text, nullable=True),
            sa.Column("system_prompt", sa.Text, nullable=True),
            sa.Column(
                "created_by_user_id",
                sa.Text,
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "created_at",
                sa.Text,
                nullable=False,
                server_default=_NOW_TEXT_DEFAULT,
            ),
            sa.Column(
                "updated_at",
                sa.Text,
                nullable=False,
                server_default=_NOW_TEXT_DEFAULT,
            ),
        )

    if "document_drafts" not in existing:
        op.create_table(
            "document_drafts",
            sa.Column("path", sa.Text, primary_key=True),
            sa.Column(
                "template_id",
                sa.Text,
                sa.ForeignKey("document_templates.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("template_body_snapshot", sa.Text, nullable=False),
            sa.Column(
                "created_by_user_id",
                sa.Text,
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "created_at",
                sa.Text,
                nullable=False,
                server_default=_NOW_TEXT_DEFAULT,
            ),
        )


def downgrade() -> None:
    op.drop_table("document_drafts")
    op.drop_table("document_templates")
