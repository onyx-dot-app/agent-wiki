"""Trash = soft delete via move into hidden `.trash/`.

Deleting a page/folder moves it to `.trash/<trash_id>/<original>` instead of
`git rm`. Because it's a move, path-keyed metadata (ACL, comments, …) is
re-pointed there and comes back on restore — so restore is lossless. The
`.trash/` area is hidden: excluded from listing/search and unreachable by
URL/API; the only ways in are the Trash view and restore.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app
from app.wiki import acl, trash

from tests._auth import login_fastapi
from tests._seed import seed_user


def _client(user_id: str) -> TestClient:
    client = TestClient(create_app())
    login_fastapi(client, user_id)
    return client


def _everyone_ids(path: str) -> list[str]:
    return [
        g["id"] for g in acl.list_for_path(path) if g["principal_kind"] == "everyone"
    ]


def test_delete_moves_to_trash_and_hides_everywhere(tmp_repo):
    user = seed_user()
    client = _client(user)
    client.put("/api/wiki/file", json={"path": "proj/a.md", "body": "# A\n"})

    resp = client.delete("/api/wiki/file?path=proj/a.md")
    assert resp.status_code == 200
    trash_id = resp.json()["trash_id"]
    assert trash_id

    # Gone from the live tree listing…
    tree = {e["path"] for e in client.get("/api/wiki").json()["entries"]}
    assert "proj/a.md" not in tree
    assert not any(p.startswith(".trash/") for p in tree)  # .trash never surfaces
    # …and unreachable by URL/API (hard-blocked regardless of ACL).
    assert client.get("/api/wiki/file?path=proj/a.md").status_code == 404
    trash_loc = trash.trash_location(trash_id, "proj/a.md")
    assert client.get(f"/api/wiki/file?path={trash_loc}").status_code == 400
    # …but present in Trash, at its original path.
    items = {i["path"]: i for i in client.get("/api/wiki/trash").json()["items"]}
    assert "proj/a.md" in items
    assert items["proj/a.md"]["trash_id"] == trash_id
    assert items["proj/a.md"]["kind"] == "page"


def test_view_trashed_page_returns_content(tmp_repo):
    user = seed_user()
    client = _client(user)
    client.put("/api/wiki/file", json={"path": "note.md", "body": "# Note\nhi"})
    tid = client.delete("/api/wiki/file?path=note.md").json()["trash_id"]

    view = client.get(f"/api/wiki/trash/{tid}").json()
    assert view["path"] == "note.md"
    assert view["body"] == "# Note\nhi"


def test_restore_moves_back_losslessly(tmp_repo):
    owner = seed_user()
    other = seed_user(uid="u_other", email="other@x.com")
    client = _client(owner)
    client.put("/api/wiki/file", json={"path": "doc.md", "body": "# Doc\n"})
    # A specific grant that must survive the delete/restore round-trip.
    acl.grant(
        resource_kind="page",
        resource_path="doc.md",
        principal_kind="user",
        principal_id=other,
        permission="write",
        granted_by_user_id=owner,
    )

    tid = client.delete("/api/wiki/file?path=doc.md").json()["trash_id"]
    assert client.post("/api/wiki/file/restore", json={"trash_id": tid}).status_code == 200

    # Content back…
    read = client.get("/api/wiki/file?path=doc.md")
    assert read.status_code == 200
    assert read.json()["body"] == "# Doc\n"
    # …and the grant came back with it (lossless — the move re-pointed it).
    grants = [
        (g["principal_kind"], g["principal_id"], g["permission"])
        for g in acl.list_for_path("doc.md")
    ]
    assert ("user", other, "write") in grants


def test_restore_refused_when_path_recreated(tmp_repo):
    user = seed_user()
    client = _client(user)
    client.put("/api/wiki/file", json={"path": "a.md", "body": "# A\n"})
    tid = client.delete("/api/wiki/file?path=a.md").json()["trash_id"]
    # Re-create a live page at the same path.
    client.put("/api/wiki/file", json={"path": "a.md", "body": "# new\n"})

    resp = client.post("/api/wiki/file/restore", json={"trash_id": tid})
    assert resp.status_code == 409


def test_folder_trash_and_restore(tmp_repo):
    user = seed_user()
    client = _client(user)
    client.put("/api/wiki/file", json={"path": "proj/a.md", "body": "# A\n"})
    client.put("/api/wiki/file", json={"path": "proj/sub/b.md", "body": "# B\n"})

    tid = client.delete("/api/wiki/file?path=proj").json()["trash_id"]
    items = {i["path"]: i for i in client.get("/api/wiki/trash").json()["items"]}
    assert items["proj"]["kind"] == "folder"
    assert client.get("/api/wiki/file?path=proj/a.md").status_code == 404

    assert client.post("/api/wiki/file/restore", json={"trash_id": tid}).status_code == 200
    assert client.get("/api/wiki/file?path=proj/a.md").json()["body"] == "# A\n"
    assert client.get("/api/wiki/file?path=proj/sub/b.md").json()["body"] == "# B\n"


def test_single_file_folder_classified_as_folder(tmp_repo):
    # A folder with exactly one .md file trashes to the same tree as trashing
    # that page directly (.trash/<id>/only/a.md). The recorded root — not the
    # file list — must decide: this is a folder, restore must recreate `only/`.
    user = seed_user()
    client = _client(user)
    client.put("/api/wiki/file", json={"path": "only/a.md", "body": "# A\n"})

    tid = client.delete("/api/wiki/file?path=only").json()["trash_id"]
    items = {i["path"]: i for i in client.get("/api/wiki/trash").json()["items"]}
    assert items["only"]["kind"] == "folder"  # not "only/a.md" / "page"

    view = client.get(f"/api/wiki/trash/{tid}").json()
    assert view["path"] == "only" and view["kind"] == "folder"

    assert client.post("/api/wiki/file/restore", json={"trash_id": tid}).status_code == 200
    assert client.get("/api/wiki/file?path=only/a.md").json()["body"] == "# A\n"


def test_trashed_private_page_hidden_from_other_user(tmp_repo):
    owner = seed_user()
    other = seed_user(uid="u_other", email="other@x.com")
    owner_client = _client(owner)
    owner_client.put("/api/wiki/file", json={"path": "secret.md", "body": "# S\n"})
    # Make it owner-only, then trash it — the grants re-point into .trash.
    for gid in _everyone_ids("secret.md"):
        acl.revoke(gid)
    tid = owner_client.delete("/api/wiki/file?path=secret.md").json()["trash_id"]

    other_client = _client(other)
    # The other user can't see it in Trash, view it, or restore it.
    assert other_client.get("/api/wiki/trash").json()["items"] == []
    assert other_client.get(f"/api/wiki/trash/{tid}").status_code == 403
    assert other_client.post("/api/wiki/file/restore", json={"trash_id": tid}).status_code == 403
    # The owner still can.
    assert any(
        i["trash_id"] == tid for i in owner_client.get("/api/wiki/trash").json()["items"]
    )


def test_duplicate_names_coexist_in_trash(tmp_repo):
    user = seed_user()
    client = _client(user)
    # Trash a.md, re-create a.md, trash again → two trashed "a.md" items.
    client.put("/api/wiki/file", json={"path": "a.md", "body": "# v1\n"})
    tid1 = client.delete("/api/wiki/file?path=a.md").json()["trash_id"]
    client.put("/api/wiki/file", json={"path": "a.md", "body": "# v2\n"})
    tid2 = client.delete("/api/wiki/file?path=a.md").json()["trash_id"]

    assert tid1 != tid2
    items = client.get("/api/wiki/trash").json()["items"]
    a_items = [i for i in items if i["path"] == "a.md"]
    assert len(a_items) == 2  # both coexist, distinct trash_ids
    # Each keeps its own content.
    assert client.get(f"/api/wiki/trash/{tid1}").json()["body"] == "# v1\n"
    assert client.get(f"/api/wiki/trash/{tid2}").json()["body"] == "# v2\n"

    # Path is free → restore one succeeds; then the path is occupied → the
    # other 409s (can't restore two items onto the same live path).
    assert client.post("/api/wiki/file/restore", json={"trash_id": tid1}).status_code == 200
    assert client.get("/api/wiki/file?path=a.md").json()["body"] == "# v1\n"
    assert client.post("/api/wiki/file/restore", json={"trash_id": tid2}).status_code == 409
