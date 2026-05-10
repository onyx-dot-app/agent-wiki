"""Shared pytest fixtures.

Each test gets an isolated Postgres schema (created at the start, dropped
on teardown) and a fresh wiki dir under ``tmp_path``. We construct a new
``Config`` and patch it into every module that pulled ``CONFIG`` in via
``from app.config import CONFIG`` — those bindings are captured at import
time, so patching ``app.config.CONFIG`` alone is not enough.

The test database itself (with ``pg_textsearch`` already installed) must
already exist; point ``TEST_DATABASE_URL`` at it. Locally:

  createdb agent_wiki_test
  psql agent_wiki_test -c 'CREATE EXTENSION pg_textsearch;'
"""
from __future__ import annotations

import os
import uuid
from urllib.parse import quote

# Make sure required env vars exist before ``app.config`` is imported by any
# subsequent test module. ``app.config.load_config()`` runs at import.
os.environ.setdefault("SECRET_KEY", "test-secret")

import psycopg
import pytest
from psycopg import sql

from app.config import Config

_BASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/agent_wiki_test",
)


def _with_search_path(url: str, schema: str) -> str:
    sep = "&" if "?" in url else "?"
    # Include ``public`` as a fallback so pg_textsearch's functions
    # (``to_bm25query``, ``bm25_*``) and pgmq's helpers — both installed
    # into ``public`` by ``CREATE EXTENSION`` — resolve when the test's
    # primary schema is the per-test isolation schema.
    return f"{url}{sep}options={quote(f'-csearch_path={schema},public')}"


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    """Point CONFIG at a per-test schema. Does not initialize the DB."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    schema = f"test_{uuid.uuid4().hex[:12]}"

    with psycopg.connect(_BASE_URL, autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))

    cfg = Config(
        secret_key="test-secret",
        wiki_dir=str(wiki_dir),
        database_url=_with_search_path(_BASE_URL, schema),
        max_queue_size=1000,
        auth_mode="basic",
        oidc_issuer="",
        oidc_client_id="",
        oidc_client_secret="",
        oidc_redirect_uri="",
        secure_cookies=False,
    )
    monkeypatch.setattr("app.config.CONFIG", cfg)
    monkeypatch.setattr("app.db.session.CONFIG", cfg)

    # Each test rebuilds the engine so the new schema's search_path takes effect.
    from app.db.session import reset_engine_for_tests
    reset_engine_for_tests()

    yield cfg

    reset_engine_for_tests()
    with psycopg.connect(_BASE_URL, autocommit=True) as conn:
        conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


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
