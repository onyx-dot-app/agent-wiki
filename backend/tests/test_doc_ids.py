"""Stable doc ids: minted on create, follow moves, survive delete/restore.

The ``wiki_doc_ids`` mapping is maintained at the lifecycle seams
(``notify.after_doc_write/after_path_move/after_doc_delete`` + the folder
routes). A delete stamps ``deleted_at`` instead of dropping the row, so the
id still resolves and a restore re-binds it; a page recreated at the same
path is a new document with a fresh id.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app
from app.wiki import doc_ids
from app.wiki import git as wiki_git

from tests._auth import login_fastapi
from tests._seed import seed_user


def _client(user_id: str) -> TestClient:
    client = TestClient(create_app())
    login_fastapi(client, user_id)
    return client


def test_id_minted_on_create_and_stable_across_edits(tmp_repo):
    user = seed_user()
    client = _client(user)

    created = client.put("/api/wiki/file", json={"path": "a.md", "body": "# A\n"})
    assert created.status_code == 200
    doc_id = created.json()["id"]
    assert doc_id

    edited = client.put(
        "/api/wiki/file", json={"path": "a.md", "body": "# A2\n"}
    )
    assert edited.json()["id"] == doc_id
    assert client.get("/api/wiki/file?path=a.md").json()["id"] == doc_id


def test_page_create_seeds_ancestor_folder_ids(tmp_repo):
    user = seed_user()
    client = _client(user)
    client.put("/api/wiki/file", json={"path": "proj/sub/a.md", "body": "# A\n"})

    assert doc_ids.id_for_path("proj") is not None
    assert doc_ids.id_for_path("proj/sub") is not None


def test_id_follows_file_move(tmp_repo):
    user = seed_user()
    client = _client(user)
    doc_id = client.put("/api/wiki/file", json={"path": "a.md", "body": "# A\n"}).json()["id"]

    resp = client.post("/api/wiki/move", json={"old_path": "a.md", "new_path": "docs/b.md"})
    assert resp.status_code == 200

    assert doc_ids.id_for_path("docs/b.md") == doc_id
    assert doc_ids.id_for_path("a.md") is None
    resolved = client.get(f"/api/wiki/id/{doc_id}").json()
    assert resolved["path"] == "docs/b.md"
    assert resolved["deleted_at"] is None


def test_ids_follow_folder_move(tmp_repo):
    user = seed_user()
    client = _client(user)
    page_id = client.put(
        "/api/wiki/file", json={"path": "proj/sub/a.md", "body": "# A\n"}
    ).json()["id"]
    folder_id = doc_ids.id_for_path("proj")
    sub_id = doc_ids.id_for_path("proj/sub")

    resp = client.post("/api/wiki/move", json={"old_path": "proj", "new_path": "proj2"})
    assert resp.status_code == 200

    assert doc_ids.id_for_path("proj2") == folder_id
    assert doc_ids.id_for_path("proj2/sub") == sub_id
    assert doc_ids.id_for_path("proj2/sub/a.md") == page_id


def test_delete_keeps_id_as_tombstone_and_restore_rebinds(tmp_repo):
    user = seed_user()
    client = _client(user)
    doc_id = client.put("/api/wiki/file", json={"path": "a.md", "body": "# A\n"}).json()["id"]

    assert client.delete("/api/wiki/file?path=a.md").status_code == 200

    resolved = client.get(f"/api/wiki/id/{doc_id}").json()
    assert resolved["path"] == "a.md"
    assert resolved["deleted_at"] is not None
    assert doc_ids.id_for_path("a.md") is None

    assert client.post("/api/wiki/file/restore", json={"path": "a.md"}).status_code == 200
    assert doc_ids.id_for_path("a.md") == doc_id
    assert client.get(f"/api/wiki/id/{doc_id}").json()["deleted_at"] is None


def test_recreate_at_deleted_path_gets_fresh_id(tmp_repo):
    user = seed_user()
    client = _client(user)
    old_id = client.put("/api/wiki/file", json={"path": "a.md", "body": "# A\n"}).json()["id"]
    client.delete("/api/wiki/file?path=a.md")

    new_id = client.put("/api/wiki/file", json={"path": "a.md", "body": "# fresh\n"}).json()["id"]

    assert new_id != old_id
    # The old id still resolves — to its tombstone, not the new page.
    old = client.get(f"/api/wiki/id/{old_id}").json()
    assert old["deleted_at"] is not None
    assert client.get(f"/api/wiki/id/{new_id}").json()["deleted_at"] is None


def test_folder_delete_tombstones_folder_and_nested_ids(tmp_repo):
    user = seed_user()
    client = _client(user)
    page_id = client.put(
        "/api/wiki/file", json={"path": "proj/a.md", "body": "# A\n"}
    ).json()["id"]
    folder_id = doc_ids.id_for_path("proj")
    assert folder_id is not None

    client.delete("/api/wiki/file?path=proj")

    assert doc_ids.id_for_path("proj") is None
    assert client.get(f"/api/wiki/id/{folder_id}").json()["deleted_at"] is not None
    assert client.get(f"/api/wiki/id/{page_id}").json()["deleted_at"] is not None

    # Folder restore re-binds the folder and page ids alike.
    client.post("/api/wiki/file/restore", json={"path": "proj"})
    assert doc_ids.id_for_path("proj") == folder_id
    assert doc_ids.id_for_path("proj/a.md") == page_id


def test_nested_folder_ids_survive_delete_restore(tmp_repo):
    # Two-level layout: the intermediate folder proj/sub has its own id row,
    # which restore must resurrect rather than let mint_for_page mint fresh.
    user = seed_user()
    client = _client(user)
    page_id = client.put(
        "/api/wiki/file", json={"path": "proj/sub/a.md", "body": "# A\n"}
    ).json()["id"]
    proj_id = doc_ids.id_for_path("proj")
    sub_id = doc_ids.id_for_path("proj/sub")
    assert proj_id is not None and sub_id is not None

    client.delete("/api/wiki/file?path=proj")
    client.post("/api/wiki/file/restore", json={"path": "proj"})

    assert doc_ids.id_for_path("proj") == proj_id
    assert doc_ids.id_for_path("proj/sub") == sub_id
    assert doc_ids.id_for_path("proj/sub/a.md") == page_id


def test_read_by_id_and_lazy_backfill(tmp_repo):
    user = seed_user()
    client = _client(user)
    # Seed outside the API — no lifecycle hook, so no id row yet (pre-id page).
    wiki_git.commit_file("legacy.md", "# Legacy\n", "seed")
    assert doc_ids.id_for_path("legacy.md") is None

    read = client.get("/api/wiki/file?path=legacy.md")
    doc_id = read.json()["id"]
    assert doc_id  # reading minted the row

    by_id = client.get(f"/api/wiki/file?id={doc_id}")
    assert by_id.status_code == 200
    assert by_id.json()["path"] == "legacy.md"
    assert by_id.json()["body"] == "# Legacy\n"


def test_resolve_unknown_id_404(tmp_repo):
    user = seed_user()
    client = _client(user)
    assert client.get("/api/wiki/id/deadbeefdeadbeef").status_code == 404
    assert client.get("/api/wiki/file?id=deadbeefdeadbeef").status_code == 404


def test_listing_endpoints_carry_stable_id(tmp_repo):
    user = seed_user()
    client = _client(user)
    page_id = client.put(
        "/api/wiki/file", json={"path": "proj/a.md", "body": "# A\n"}
    ).json()["id"]

    # The tree listing is file-based (git ls-files), so it carries page ids;
    # implicit folders like ``proj`` aren't rows here (the frontend derives the
    # folder tree from paths and resolves a folder's own id separately).
    entries = {e["path"]: e["id"] for e in client.get("/api/wiki").json()["entries"]}
    assert entries["proj/a.md"] == page_id

    # Recents and starred expose the id alongside the legacy paths list.
    client.post("/api/wiki/recents", json={"path": "proj/a.md"})
    client.post("/api/wiki/starred", json={"path": "proj/a.md"})
    recents = client.get("/api/wiki/recents").json()
    starred = client.get("/api/wiki/starred").json()
    assert recents["paths"] == ["proj/a.md"]
    assert recents["items"] == [{"path": "proj/a.md", "id": page_id}]
    assert starred["items"] == [{"path": "proj/a.md", "id": page_id}]


def test_listing_id_is_none_for_unbackfilled_page(tmp_repo):
    # A page seeded outside the API has no id row yet; the tree still lists it,
    # with id=None, rather than failing.
    user = seed_user()
    client = _client(user)
    wiki_git.commit_file("legacy.md", "# L\n", "seed")
    entries = {e["path"]: e["id"] for e in client.get("/api/wiki").json()["entries"]}
    assert "legacy.md" in entries
    assert entries["legacy.md"] is None


def test_ids_for_paths_merges_across_chunks(tmp_repo, monkeypatch):
    # Force multiple chunks so the merge path is exercised without 1000+ rows.
    monkeypatch.setattr(doc_ids, "_ID_LOOKUP_CHUNK", 2)
    paths = [f"p{i}.md" for i in range(5)]
    want = {p: doc_ids.mint_for_page(p) for p in paths}
    # An absent path is simply omitted, not an error.
    got = doc_ids.ids_for_paths(paths + ["missing.md"])
    assert got == want


def test_resolve_ids_endpoint_returns_folder_and_page_ids(tmp_repo):
    user = seed_user()
    client = _client(user)
    page_id = client.put(
        "/api/wiki/file", json={"path": "proj/sub/a.md", "body": "# A\n"}
    ).json()["id"]
    proj_id = doc_ids.id_for_path("proj")
    sub_id = doc_ids.id_for_path("proj/sub")

    resp = client.post(
        "/api/wiki/resolve-ids",
        json={"paths": ["proj", "proj/sub", "proj/sub/a.md", "nope.md", ""]},
    )
    assert resp.status_code == 200
    got = {it["path"]: it["id"] for it in resp.json()["items"]}
    # Folder paths (absent from the file-based tree listing) resolve here.
    assert got == {"proj": proj_id, "proj/sub": sub_id, "proj/sub/a.md": page_id}


def test_resolve_ids_acl_filters_pages_for_non_admin(tmp_repo):
    from app.wiki import acl

    owner = seed_user()
    other = seed_user(uid="u_other", email="other@x.com")
    owner_client = _client(owner)
    owner_client.put("/api/wiki/file", json={"path": "secret.md", "body": "# S\n"})
    # Strip the default everyone-read grant → owner-only.
    for g in acl.list_for_path("secret.md"):
        if g["principal_kind"] == "everyone":
            acl.revoke(g["id"])

    resp = _client(other).post("/api/wiki/resolve-ids", json={"paths": ["secret.md"]})
    assert resp.status_code == 200
    # The page the caller can't read is omitted rather than leaked.
    assert resp.json()["items"] == []
