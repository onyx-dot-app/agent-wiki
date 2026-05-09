"""Alembic environment.

Wires Alembic to the live app: pulls the URL from ``CONFIG.database_url``
(via ``app.db.session._sa_url``) and the target metadata from
``app.db.models.Base``.

Two ways this gets invoked:

  * ``init_db()`` calls ``command.upgrade(cfg, "head")`` programmatically
    on every backend / worker boot — the migration runner is the only
    code path that touches schema in production.
  * ``alembic revision --autogenerate -m "..."`` from the CLI uses this
    same env to compare ``Base.metadata`` to the DB and emit a diff
    migration into ``versions/``.

We import ``app.db.models`` for its side effects (registering every ORM
class on ``Base.metadata``) so autogenerate sees the full schema.
"""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.config import CONFIG
from app.db import models  # noqa: F401 — registers all tables on Base.metadata  # pyright: ignore[reportUnusedImport]
from app.db.models import Base
from app.db.session import _sa_url  # pyright: ignore[reportPrivateUsage]

config = context.config

# We sidestep alembic's ``sqlalchemy.url`` config option entirely because
# configparser would otherwise try to interpolate the ``%3D`` (etc.) that
# show up in our search-path query string. The programmatic ``init_db()``
# path stashes the URL under ``cfg.attributes['db_url']``; CLI invocations
# fall back to ``CONFIG.database_url``.
_db_url = config.attributes.get("db_url") or _sa_url(CONFIG.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations against a URL only, emitting SQL to stdout."""
    context.configure(
        url=_db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a real engine."""
    connectable = create_engine(_db_url, poolclass=pool.NullPool, future=True)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Compare both column types and server defaults so autogenerate
            # picks up subtle schema drift instead of silently ignoring it.
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
