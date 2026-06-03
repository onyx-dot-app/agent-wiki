"""Tests for ``GET/POST /api/wiki/recents``.

Recents = docs the user actually opened, newest first, per user.
Crucially, the list is ordered by the user's own views — a commit to a
doc the user never opened must not appear or reshuffle anything (that
was the bug with the old commit-time-ordered sidebar).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth import users as users_repo
from app.main import create_app
from app.wiki import acl, git as wiki_git, recents

from tests._auth import login_fastapi


@pytest.fixture
def client(tmp_db, tmp_repo):
    return TestClient(create_app())


def _record(client: TestClient, path: str) -> None:
    resp = client.post("/api/wiki/recents", json={"path": path})
    assert resp.status_code == 204


def test_unauthenticated_is_401(client):
    assert client.get("/api/wiki/recents").status_code == 401
    assert client.post("/api/wiki/recents", json={"path": "a.md"}).status_code == 401


def test_record_and_list_newest_first(client):
    uid = users_repo.create(email="alice@x.com", password="hunter2-x", name="Alice")
    login_fastapi(client, uid)
    wiki_git.commit_file("a.md", "# a", "seed")
    wiki_git.commit_file("b.md", "# b", "seed")

    _record(client, "a.md")
    _record(client, "b.md")
    assert client.get("/api/wiki/recents").json() == {"paths": ["b.md", "a.md"]}

    # Re-opening an older doc bumps it to the front, no duplicate row.
    _record(client, "a.md")
    assert client.get("/api/wiki/recents").json() == {"paths": ["a.md", "b.md"]}


def test_doc_updates_do_not_affect_recents(client):
    """A commit to a doc the user never opened must not surface it, and
    a commit to a doc they did open must not change its position."""
    uid = users_repo.create(email="alice@x.com", password="hunter2-x", name="Alice")
    login_fastapi(client, uid)
    wiki_git.commit_file("a.md", "# a", "seed")
    wiki_git.commit_file("b.md", "# b", "seed")
    wiki_git.commit_file("never-opened.md", "# n", "seed")

    _record(client, "a.md")
    _record(client, "b.md")
    # Agent/trigger-style updates land after the views.
    wiki_git.commit_file("never-opened.md", "# n v2", "agent update")
    wiki_git.commit_file("a.md", "# a v2", "agent update")

    assert client.get("/api/wiki/recents").json() == {"paths": ["b.md", "a.md"]}


def test_recents_are_per_user(client):
    alice = users_repo.create(email="alice@x.com", password="hunter2-x", name="Alice")
    bob = users_repo.create(email="bob@x.com", password="hunter2-x", name="Bob")
    wiki_git.commit_file("a.md", "# a", "seed")
    wiki_git.commit_file("b.md", "# b", "seed")

    login_fastapi(client, alice)
    _record(client, "a.md")

    login_fastapi(client, bob)
    _record(client, "b.md")
    assert client.get("/api/wiki/recents").json() == {"paths": ["b.md"]}


def test_deleted_docs_drop_out(client):
    uid = users_repo.create(email="alice@x.com", password="hunter2-x", name="Alice")
    login_fastapi(client, uid)
    wiki_git.commit_file("keep.md", "# k", "seed")
    wiki_git.commit_file("gone.md", "# g", "seed")
    _record(client, "gone.md")
    _record(client, "keep.md")

    wiki_git.delete_path("gone.md", "remove")
    assert client.get("/api/wiki/recents").json() == {"paths": ["keep.md"]}


def test_revoked_read_access_drops_out(client):
    """ACLs can change after a view — the list re-filters on read."""
    alice = users_repo.create(email="alice@x.com", password="hunter2-x", name="Alice")
    bob = users_repo.create(email="bob@x.com", password="hunter2-x", name="Bob")
    wiki_git.commit_file("private.md", "# p", "seed")

    login_fastapi(client, bob)
    _record(client, "private.md")
    assert client.get("/api/wiki/recents").json() == {"paths": ["private.md"]}

    # Make the doc owner-only after Bob's view.
    acl.set_owner("private.md", alice)
    for grant in acl.list_for_path("private.md"):
        if grant["principal_kind"] == "everyone":
            acl.revoke(grant["id"])

    assert client.get("/api/wiki/recents").json() == {"paths": []}


def test_record_requires_read_access(client):
    alice = users_repo.create(email="alice@x.com", password="hunter2-x", name="Alice")
    bob = users_repo.create(email="bob@x.com", password="hunter2-x", name="Bob")
    wiki_git.commit_file("private.md", "# p", "seed")
    acl.set_owner("private.md", alice)
    for grant in acl.list_for_path("private.md"):
        if grant["principal_kind"] == "everyone":
            acl.revoke(grant["id"])

    login_fastapi(client, bob)
    resp = client.post("/api/wiki/recents", json={"path": "private.md"})
    assert resp.status_code == 403
    assert client.get("/api/wiki/recents").json() == {"paths": []}


def test_record_rejects_traversal(client):
    uid = users_repo.create(email="alice@x.com", password="hunter2-x", name="Alice")
    login_fastapi(client, uid)
    resp = client.post("/api/wiki/recents", json={"path": "../etc/passwd"})
    assert resp.status_code == 400


def test_cap_prunes_oldest(tmp_db, tmp_repo):
    uid = users_repo.create(email="alice@x.com", password="hunter2-x", name="Alice")
    for i in range(recents.RECENTS_CAP + 5):
        recents.record_view(uid, f"doc-{i:03}.md")
    paths = recents.list_paths(uid, limit=recents.RECENTS_CAP + 5)
    assert len(paths) == recents.RECENTS_CAP
    assert paths[0] == f"doc-{recents.RECENTS_CAP + 4:03}.md"
    # The oldest views fell off.
    assert "doc-000.md" not in paths
