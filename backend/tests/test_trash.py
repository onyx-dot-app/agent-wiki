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


def test_trashed_folder_grant_repoints_when_files_nested(tmp_repo):
    # A folder visible only via a *folder-level* grant, whose only file sits in
    # a subdirectory. Trashing must re-point the folder's own grant to the trash
    # location (root_move) — otherwise it strands at the now-gone path and the
    # item is mis-authorized in Trash. Without the fix `other` sees nothing.
    owner = seed_user()
    other = seed_user(uid="u_o2", email="o2@x.com")
    oc = _client(owner)
    oc.put("/api/wiki/file", json={"path": "proj/sub/a.md", "body": "# A\n"})
    # Drop the page's own public grant so visibility hinges on the folder grant.
    for gid in _everyone_ids("proj/sub/a.md"):
        acl.revoke(gid)
    acl.grant(
        resource_kind="folder",
        resource_path="proj",
        principal_kind="everyone",
        principal_id=None,
        permission="read",
        granted_by_user_id=None,
    )

    tid = oc.delete("/api/wiki/file?path=proj").json()["trash_id"]

    other_c = _client(other)
    items = {i["path"]: i for i in other_c.get("/api/wiki/trash").json()["items"]}
    assert "proj" in items and items["proj"]["kind"] == "folder"
    assert other_c.get(f"/api/wiki/trash/{tid}").status_code == 200


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


def test_deleted_endpoint_returns_tombstone(tmp_repo):
    # The deleted-URL tombstone panel looks up a deleted page's Trash entry by
    # its original path to show who/when + offer Restore.
    user = seed_user()
    client = _client(user)
    client.put("/api/wiki/file", json={"path": "proj/note.md", "body": "# N\n"})
    tid = client.delete("/api/wiki/file?path=proj/note.md").json()["trash_id"]

    t = client.get("/api/wiki/deleted?path=proj/note.md")
    assert t.status_code == 200
    body = t.json()
    assert body["trash_id"] == tid
    assert body["path"] == "proj/note.md"
    assert body["kind"] == "page"
    assert body["can_restore"] is True


def test_deleted_endpoint_404_when_not_trashed(tmp_repo):
    user = seed_user()
    client = _client(user)
    client.put("/api/wiki/file", json={"path": "live.md", "body": "# L\n"})
    # A live page and a never-existed path are both "not deleted" → 404.
    assert client.get("/api/wiki/deleted?path=live.md").status_code == 404
    assert client.get("/api/wiki/deleted?path=never.md").status_code == 404


def test_deleted_endpoint_returns_newest_tombstone(tmp_repo):
    # Delete / recreate / delete → the endpoint returns the most-recent entry
    # (the one a Restore would bring back).
    user = seed_user()
    client = _client(user)
    client.put("/api/wiki/file", json={"path": "a.md", "body": "# v1\n"})
    client.delete("/api/wiki/file?path=a.md")
    client.put("/api/wiki/file", json={"path": "a.md", "body": "# v2\n"})
    tid2 = client.delete("/api/wiki/file?path=a.md").json()["trash_id"]

    assert client.get("/api/wiki/deleted?path=a.md").json()["trash_id"] == tid2


def test_deleted_tombstone_hidden_from_other_user(tmp_repo):
    owner = seed_user()
    other = seed_user(uid="u_t2", email="t2@x.com")
    oc = _client(owner)
    oc.put("/api/wiki/file", json={"path": "secret.md", "body": "# S\n"})
    for gid in _everyone_ids("secret.md"):
        acl.revoke(gid)
    oc.delete("/api/wiki/file?path=secret.md")

    # Owner sees the tombstone; another user can't (403, resolved against the
    # re-pointed trash-path ACL — no leak of a deleted private page).
    assert oc.get("/api/wiki/deleted?path=secret.md").status_code == 200
    assert _client(other).get("/api/wiki/deleted?path=secret.md").status_code == 403


def test_deleted_id_resolve_hidden_from_other_user(tmp_repo):
    # The id-URL resolve endpoint must not leak a deleted private page. Deleting
    # re-points the page's ACL to its trash location, leaving the original path
    # unmanaged (implicit-public) — so resolve must gate on the trash location,
    # not the bare path, or path/kind/deleted_at leak to anyone.
    owner = seed_user()
    other = seed_user(uid="u_t2", email="t2@x.com")
    oc = _client(owner)
    doc_id = oc.put("/api/wiki/file", json={"path": "secret.md", "body": "# S\n"}).json()[
        "id"
    ]
    for gid in _everyone_ids("secret.md"):
        acl.revoke(gid)
    oc.delete("/api/wiki/file?path=secret.md")

    # Owner still resolves the id to its tombstone; another user gets a 404 that
    # reveals nothing (no path, kind, or deletion time).
    owner_view = oc.get(f"/api/wiki/id/{doc_id}")
    assert owner_view.status_code == 200
    assert owner_view.json()["deleted_at"] is not None
    assert owner_view.json()["path"] == "secret.md"
    assert _client(other).get(f"/api/wiki/id/{doc_id}").status_code == 404


def test_deleted_id_resolve_denies_when_ambiguous_tombstones(tmp_repo):
    # A path can carry several tombstones (delete → recreate → delete the
    # enclosing folder), and the tombstone doesn't record which trash entry it
    # belongs to. The old id must stay hidden from a user who lacked access to
    # *its* delete, even when a newer same-path tombstone is public.
    owner = seed_user()
    other = seed_user(uid="u_amb", email="amb@x.com")
    oc = _client(owner)
    # Private page, deleted → tombstone #1 (private).
    old_id = oc.put(
        "/api/wiki/file", json={"path": "proj/secret.md", "body": "# S\n"}
    ).json()["id"]
    for gid in _everyone_ids("proj/secret.md"):
        acl.revoke(gid)
    oc.delete("/api/wiki/file?path=proj/secret.md")
    # Recreate at the same path (public), then trash the whole folder → a second,
    # public tombstone whose subtree also covers proj/secret.md.
    oc.put("/api/wiki/file", json={"path": "proj/secret.md", "body": "# new\n"})
    oc.delete("/api/wiki/file?path=proj")

    # The other user is blocked by the private entry even though the folder
    # tombstone covering the same path is public; the owner still resolves it.
    assert _client(other).get(f"/api/wiki/id/{old_id}").status_code == 404
    assert oc.get(f"/api/wiki/id/{old_id}").status_code == 200


def test_deleted_endpoint_404_when_path_recreated(tmp_repo):
    # Delete a.md, then recreate a live a.md. The old tombstone still exists in
    # Trash, but the path is live now → /wiki/deleted must report not-deleted
    # (else the panel shows stale delete metadata + a Restore that would 409).
    user = seed_user()
    client = _client(user)
    client.put("/api/wiki/file", json={"path": "a.md", "body": "# v1\n"})
    client.delete("/api/wiki/file?path=a.md")
    client.put("/api/wiki/file", json={"path": "a.md", "body": "# v2\n"})

    assert client.get("/api/wiki/deleted?path=a.md").status_code == 404
