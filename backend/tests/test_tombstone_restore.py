"""Tombstone + restore for deleted/moved wiki paths.

``path_fate`` resolves a HEAD-absent path to its fate from git history —
deleted (with the ref where content is still readable) or moved (rename
chains followed to the current name). The tombstone endpoint exposes that;
the restore endpoint reintroduces a deleted file or folder as a new additive
commit and runs the create lifecycle per page, so the restorer becomes the
owner and search/triggers pick the pages back up.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app
from app.wiki import acl
from app.wiki import git as wiki_git

from tests._auth import login_fastapi
from tests._seed import seed_user


def _client(user_id: str) -> TestClient:
    client = TestClient(create_app())
    login_fastapi(client, user_id)
    return client


# --------------------------------------------------------------------------- #
# path_fate (git seam)                                                        #
# --------------------------------------------------------------------------- #


def test_path_fate_deleted_file(tmp_repo):
    wiki_git.commit_file("a.md", "# A\n", "seed")
    del_sha = wiki_git.delete_path("a.md", "delete a.md")

    fate = wiki_git.path_fate("a.md")

    assert fate is not None
    assert fate.status == "deleted"
    assert fate.sha == del_sha
    assert fate.path == "a.md"
    assert fate.last_content_sha == wiki_git.parent_sha(del_sha)
    assert wiki_git.read_file("a.md", ref=fate.last_content_sha or "") == "# A\n"


def test_path_fate_moved_file(tmp_repo):
    wiki_git.commit_file("a.md", "# A\n", "seed")
    wiki_git.move_path("a.md", "b.md", "move")

    fate = wiki_git.path_fate("a.md")

    assert fate is not None
    assert fate.status == "moved"
    assert fate.new_path == "b.md"


def test_path_fate_follows_move_then_delete_chain(tmp_repo):
    wiki_git.commit_file("a.md", "# A\n", "seed")
    wiki_git.move_path("a.md", "b.md", "move")
    del_sha = wiki_git.delete_path("b.md", "delete b.md")

    fate = wiki_git.path_fate("a.md")

    assert fate is not None
    assert fate.status == "deleted"
    assert fate.path == "b.md"  # restores at its final name
    assert fate.sha == del_sha


def test_path_fate_follows_move_chain_to_head_name(tmp_repo):
    wiki_git.commit_file("a.md", "# A\n", "seed")
    wiki_git.move_path("a.md", "b.md", "move 1")
    wiki_git.move_path("b.md", "c.md", "move 2")

    fate = wiki_git.path_fate("a.md")

    assert fate is not None
    assert fate.status == "moved"
    assert fate.new_path == "c.md"


def test_path_fate_folder_delete_and_move(tmp_repo):
    wiki_git.commit_file("proj/a.md", "# A\n", "seed a")
    wiki_git.commit_file("proj/sub/b.md", "# B\n", "seed b")
    wiki_git.move_path("proj", "proj2", "move folder")

    moved = wiki_git.path_fate("proj")
    assert moved is not None
    assert moved.status == "moved"
    assert moved.new_path == "proj2"

    del_sha = wiki_git.delete_path("proj2", "delete folder")
    deleted = wiki_git.path_fate("proj")
    assert deleted is not None
    assert deleted.status == "deleted"
    assert deleted.path == "proj2"
    assert deleted.sha == del_sha


def test_path_fate_none_for_unknown_or_existing(tmp_repo):
    wiki_git.commit_file("a.md", "# A\n", "seed")
    assert wiki_git.path_fate("never-existed.md") is None
    assert wiki_git.path_fate("a.md") is None  # exists at HEAD — not a tombstone


# --------------------------------------------------------------------------- #
# Tombstone endpoint                                                          #
# --------------------------------------------------------------------------- #


def test_tombstone_deleted_page(tmp_repo):
    user = seed_user()
    client = _client(user)
    wiki_git.commit_file("a.md", "# A\n", "seed")
    del_sha = wiki_git.delete_path("a.md", "delete a.md")

    resp = client.get("/api/wiki/file/tombstone?path=a.md")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "deleted"
    assert data["commit"]["sha"] == del_sha
    assert data["can_restore"] is True
    # The advertised ref serves the pre-delete body through the read endpoint.
    read = client.get(f"/api/wiki/file?path=a.md&ref={data['last_content_sha']}")
    assert read.status_code == 200
    assert read.json()["body"] == "# A\n"


def test_tombstone_moved_page(tmp_repo):
    user = seed_user()
    client = _client(user)
    wiki_git.commit_file("a.md", "# A\n", "seed")
    wiki_git.move_path("a.md", "docs/b.md", "move")

    resp = client.get("/api/wiki/file/tombstone?path=a.md")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "moved"
    assert data["moved_to"] == "docs/b.md"


def test_tombstone_unknown_and_existing_paths(tmp_repo):
    user = seed_user()
    client = _client(user)
    wiki_git.commit_file("a.md", "# A\n", "seed")

    assert client.get("/api/wiki/file/tombstone?path=nope.md").status_code == 404
    assert client.get("/api/wiki/file/tombstone?path=a.md").status_code == 409


# --------------------------------------------------------------------------- #
# Restore endpoint                                                            #
# --------------------------------------------------------------------------- #


def test_restore_deleted_page_roundtrip(tmp_repo):
    user = seed_user()
    client = _client(user)
    wiki_git.commit_file("a.md", "# A\n", "seed")
    wiki_git.delete_path("a.md", "delete a.md")

    resp = client.post("/api/wiki/file/restore", json={"path": "a.md"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["path"] == "a.md"
    assert data["restored"] == ["a.md"]
    read = client.get("/api/wiki/file?path=a.md")
    assert read.status_code == 200
    assert read.json()["body"] == "# A\n"
    # Create lifecycle ran: the restorer owns the page now.
    assert acl.get_owner("a.md") == user
    # The delete stays in history — restore is additive, not a rewrite.
    messages = [c.message for c in wiki_git.history("a.md")]
    assert "delete a.md" in messages
    # Nothing left to restore at this path.
    assert client.post("/api/wiki/file/restore", json={"path": "a.md"}).status_code == 409


def test_restore_folder_restores_nested_pages(tmp_repo):
    user = seed_user()
    client = _client(user)
    wiki_git.commit_file("proj/a.md", "# A\n", "seed a")
    wiki_git.commit_file("proj/sub/b.md", "# B\n", "seed b")
    wiki_git.delete_path("proj", "delete folder")

    resp = client.post("/api/wiki/file/restore", json={"path": "proj"})

    assert resp.status_code == 200
    assert sorted(resp.json()["restored"]) == ["proj/a.md", "proj/sub/b.md"]
    for p, body in (("proj/a.md", "# A\n"), ("proj/sub/b.md", "# B\n")):
        read = client.get(f"/api/wiki/file?path={p}")
        assert read.status_code == 200
        assert read.json()["body"] == body
        assert acl.get_owner(p) == user


def test_restore_moved_page_refused_with_pointer(tmp_repo):
    user = seed_user()
    client = _client(user)
    wiki_git.commit_file("a.md", "# A\n", "seed")
    wiki_git.move_path("a.md", "b.md", "move")

    resp = client.post("/api/wiki/file/restore", json={"path": "a.md"})

    assert resp.status_code == 409
    assert "b.md" in resp.json()["error"]


def test_restore_moved_then_deleted_restores_at_final_name(tmp_repo):
    user = seed_user()
    client = _client(user)
    wiki_git.commit_file("a.md", "# A\n", "seed")
    wiki_git.move_path("a.md", "b.md", "move")
    wiki_git.delete_path("b.md", "delete b.md")

    resp = client.post("/api/wiki/file/restore", json={"path": "a.md"})

    assert resp.status_code == 200
    assert resp.json()["path"] == "b.md"
    read = client.get("/api/wiki/file?path=b.md")
    assert read.status_code == 200
    assert read.json()["body"] == "# A\n"


# --------------------------------------------------------------------------- #
# Trash listing                                                               #
# --------------------------------------------------------------------------- #


def test_trash_lists_deleted_pages(tmp_repo):
    user = seed_user()
    client = _client(user)
    wiki_git.commit_file("keep.md", "# Keep\n", "seed")
    wiki_git.commit_file("gone.md", "# Gone\n", "seed")
    wiki_git.delete_path("gone.md", "delete gone.md")

    items = client.get("/api/wiki/trash").json()["items"]
    paths = {i["path"]: i for i in items}
    assert "gone.md" in paths
    assert "keep.md" not in paths  # live pages aren't trash
    assert paths["gone.md"]["kind"] == "page"
    # The advertised ref serves the pre-delete body.
    read = client.get(
        f"/api/wiki/file?path=gone.md&ref={paths['gone.md']['last_content_sha']}"
    )
    assert read.status_code == 200
    assert read.json()["body"] == "# Gone\n"


def test_trash_drops_recreated_and_restored_paths(tmp_repo):
    user = seed_user()
    client = _client(user)
    # Deleted then re-created at the same path → not in trash (live again).
    wiki_git.commit_file("a.md", "# A\n", "seed")
    wiki_git.delete_path("a.md", "delete a.md")
    client.put("/api/wiki/file", json={"path": "a.md", "body": "# A2\n"})

    # Deleted then restored → not in trash.
    wiki_git.commit_file("b.md", "# B\n", "seed")
    wiki_git.delete_path("b.md", "delete b.md")
    client.post("/api/wiki/file/restore", json={"path": "b.md"})

    # Deleted and still gone → in trash.
    wiki_git.commit_file("c.md", "# C\n", "seed")
    wiki_git.delete_path("c.md", "delete c.md")

    paths = {i["path"] for i in client.get("/api/wiki/trash").json()["items"]}
    assert "c.md" in paths
    assert "a.md" not in paths
    assert "b.md" not in paths
