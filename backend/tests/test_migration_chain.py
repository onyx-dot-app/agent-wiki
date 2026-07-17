"""Alembic migration chain integrity.

CI only ever builds a schema through ``init_db`` (``alembic upgrade head``, on
top of ``0001``'s ``create_all``), so a stray ``down_revision`` that forks the
history or a migration that can't be re-run is invisible to it. These assert one
head (no divergence) and that the provenance migrations survive a real
down-then-up round trip, which nothing else exercises.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import command
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from app.db.session import _alembic_config, get_engine

# The write-side migration. Its down_revision is the point we roll back to.
_PROVENANCE_LEDGER = "c7a1f0e3b2d9"
_PROVENANCE_TABLES = ("provenance_ledger", "source_ranges")


def _current_revision() -> str | None:
    with get_engine().connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def _has_table(name: str) -> bool:
    return sa.inspect(get_engine()).has_table(name)


def test_single_head_revision():
    """Exactly one head. A forked or dangling down_revision shows up here."""
    heads = ScriptDirectory.from_config(_alembic_config()).get_heads()
    assert len(heads) == 1, f"diverged migration history, expected one head, got {heads}"


def test_provenance_migrations_round_trip(tmp_db):
    """Downgrade past the provenance migrations then upgrade back to head.

    The fixture leaves the schema at head. Rolling back drops the source-range
    and ledger tables and re-running the chain recreates them, so a broken
    downgrade or a create that no longer matches the model fails here rather
    than only on a real production upgrade.
    """
    cfg = _alembic_config()
    script = ScriptDirectory.from_config(cfg)
    head = script.get_heads()[0]
    before_provenance = script.get_revision(_PROVENANCE_LEDGER).down_revision
    assert isinstance(before_provenance, str)  # linear parent, not a merge point

    assert _current_revision() == head
    assert all(_has_table(t) for t in _PROVENANCE_TABLES)

    command.downgrade(cfg, before_provenance)
    assert _current_revision() == before_provenance
    assert not any(_has_table(t) for t in _PROVENANCE_TABLES)

    command.upgrade(cfg, "head")
    assert _current_revision() == head
    assert all(_has_table(t) for t in _PROVENANCE_TABLES)
