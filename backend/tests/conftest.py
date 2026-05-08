"""Shared pytest fixtures.

Each test gets isolated SQLite DBs and a fresh wiki dir under ``tmp_path``.
We construct a new ``Config`` and patch it into every module that pulled
``CONFIG`` in via ``from app.config import CONFIG`` — those bindings are
captured at import time, so patching ``app.config.CONFIG`` alone is not
enough.
"""
from __future__ import annotations

import os

# Make sure required env vars exist before ``app.config`` is imported by any
# subsequent test module. ``app.config.load_config()`` runs at import.
os.environ.setdefault("SECRET_KEY", "test-secret")

import pytest

from app.config import Config


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    """Point CONFIG at a per-test tmp dir. Does not initialize the DB."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    cfg = Config(
        secret_key="test-secret",
        wiki_dir=str(wiki_dir),
        app_db_path=str(tmp_path / "app.sqlite"),
        queue_db_path=str(tmp_path / "queue.sqlite"),
        auth_mode="basic",
        oidc_issuer="",
        oidc_client_id="",
        oidc_client_secret="",
        oidc_redirect_uri="",
    )
    monkeypatch.setattr("app.config.CONFIG", cfg)
    monkeypatch.setattr("app.db.sqlite.CONFIG", cfg)
    return cfg


@pytest.fixture
def tmp_db(tmp_config):
    """Tmp DB with all migrations applied."""
    from app.db.sqlite import init_db

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
