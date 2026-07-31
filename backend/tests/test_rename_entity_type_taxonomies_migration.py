"""d3a71f5c8b40: ``entity_taxonomies`` -> ``entity_type_taxonomies``.

The table holds a taxonomy of entity TYPES; the old name read as a taxonomy of entities, which is
a different thing. Renamed while the window was open — no rows anywhere, one consumer.

Normal test schemas never have the old name (``0001_initial`` builds from the current registry, so
they arrive already renamed), which means the branch that actually runs against a deployed
database is the one tests would otherwise never take. This reproduces that database: recreate the
table under its old name, rewind the alembic stamp, and let ``init_db()`` migrate forward.
"""

from __future__ import annotations

import sqlalchemy as sa

from app.db.session import init_db, session

_CREATE_OLD = """
CREATE TABLE entity_taxonomies (
    id serial PRIMARY KEY,
    active boolean NOT NULL DEFAULT FALSE,
    corpus_fingerprint text NOT NULL,
    types jsonb NOT NULL,
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
    stats jsonb NOT NULL DEFAULT '{}'::jsonb,
    triggered_by text REFERENCES users(id) ON DELETE SET NULL,
    created_at text NOT NULL DEFAULT '2026-01-01 00:00:00'
)
"""


def _rewind_to_the_old_name(seed_row: bool = False) -> None:
    with session() as s:
        s.execute(sa.text("DROP TABLE IF EXISTS page_needs"))
        s.execute(sa.text("DROP TABLE IF EXISTS entity_type_taxonomies"))
        s.execute(sa.text(_CREATE_OLD))
        s.execute(
            sa.text(
                "CREATE UNIQUE INDEX uq_entity_taxonomies_active "
                "ON entity_taxonomies (active) WHERE active"
            )
        )
        if seed_row:
            s.execute(
                sa.text(
                    "INSERT INTO entity_taxonomies (active, corpus_fingerprint, types) "
                    "VALUES (TRUE, 'abc123', '[{\"name\": \"organization\", "
                    "\"definition\": \"A named company.\"}]'::jsonb)"
                )
            )
        s.execute(sa.text("UPDATE alembic_version SET version_num = 'e1b7c3a95d24'"))


def _tables(s) -> set[str]:
    return set(
        s.scalars(
            sa.text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
        )
    )


def test_the_table_is_renamed(tmp_db) -> None:
    _rewind_to_the_old_name()

    init_db()

    with session() as s:
        tables = _tables(s)
    assert "entity_type_taxonomies" in tables
    assert "entity_taxonomies" not in tables


def test_existing_rows_survive_the_rename(tmp_db) -> None:
    """A rename, not a recreate — anything already derived has to come through intact."""
    _rewind_to_the_old_name(seed_row=True)

    init_db()

    from app.db import entity_type_taxonomy

    row = entity_type_taxonomy.active()
    assert row is not None
    assert row.corpus_fingerprint == "abc123"
    assert row.types[0]["name"] == "organization"


def test_the_active_index_follows_the_table(tmp_db) -> None:
    """The partial unique index is what enforces "at most one active" — if its name were left
    behind, the constraint would still exist but under a name the model does not declare."""
    _rewind_to_the_old_name()

    init_db()

    with session() as s:
        names = set(
            s.scalars(
                sa.text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE tablename = 'entity_type_taxonomies'"
                )
            )
        )
    assert "uq_entity_type_taxonomies_active" in names
    assert "uq_entity_taxonomies_active" not in names


def test_page_needs_lands_on_the_renamed_table(tmp_db) -> None:
    """``page_needs`` is created after the rename and keys into it, so the ordering of the two
    migrations is load-bearing: created first, its foreign key would point at a gone name."""
    _rewind_to_the_old_name()

    init_db()

    with session() as s:
        target = s.scalar(
            sa.text(
                "SELECT ccu.table_name FROM information_schema.table_constraints tc "
                "JOIN information_schema.constraint_column_usage ccu "
                "  ON ccu.constraint_name = tc.constraint_name "
                "WHERE tc.table_name = 'page_needs' AND tc.constraint_type = 'FOREIGN KEY' "
                "  AND ccu.column_name = 'id' AND ccu.table_name LIKE '%taxonomies'"
            )
        )
    assert target == "entity_type_taxonomies"


def test_a_fresh_database_is_left_alone(tmp_db) -> None:
    """The other branch: a schema built from the current models already has the new name, so the
    migration must be a no-op rather than trying to rename something that is not there."""
    with session() as s:
        assert "entity_type_taxonomies" in _tables(s)

    init_db()  # walks every migration again

    with session() as s:
        tables = _tables(s)
    assert "entity_type_taxonomies" in tables
    assert "entity_taxonomies" not in tables
