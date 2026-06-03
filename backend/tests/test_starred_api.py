"""Tests for ``GET/POST/DELETE/PUT /api/wiki/starred``.

Starred docs are pinned per user in a user-chosen order: star appends
at the end, drag-reorder rewrites positions via PUT with the full list.
Read-side filtering matches recents — deleted or no-longer-readable
docs are hidden.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth import users as users_repo
from app.main import create_app
from app.wiki import acl, git as wiki_git, starred

from tests._auth import login_fastapi


@pytest.fixture
def client(tmp_db, tmp_repo):
    return TestClient(create_app())


def _star(client: TestClient, path: str) -> None:
    resp = client.post("/api/wiki/starred", json={"path": path})
    assert resp.status_code == 204


def _paths(client: TestClient) -> list[str]:
    resp = client.get("/api/wiki/starred")
    assert resp.status_code == 200
    return resp.json()["paths"]


def test_unauthenticated_is_401(client):
    assert client.get("/api/wiki/starred").status_code == 401
    assert client.post("/api/wiki/starred", json={"path": "a.md"}).status_code == 401
    assert client.delete("/api/wiki/starred?path=a.md").status_code == 401
    assert client.put("/api/wiki/starred", json={"paths": []}).status_code == 401


def test_star_appends_in_order(client):
    uid = users_repo.create(email="alice@x.com", password="hunter2-x", name="Alice")
    login_fastapi(client, uid)
    for p in ("a.md", "b.md", "c.md"):
        wiki_git.commit_file(p, "# x", "seed")

    _star(client, "a.md")
    _star(client, "b.md")
    _star(client, "c.md")
    assert _paths(client) == ["a.md", "b.md", "c.md"]

    # Re-starring keeps the existing position, no duplicate.
    _star(client, "a.md")
    assert _paths(client) == ["a.md", "b.md", "c.md"]


def test_reorder_persists(client):
    uid = users_repo.create(email="alice@x.com", password="hunter2-x", name="Alice")
    login_fastapi(client, uid)
    for p in ("a.md", "b.md", "c.md"):
        wiki_git.commit_file(p, "# x", "seed")
        _star(client, p)

    resp = client.put("/api/wiki/starred", json={"paths": ["c.md", "a.md", "b.md"]})
    assert resp.status_code == 204
    assert _paths(client) == ["c.md", "a.md", "b.md"]

    # A later star still lands at the end of the new order.
    wiki_git.commit_file("d.md", "# d", "seed")
    _star(client, "d.md")
    assert _paths(client) == ["c.md", "a.md", "b.md", "d.md"]


def test_reorder_ignores_unknown_and_keeps_unlisted(client):
    """Stale reorders (concurrent star in another tab) must not drop
    rows: unlisted paths sink to the end, unknown paths are ignored."""
    uid = users_repo.create(email="alice@x.com", password="hunter2-x", name="Alice")
    login_fastapi(client, uid)
    for p in ("a.md", "b.md", "c.md"):
        wiki_git.commit_file(p, "# x", "seed")
        _star(client, p)

    resp = client.put(
        "/api/wiki/starred", json={"paths": ["b.md", "never-starred.md", "a.md"]}
    )
    assert resp.status_code == 204
    assert _paths(client) == ["b.md", "a.md", "c.md"]


def test_unstar_removes(client):
    uid = users_repo.create(email="alice@x.com", password="hunter2-x", name="Alice")
    login_fastapi(client, uid)
    for p in ("a.md", "b.md"):
        wiki_git.commit_file(p, "# x", "seed")
        _star(client, p)

    resp = client.delete("/api/wiki/starred?path=a.md")
    assert resp.status_code == 204
    assert _paths(client) == ["b.md"]


def test_starred_are_per_user(client):
    alice = users_repo.create(email="alice@x.com", password="hunter2-x", name="Alice")
    bob = users_repo.create(email="bob@x.com", password="hunter2-x", name="Bob")
    wiki_git.commit_file("a.md", "# a", "seed")

    login_fastapi(client, alice)
    _star(client, "a.md")

    login_fastapi(client, bob)
    assert _paths(client) == []


def test_deleted_docs_hidden(client):
    uid = users_repo.create(email="alice@x.com", password="hunter2-x", name="Alice")
    login_fastapi(client, uid)
    wiki_git.commit_file("keep.md", "# k", "seed")
    wiki_git.commit_file("gone.md", "# g", "seed")
    _star(client, "gone.md")
    _star(client, "keep.md")

    wiki_git.delete_path("gone.md", "remove")
    assert _paths(client) == ["keep.md"]


def test_star_requires_read_access_but_unstar_does_not(client):
    alice = users_repo.create(email="alice@x.com", password="hunter2-x", name="Alice")
    bob = users_repo.create(email="bob@x.com", password="hunter2-x", name="Bob")
    wiki_git.commit_file("private.md", "# p", "seed")

    # Bob stars while it's still public.
    login_fastapi(client, bob)
    _star(client, "private.md")

    # Owner locks it down afterwards.
    acl.set_owner("private.md", alice)
    for grant in acl.list_for_path("private.md"):
        if grant["principal_kind"] == "everyone":
            acl.revoke(grant["id"])

    # Hidden from the list, can't re-star, but can still remove the pin.
    assert _paths(client) == []
    assert (
        client.post("/api/wiki/starred", json={"path": "private.md"}).status_code == 403
    )
    assert client.delete("/api/wiki/starred?path=private.md").status_code == 204
    assert starred.list_paths(bob) == []


def test_star_rejects_traversal(client):
    uid = users_repo.create(email="alice@x.com", password="hunter2-x", name="Alice")
    login_fastapi(client, uid)
    assert (
        client.post("/api/wiki/starred", json={"path": "../etc/passwd"}).status_code
        == 400
    )
    assert client.put("/api/wiki/starred", json={"paths": ["../x"]}).status_code == 400


def test_star_cap(tmp_db, tmp_repo):
    uid = users_repo.create(email="alice@x.com", password="hunter2-x", name="Alice")
    for i in range(starred.STARRED_CAP + 3):
        starred.star(uid, f"doc-{i:03}.md")
    assert len(starred.list_paths(uid)) == starred.STARRED_CAP
