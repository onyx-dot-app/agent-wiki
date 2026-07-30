"""Shared pytest fixtures.

Each test gets an isolated Postgres database (created at the start,
dropped on teardown) and a fresh wiki dir under ``tmp_path``. We construct
a new ``Config`` and patch it into every module that pulled ``CONFIG`` in
via ``from app.config import CONFIG`` — those bindings are captured at
import time, so patching ``app.config.CONFIG`` alone is not enough.

Per-test databases are cloned from a session-scoped template database
that has every migration applied (``CREATE DATABASE … TEMPLATE …``).
Cloning costs ~25ms regardless of how many migrations exist, whereas
running ``alembic upgrade head`` per test walks the whole migration
chain and gets slower with every migration added. The template's name
embeds a fingerprint of the migration sources, so editing or adding a
migration invalidates it automatically; templates built from older
migration sets are dropped when a new one is built.

The maintenance database named by ``TEST_DATABASE_URL`` must already
exist — it is only connected to for CREATE/DROP DATABASE calls. Locally:

  createdb agent_wiki_test

OpenSearch tests require a running OpenSearch instance.  Set
``TEST_OPENSEARCH_URL`` (default ``http://localhost:9201``) and make sure
the service is up — docker compose runs it on port 9201.  Tests that
require OpenSearch are marked ``@needs_opensearch`` and skipped when the
instance is not reachable.
"""

from __future__ import annotations

import hashlib
import os
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

# Make sure required env vars exist before ``app.config`` is imported by any
# subsequent test module. ``app.config.load_config()`` runs at import.
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("PUBLIC_BASE_URL", "http://testserver")

import bcrypt
import psycopg
import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from psycopg import sql
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.config import Config
from app.db import comment_fts as _comment_fts
from app.db import fts as _fts
from app.db.models import Base
from app.db.session import (
    _sa_url,  # pyright: ignore[reportPrivateUsage]
    reset_engine_for_tests,
)
from app.mcp_server import session as _mcp_session
from app.realtime import bus as _bus
from app.tasks.queue import get_redis, reset_redis_for_tests
from app.wiki.git import ensure_wiki_repo

# --------------------------------------------------------------------------- #
# OpenSearch availability check (runs once at collection time)                #
# --------------------------------------------------------------------------- #

_TEST_OPENSEARCH_URL = os.environ.get("TEST_OPENSEARCH_URL", "http://localhost:9201")


def _check_opensearch() -> bool:
    try:
        urllib.request.urlopen(f"{_TEST_OPENSEARCH_URL}/_cluster/health", timeout=2)
        return True
    except Exception:  # noqa: BLE001 — any failure at all means "not reachable"
        return False


_opensearch_up: bool = _check_opensearch()

needs_opensearch = pytest.mark.skipif(
    not _opensearch_up,
    reason=f"OpenSearch not reachable at {_TEST_OPENSEARCH_URL}",
)

# Suppress the cross-process Postgres LISTEN bridge in tests. ``create_app``'s
# lifespan calls ``bus.start_listener`` (app/main.py), which would open a
# connection on the shared test DB and receive every NOTIFY emitted by every
# other xdist worker. Tests exercise in-process delivery directly, so the
# listener has no value and only adds cross-worker noise.
_bus.start_listener = lambda: None  # type: ignore[assignment]

_BASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/agent_wiki_test",
)


@pytest.fixture(scope="session", autouse=True)
def _fast_bcrypt():
    """Generate bcrypt salts at cost 4 instead of the production default.

    ``checkpw`` reads the work factor out of the hash itself, so every
    verification of a test-minted hash gets cheap too — ~1ms instead of
    ~165ms. That matters because MCP bearer auth verifies on every
    request. Patched at the ``bcrypt`` module so callers that imported
    ``hash_password`` by value are covered as well."""
    orig_gensalt = bcrypt.gensalt
    mp = pytest.MonkeyPatch()
    mp.setattr(bcrypt, "gensalt", lambda rounds=12, prefix=b"2b": orig_gensalt(4, prefix))
    yield
    mp.undo()


@pytest.fixture(autouse=True)
def _reset_mcp_state():
    """Module-level pubsub/session state lives in process memory and can
    leak across tests on the same xdist worker. Reset before each test
    so subscriptions, queues, and session rows from the previous test
    can't bleed in. Autouse so new tests can't forget."""
    _mcp_session.reset_for_tests()
    yield


_BACKEND_DIR = Path(__file__).resolve().parent.parent
_MIGRATIONS_DIR = _BACKEND_DIR / "app" / "db" / "migrations"

_TEMPLATE_PREFIX = "agent_wiki_test_tmpl_"
# Arbitrary advisory-lock keys, both taken on the maintenance DB
# (_BASE_URL — advisory locks are scoped to the database the connection is
# on). BUILD serializes template builds across xdist workers. USE is held
# *shared* by every live test session for as long as it may clone its
# template, and taken exclusively (non-blocking) before dropping stale
# templates — so cleanup skips whenever an overlapping run on another
# checkout might still clone from an older template.
_TEMPLATE_BUILD_LOCK_KEY = 913_027_554
_TEMPLATE_USE_LOCK_KEY = 913_027_555


def _with_dbname(url: str, dbname: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"/{dbname}"))


def _migrations_fingerprint() -> str:
    """Hash the migration sources plus the compiled DDL of the ORM
    metadata (which the bootstrap migration materializes via
    ``create_all``) so the template database is rebuilt whenever the
    migrated schema could differ. Compiled DDL — not source files —
    because the physical schema also depends on code imported by
    models.py (custom column types, server defaults, …)."""
    h = hashlib.sha256()
    for p in sorted((_MIGRATIONS_DIR / "versions").glob("*.py")):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    dialect = postgresql.dialect()
    for table in sorted(Base.metadata.tables.values(), key=lambda t: t.name):
        h.update(str(CreateTable(table).compile(dialect=dialect)).encode())
        for index in sorted(table.indexes, key=lambda i: i.name or ""):
            h.update(str(CreateIndex(index).compile(dialect=dialect)).encode())
    return h.hexdigest()[:12]


def _upgrade_to_head(url: str) -> None:
    """Run ``alembic upgrade head`` against ``url``.

    Mirrors ``app.db.session.init_db``'s programmatic invocation but takes
    an explicit URL instead of reading ``CONFIG`` (which isn't patched yet
    at session-fixture time). ``env.py`` uses a NullPool engine, so no
    connection outlives this call — required for the RENAME below."""
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.attributes["db_url"] = _sa_url(url)
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
def _template_db():
    """Ensure the migrated template database exists; yield its name.

    Runs once per xdist worker. The first worker to take the build lock
    builds the template under a ``_bld`` scratch name and atomically
    RENAMEs it into place, so a crashed build can never be mistaken for a
    finished template; the rest see it exists and move on.

    ``guard`` holds the shared USE lock for the whole session (released
    when the connection closes). It is acquired inside the BUILD critical
    section, so a concurrent run's cleanup can never observe this
    session's template as unreferenced between the existence check and
    the shared-lock grab."""
    name = f"{_TEMPLATE_PREFIX}{_migrations_fingerprint()}"
    guard = psycopg.connect(_BASE_URL, autocommit=True)
    try:
        with psycopg.connect(_BASE_URL, autocommit=True) as conn:
            conn.execute("SELECT pg_advisory_lock(%s)", (_TEMPLATE_BUILD_LOCK_KEY,))
            try:
                row = conn.execute(
                    "SELECT 1 FROM pg_database WHERE datname = %s", (name,)
                ).fetchone()
                if not row:
                    _drop_stale_templates(conn, keep=name)
                    bld = f"{name}_bld"
                    conn.execute(
                        sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                            sql.Identifier(bld)
                        )
                    )
                    conn.execute(
                        sql.SQL("CREATE DATABASE {}").format(sql.Identifier(bld))
                    )
                    _upgrade_to_head(_with_dbname(_BASE_URL, bld))
                    conn.execute(
                        sql.SQL("ALTER DATABASE {} RENAME TO {}").format(
                            sql.Identifier(bld), sql.Identifier(name)
                        )
                    )
                guard.execute(
                    "SELECT pg_advisory_lock_shared(%s)", (_TEMPLATE_USE_LOCK_KEY,)
                )
            finally:
                conn.execute(
                    "SELECT pg_advisory_unlock(%s)", (_TEMPLATE_BUILD_LOCK_KEY,)
                )
        yield name
    finally:
        guard.close()


def _drop_stale_templates(conn: psycopg.Connection, *, keep: str) -> None:
    """Drop templates built from older migration sets — but only if no
    live session anywhere still holds the shared USE lock, since an
    overlapping run on another checkout may yet clone from one of them.
    Skipped templates get cleaned by a later run that overlaps nothing."""
    row = conn.execute(
        "SELECT pg_try_advisory_lock(%s)", (_TEMPLATE_USE_LOCK_KEY,)
    ).fetchone()
    if not (row and row[0]):
        return
    try:
        stale = conn.execute(
            "SELECT datname FROM pg_database WHERE datname LIKE %s AND datname != %s",
            (_TEMPLATE_PREFIX + "%", keep),
        ).fetchall()
        for (datname,) in stale:
            try:
                conn.execute(
                    sql.SQL("DROP DATABASE {}").format(sql.Identifier(datname))
                )
            except psycopg.Error:
                pass  # e.g. a clone in flight; a later run gets it
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (_TEMPLATE_USE_LOCK_KEY,))


@pytest.fixture
def tmp_config(tmp_path, monkeypatch, _template_db):
    """Point CONFIG at a per-test database cloned from the migrated
    template — every migration is already applied. Tests that call
    ``init_db()`` themselves still work; it's a fast no-op walk."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    dbname = f"test_{uuid.uuid4().hex[:12]}"

    with psycopg.connect(_BASE_URL, autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(
                sql.Identifier(dbname), sql.Identifier(_template_db)
            )
        )

    cfg = Config(
        secret_key="test-secret",
        wiki_dir=str(wiki_dir),
        database_url=_with_dbname(_BASE_URL, dbname),
        redis_url=os.environ.get("TEST_REDIS_URL", "redis://localhost:6380/1"),
        # Queue keys share one Redis across tests and xdist workers; the
        # prefix keeps each test's streams private (and lets teardown
        # delete them — nothing drains queues in tests).
        redis_key_prefix=f"{dbname}:",
        opensearch_url=_TEST_OPENSEARCH_URL,
        opensearch_index=f"wiki-docs-test-{dbname}",  # isolated per test
        max_queue_size=1000,
        web_thread_pool_size=64,
        auth_mode="basic",
        oidc_issuer="",
        oidc_client_id="",
        oidc_client_secret="",
        oidc_redirect_uri="",
        secure_cookies=False,
        dev_mode=True,
        encryption_key_secret="",
        ingest_bm25_min_score=1.0,
        ingest_bm25_title_boost=2.0,
        ingest_bm25_limit=20,
        ingest_irrelevant_stop_n=2,
        ingest_embed_model="text-embedding-3-small",
        ingest_relevance_two_tower_model_path="",
        ingest_relevance_cosine_threshold=0.4334,
        ingest_eval_logging=False,
        ingest_eval_retention_days=90,
        launchers_enabled=True,
        launch_code_ttl_seconds=60,
        agent_session_idle_seconds=300,
        agent_session_close_after_idle_seconds=86400,
        agent_session_spawn_ok_seconds=30,
        public_base_url="http://testserver",
    )
    monkeypatch.setattr("app.config.CONFIG", cfg)
    monkeypatch.setattr("app.db.session.CONFIG", cfg)
    # Launcher API modules cache `CONFIG` at import time via
    # `from app.config import CONFIG`, so a setattr on `app.config.CONFIG`
    # alone doesn't reach them. CI runs without LAUNCHERS_ENABLED in env
    # (the local .env sets it to true), which is what masked this in
    # earlier local runs.
    monkeypatch.setattr("app.api.launchers.CONFIG", cfg)
    monkeypatch.setattr("app.api.agent_sessions.CONFIG", cfg)
    monkeypatch.setattr("app.api.craft.CONFIG", cfg)

    # Reset the lazy OpenSearch client so it re-reads CONFIG on next use.
    _fts.reset_client_for_tests()

    # Same for the lazy Redis client — otherwise a client cached against the
    # default URL (or an earlier test's broker) leaks across cases.
    reset_redis_for_tests()

    # Each test rebuilds the engine so it points at the new database.
    reset_engine_for_tests()

    # Every test's coedit_sessions id sequence restarts at 1 (a fresh clone
    # of the empty-table template) — without this, a room left behind by an
    # earlier test (never explicitly closed) would be adopted by a later,
    # unrelated test that happens to reuse the same session id.

    yield cfg

    # Delete this test's queue keys while CONFIG still points at the test
    # broker — accumulated streams would otherwise trip the queue-size cap
    # after enough runs.
    try:
        r = get_redis()
        keys = list(r.scan_iter(match=f"{dbname}:*"))
        if keys:
            r.delete(*keys)
    except Exception:  # noqa: BLE001 — teardown; a dead broker shouldn't mask the test result
        pass

    reset_engine_for_tests()
    if _opensearch_up:
        _fts.drop_index_for_tests()  # delete the per-test index
        _comment_fts.drop_index_for_tests()  # and the per-test comment index
    _fts.reset_client_for_tests()
    reset_redis_for_tests()
    with psycopg.connect(_BASE_URL, autocommit=True) as conn:
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                sql.Identifier(dbname)
            )
        )


@pytest.fixture
def tmp_db(tmp_config):
    """Tmp DB with all migrations applied.

    The clone made by ``tmp_config`` is already at head, so there is
    nothing left to do — the fixture exists for its name: tests take it
    to declare they need a migrated database."""
    return tmp_config


@pytest.fixture
def tmp_repo(tmp_db, tmp_config, monkeypatch):
    """Tmp DB + an initialized wiki git repo for tests that touch the filesystem."""
    monkeypatch.setattr("app.wiki.git.CONFIG", tmp_config)
    monkeypatch.setattr("app.wiki.filesystem.CONFIG", tmp_config)

    ensure_wiki_repo()
    return tmp_db
