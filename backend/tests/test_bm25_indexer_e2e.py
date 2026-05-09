"""End-to-end: a save makes the doc searchable via the BM25 index.

The other e2e file (``test_save_to_fire_e2e.py``) asserts trigger fan-out
goes through. This file asserts the **other** half of the post-write
seam — the ``wiki_bm25_queue``-routed ``reindex_path`` task — actually
executes and populates ``documents_fts``.

We run with ``wiki_bm25_queue.immediate = True`` so the worker
loop is replaced by inline execution. That proves the wiring (decorator
registration, queue routing, fts.upsert_document call) without needing
a real consumer process. Production parity check is in
``docker-compose.yml`` (``worker-wiki-bm25``).
"""
from __future__ import annotations

import pytest

from app.db import fts

from tests._seed import list_fts_rows


@pytest.fixture
def app(tmp_repo):
    from app.main import create_app

    flask_app = create_app()
    flask_app.config["TESTING"] = True
    return flask_app


@pytest.fixture
def signed_in(app, tmp_repo):
    from app.auth import users as users_repo

    users_repo.create(email="u@x.com", password="hunter2", name="U")
    client = app.test_client()
    resp = client.post(
        "/api/auth/login", json={"email": "u@x.com", "password": "hunter2"}
    )
    assert resp.status_code == 200
    return client


@pytest.fixture(autouse=True)
def _immediate_queues():
    """Run the bm25 + triggers queues inline.

    We don't care about trigger fires here, but the save path enqueues
    on both queues and we want neither to block.
    """
    from contextlib import ExitStack

    from app.tasks.queues import triggers_queue, wiki_bm25_queue

    with ExitStack() as stack:
        stack.enter_context(wiki_bm25_queue.immediate_mode())
        stack.enter_context(triggers_queue.immediate_mode())
        yield


def _fts_rows():
    return list_fts_rows()


def _put_doc(client, *, path, body):
    return client.put("/api/documents/file", json={"path": path, "body": body})


# --------------------------------------------------------------------------- #
# Happy path                                                                  #
# --------------------------------------------------------------------------- #


def test_save_indexes_doc_into_fts(signed_in):
    resp = _put_doc(signed_in, path="guide.md", body="# Guide\n\nfindable\n")
    assert resp.status_code == 200, resp.get_data(as_text=True)

    rows = _fts_rows()
    assert len(rows) == 1
    assert rows[0]["path"] == "guide.md"
    assert rows[0]["title"] == "Guide"
    assert "findable" in rows[0]["body"]


def test_save_makes_doc_searchable_via_bm25(signed_in):
    """Round-trip through the actual search seam."""
    _put_doc(
        signed_in,
        path="auth/passwords.md",
        body="# Passwords\n\nWe use bcrypt for password hashing.\n",
    )

    hits = fts.search("bcrypt")
    assert any(h.path == "auth/passwords.md" for h in hits)
    found = next(h for h in hits if h.path == "auth/passwords.md")
    assert found.title == "Passwords"
    assert "bcrypt" in found.snippet.lower()


def test_edit_replaces_indexed_body(signed_in):
    """Re-saving a doc replaces the FTS row, not appends."""
    _put_doc(signed_in, path="x.md", body="# X\n\nfirst pass content\n")
    _put_doc(signed_in, path="x.md", body="# X\n\nsecond pass material\n")

    rows = _fts_rows()
    assert len(rows) == 1, "should be one row per path, not duplicated"
    assert "second pass material" in rows[0]["body"]
    assert "first pass" not in rows[0]["body"]


# --------------------------------------------------------------------------- #
# Move / delete paths                                                         #
# --------------------------------------------------------------------------- #


def test_move_drops_old_path_and_indexes_new_path(signed_in):
    _put_doc(signed_in, path="src/foo.md", body="# Foo\n\nfindmehere\n")

    resp = signed_in.post(
        "/api/documents/move",
        json={"old_path": "src/foo.md", "new_path": "dst/foo.md"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)

    paths = {r["path"] for r in _fts_rows()}
    assert "src/foo.md" not in paths, "stale FTS row should have been dropped"
    assert "dst/foo.md" in paths, "new path should be indexed"

    hits = fts.search("findmehere")
    assert hits and hits[0].path == "dst/foo.md"


def test_delete_removes_doc_from_fts(signed_in):
    _put_doc(signed_in, path="goodbye.md", body="# Bye\n\nseeing you later\n")
    assert any(r["path"] == "goodbye.md" for r in _fts_rows())

    resp = signed_in.delete("/api/documents/file?path=goodbye.md")
    assert resp.status_code == 200, resp.get_data(as_text=True)

    paths = {r["path"] for r in _fts_rows()}
    assert "goodbye.md" not in paths


# --------------------------------------------------------------------------- #
# Manual reindex API                                                          #
# --------------------------------------------------------------------------- #


def test_manual_reindex_endpoint_routes_through_same_queue(signed_in):
    """``POST /api/documents/reindex`` enqueues a task on wiki_bm25_queue too,
    so the same immediate-mode patch covers it."""
    from app.wiki import git as wiki_git

    # Commit directly via git, bypassing the API so FTS isn't pre-populated.
    wiki_git.commit_file("manual.md", "# Manual\n\nmanual indexable\n", "seed", author=None)
    assert not [r for r in _fts_rows() if r["path"] == "manual.md"]

    resp = signed_in.post("/api/documents/reindex", json={"path": "manual.md"})
    assert resp.status_code == 200, resp.get_data(as_text=True)

    hits = fts.search("indexable")
    assert any(h.path == "manual.md" for h in hits)


# --------------------------------------------------------------------------- #
# Chat-agent edits hit the same indexer                                       #
# --------------------------------------------------------------------------- #


def test_chat_agent_edit_reindexes(signed_in):
    """Chat agent edits run through `_doc_helpers.commit_and_fan_out` →
    `wiki.notify.after_doc_write` → `reindex_path` — same queue."""
    from app.llm.agents._session import seen_doc_paths
    from app.llm.agents.tools.edit_doc import handle as edit_doc
    from app.wiki import git as wiki_git

    wiki_git.commit_file("agent.md", "# A\n\nbeforetoken\n", "seed", author=None)
    # `wiki_git.commit_file` doesn't auto-reindex — confirm the seed isn't in FTS.
    assert not [r for r in _fts_rows() if r["path"] == "agent.md"]

    token = seen_doc_paths.set({"agent.md"})
    try:
        out = edit_doc(
            {
                "path": "agent.md",
                "old_string": "beforetoken",
                "new_string": "aftertoken",
                "commit_message": "tweak",
            }
        )
    finally:
        seen_doc_paths.reset(token)
    assert "error" not in out, out

    hits = fts.search("aftertoken")
    assert any(h.path == "agent.md" for h in hits)
    # And the old content is gone from the index (replaced, not appended).
    assert not fts.search("beforetoken")
