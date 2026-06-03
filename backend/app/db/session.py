"""SQLAlchemy engine + session factory.

App state lives in the Postgres pointed at by ``CONFIG.database_url``.
``init_db()`` is the canonical bootstrapper: it runs ``alembic upgrade
head`` against the configured database, which applies every migration in
``app/db/migrations/versions/`` idempotently. Schema changes go in new
migration files — see ``app/db/migrations/`` for how to author one.

Repos use the ``session()`` context manager. Each call opens a new
``Session`` (one transaction per call) — sharing a session across
unrelated work in a request is a footgun we deliberately avoid. Commit
happens on clean exit; rollback on exception.

We don't add a per-request scoped session — the Flask layer has no
multi-step transactions today, and pushing one in would mean rewriting
every repo to take a session argument. Revisit when we have a use case.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, cast

from sqlalchemy import Engine, Executable, create_engine
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, sessionmaker

from app.config import CONFIG

log = logging.getLogger(__name__)

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _sa_url(database_url: str) -> str:
    """Normalize a libpq-style URL to the SQLAlchemy/psycopg3 form.

    SQLAlchemy 2.0 routes ``postgresql://`` to psycopg2 by default; we
    want psycopg3, so prepend the driver tag here.
    """
    if database_url.startswith("postgresql://"):
        return "postgresql+psycopg://" + database_url[len("postgresql://") :]
    return database_url


def get_engine() -> Engine:
    """Return (or build) the singleton engine for ``CONFIG.database_url``.

    Most callers should use ``session()`` instead — only reach for the
    raw engine when you need a connection that *outlives* a single
    transaction (e.g. holding ``pg_advisory_lock`` for the lifetime of
    a leader-elected scheduler).
    """
    global _engine, _session_factory
    if _engine is None:
        _engine = create_engine(
            _sa_url(CONFIG.database_url),
            future=True,
            pool_pre_ping=True,  # cheap dead-connection check
        )
        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)
    return _engine


# Back-compat alias for code that imported the private name. Prefer ``get_engine``.
_get_engine = get_engine


def reset_engine_for_tests() -> None:
    """Drop the cached engine + sessionmaker. Tests call this between cases."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


@contextmanager
def session() -> Generator[Session, None, None]:
    """Open a session, commit on clean exit, rollback on exception."""
    _get_engine()
    assert _session_factory is not None
    s = _session_factory()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def execute_dml(s: Session, stmt: Executable) -> int:
    """Run a DML statement (INSERT/UPDATE/DELETE) and return the affected
    row count. Centralises the ``CursorResult`` cast that ``Session.execute``
    needs to expose ``rowcount`` cleanly under basedpyright strict mode.
    """
    return cast("CursorResult[tuple[object, ...]]", s.execute(stmt)).rowcount


_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

# Advisory lock key used to serialise concurrent init_db() callers.
# "alem" in ASCII — just a stable identifier, not a secret.
_MIGRATION_ADVISORY_LOCK = 0x616C656D


def _alembic_config():
    """Build an Alembic ``Config`` pointed at our migrations + URL.

    Imported lazily so test environments / dev shells that import
    ``app.db.session`` don't pay the alembic import cost up front, and
    so ``alembic`` itself can stay an install-time-only dependency for
    parts of the app that never call ``init_db()``.
    """
    from alembic.config import Config as AlembicConfig

    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    # Stash the URL out-of-band — ``set_main_option`` runs through
    # configparser interpolation, which trips on the ``%3D`` etc. that
    # show up in our search-path query string. ``env.py`` reads
    # ``cfg.attributes['db_url']`` and overrides ``sqlalchemy.url``
    # before constructing the engine.
    cfg.attributes["db_url"] = _sa_url(CONFIG.database_url)
    return cfg


def init_db() -> None:
    """Apply every pending migration. Idempotent; safe on every boot.

    Runs ``alembic upgrade head`` against ``CONFIG.database_url``. The
    bootstrap migration creates every ORM-declared table; subsequent
    migrations layer real ALTERs on top.

    Per-test schema isolation works because ``CONFIG.database_url``
    carries the schema in its ``options=-csearch_path=…`` query string
    — Alembic picks it up from the URL like any other connection.

    A Postgres advisory lock (``_MIGRATION_ADVISORY_LOCK``) serialises concurrent
    callers — uvicorn ``--workers N`` fires the lifespan in every worker
    process simultaneously, so without this lock they race to CREATE TABLE
    alembic_version and the second writer crashes with UniqueViolation.
    """
    import sqlalchemy as sa
    from alembic import command

    engine = get_engine()
    with engine.connect() as conn:
        # Ensure the public schema and pgmq extension exist before Alembic
        # applies any migrations — both are prerequisites for the migration
        # scripts and are not created automatically by PostgreSQL itself.
        conn.execute(sa.text("CREATE SCHEMA IF NOT EXISTS public"))
        conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgmq"))
        conn.commit()

        conn.execute(
            sa.text("SELECT pg_advisory_lock(:lock_key)"), {"lock_key": _MIGRATION_ADVISORY_LOCK}
        )
        try:
            log.info("running alembic upgrade head")
            command.upgrade(_alembic_config(), "head")
        finally:
            conn.execute(
                sa.text("SELECT pg_advisory_unlock(:lock_key)"),
                {"lock_key": _MIGRATION_ADVISORY_LOCK},
            )
