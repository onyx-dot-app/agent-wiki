"""Shared pytest fixtures.

Each test gets an isolated Postgres schema (created at the start, dropped
on teardown) and a fresh wiki dir under ``tmp_path``. We construct a new
``Config`` and patch it into every module that pulled ``CONFIG`` in via
``from app.config import CONFIG`` — those bindings are captured at import
time, so patching ``app.config.CONFIG`` alone is not enough.

The test database must already exist; point ``TEST_DATABASE_URL`` at it.
Locally:

  createdb agent_wiki_test

OpenSearch tests require a running OpenSearch instance.  Set
``TEST_OPENSEARCH_URL`` (default ``http://localhost:9201``) and make sure
the service is up — docker compose runs it on port 9201.  Tests that
require OpenSearch are marked ``@needs_opensearch`` and skipped when the
instance is not reachable.
"""

from __future__ import annotations

import os
import urllib.request
import uuid
from urllib.parse import quote

# Make sure required env vars exist before ``app.config`` is imported by any
# subsequent test module. ``app.config.load_config()`` runs at import.
os.environ.setdefault("SECRET_KEY", "test-secret")

import psycopg
import pytest
from psycopg import sql

from app.config import Config
from app.mcp_server import pubsub as _mcp_pubsub
from app.mcp_server import session as _mcp_session

# --------------------------------------------------------------------------- #
# OpenSearch availability check (runs once at collection time)                #
# --------------------------------------------------------------------------- #

_TEST_OPENSEARCH_URL = os.environ.get("TEST_OPENSEARCH_URL", "http://localhost:9201")


def _check_opensearch() -> bool:
    try:
        urllib.request.urlopen(f"{_TEST_OPENSEARCH_URL}/_cluster/health", timeout=2)
        return True
    except Exception:
        return False


_opensearch_up: bool = _check_opensearch()

needs_opensearch = pytest.mark.skipif(
    not _opensearch_up,
    reason=f"OpenSearch not reachable at {_TEST_OPENSEARCH_URL}",
)

# Suppress the cross-process Postgres LISTEN bridge in tests. ``create_app``'s
# lifespan calls ``mcp_pubsub.start_listener`` (app/main.py), which would
# open a connection on the shared test DB and receive every NOTIFY emitted
# by every other xdist worker. Tests exercise in-process delivery directly,
# so the listener has no value and only adds cross-worker noise.
_mcp_pubsub.start_listener = lambda: None  # type: ignore[assignment]

_BASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/agent_wiki_test",
)


@pytest.fixture(autouse=True)
def _reset_mcp_state():
    """Module-level pubsub/session state lives in process memory and can
    leak across tests on the same xdist worker. Reset before each test
    so subscriptions, queues, and session rows from the previous test
    can't bleed in. Autouse so new tests can't forget."""
    _mcp_session.reset_for_tests()
    yield


def _with_search_path(url: str, schema: str) -> str:
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}options={quote(f'-csearch_path={schema},public')}"


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    """Point CONFIG at a per-test schema. Does not initialize the DB."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    schema = f"test_{uuid.uuid4().hex[:12]}"

    with psycopg.connect(_BASE_URL, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))

    cfg = Config(
        secret_key="test-secret",
        wiki_dir=str(wiki_dir),
        database_url=_with_search_path(_BASE_URL, schema),
        redis_url=os.environ.get("TEST_REDIS_URL", "redis://localhost:6380/1"),
        opensearch_url=_TEST_OPENSEARCH_URL,
        opensearch_index=f"wiki-docs-test-{schema}",  # isolated per test
        max_queue_size=1000,
        auth_mode="basic",
        oidc_issuer="",
        oidc_client_id="",
        oidc_client_secret="",
        oidc_redirect_uri="",
        secure_cookies=False,
        ingest_bm25_min_score=1.0,
        ingest_bm25_title_boost=2.0,
        ingest_bm25_limit=20,
        ingest_irrelevant_stop_n=2,
        ingest_eval_logging=False,
        launchers_enabled=True,
        launch_code_ttl_seconds=60,
        agent_session_idle_seconds=300,
        agent_session_close_after_idle_seconds=86400,
        agent_session_spawn_ok_seconds=30,
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

    # Reset the lazy OpenSearch client so it re-reads CONFIG on next use.
    from app.db import fts as _fts

    _fts.reset_client_for_tests()

    # Each test rebuilds the engine so the new schema's search_path takes effect.
    from app.db.session import reset_engine_for_tests

    reset_engine_for_tests()

    yield cfg

    reset_engine_for_tests()
    from app.db import fts as _fts

    if _opensearch_up:
        _fts.drop_index_for_tests()  # delete the per-test index
    _fts.reset_client_for_tests()
    with psycopg.connect(_BASE_URL, autocommit=True) as conn:
        conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture
def tmp_db(tmp_config):
    """Tmp DB with all migrations applied."""
    from app.db.session import init_db

    init_db()
    return tmp_config


@pytest.fixture
def tmp_repo(tmp_db, tmp_config, monkeypatch):
    """Tmp DB + an initialized wiki git repo for tests that touch the filesystem."""
    monkeypatch.setattr("app.wiki.git.CONFIG", tmp_config)
    monkeypatch.setattr("app.wiki.filesystem.CONFIG", tmp_config)

    from app.wiki.git import ensure_wiki_repo

    ensure_wiki_repo()
    return tmp_db
