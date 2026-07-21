"""Pending-cleanups read API — list/get proposals.

The **listing** is write-scoped (Path-2 review): a proposal is listed only to a
caller who can *write* every path it touches — the same envelope approve/reject
enforce, so the list is exactly what the caller could act on. A single-proposal
fetch stays read-scoped (viewing). ``?path=`` scopes the listing to proposals
touching a page/folder subtree — the on-page review banner.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.wiki import acl
from app.wiki.change_proposals import (
    ProposalCreatedVia,
    ProposalOp,
    create as create_proposal,
)
from tests._auth import login_fastapi
from tests._seed import seed_user


@pytest.fixture
def client(tmp_repo, tmp_config):
    return TestClient(create_app())


def _mk(folder: str) -> int:
    return create_proposal(
        op=ProposalOp.DELETE_EMPTY_FOLDER,
        source_paths=[folder],
        target_paths=[],
        base_shas={folder: "deadbeef"},
        summary=f"Delete empty folder “{folder}”",
        created_via=ProposalCreatedVia.SWEEP,
    )["id"]


def test_admin_lists_all_pending(client):
    uid = seed_user(uid="admin_1", email="a@x.com", is_admin=True)
    login_fastapi(client, uid)
    _mk("old")
    _mk("archive")
    resp = client.get("/api/automanage/proposals")
    assert resp.status_code == 200
    folders = {p["source_paths"][0] for p in resp.json()["proposals"]}
    assert folders == {"old", "archive"}


def test_get_one_and_404(client):
    uid = seed_user(uid="admin_1", email="a@x.com", is_admin=True)
    login_fastapi(client, uid)
    pid = _mk("old")
    got = client.get(f"/api/automanage/proposals/{pid}")
    assert got.status_code == 200
    assert got.json()["op"] == "delete_empty_folder"
    assert client.get("/api/automanage/proposals/999999").status_code == 404


def test_public_proposal_visible_to_any_user(client):
    seed_user(uid="admin_1", email="a@x.com", is_admin=True)  # first user = admin
    uid = seed_user(uid="usr_2", email="u2@x.com", is_admin=False)
    login_fastapi(client, uid)
    _mk("public-folder")  # no owner/ACL → implicit-public
    folders = {p["source_paths"][0] for p in client.get("/api/automanage/proposals").json()["proposals"]}
    assert "public-folder" in folders


def test_restricted_proposal_hidden_from_non_reader(client):
    admin = seed_user(uid="admin_1", email="a@x.com", is_admin=True)
    # Non-admin owner: exercises the ownership path in effective(), not the
    # admin-bypass short-circuit.
    owner = seed_user(uid="owner_1", email="o@x.com", is_admin=False)
    other = seed_user(uid="other_1", email="ot@x.com", is_admin=False)
    pid = _mk("restricted")
    acl.set_owner("restricted", owner)  # owner + admins only

    # The non-reader can't see it in the list, and gets 403 on direct fetch.
    login_fastapi(client, other)
    listed = {p["source_paths"][0] for p in client.get("/api/automanage/proposals").json()["proposals"]}
    assert "restricted" not in listed
    assert client.get(f"/api/automanage/proposals/{pid}").status_code == 403

    # The non-admin owner sees it via ownership.
    login_fastapi(client, owner)
    assert client.get(f"/api/automanage/proposals/{pid}").status_code == 200

    # And an admin sees it via bypass.
    login_fastapi(client, admin)
    assert client.get(f"/api/automanage/proposals/{pid}").status_code == 200


def test_read_only_user_excluded_from_list_but_can_view(client):
    # The listing is write-scoped: a reader (read but not write) can *view* a
    # proposal but it's not in their actionable list; the writer/owner sees it.
    seed_user(uid="admin_1", email="a@x.com", is_admin=True)  # first = admin
    owner = seed_user(uid="owner_1", email="o@x.com", is_admin=False)
    reader = seed_user(uid="reader_1", email="r@x.com", is_admin=False)
    pid = _mk("ro")
    acl.set_owner("ro", owner)  # manage the path (owner + admin write)
    acl.grant(
        resource_kind="folder",
        resource_path="ro",
        principal_kind="user",
        principal_id=reader,
        permission="read",
        granted_by_user_id=owner,
    )

    login_fastapi(client, reader)
    listed = {p["source_paths"][0] for p in client.get("/api/automanage/proposals").json()["proposals"]}
    assert "ro" not in listed  # read-only → not actionable, not listed
    assert client.get(f"/api/automanage/proposals/{pid}").status_code == 200  # but viewable

    login_fastapi(client, owner)
    listed = {p["source_paths"][0] for p in client.get("/api/automanage/proposals").json()["proposals"]}
    assert "ro" in listed  # writer sees it


def test_path_filter_scopes_to_subtree(client):
    uid = seed_user(uid="admin_1", email="a@x.com", is_admin=True)
    login_fastapi(client, uid)
    _mk("docs/old")
    _mk("archive")

    def folders(path=None):
        q = f"/api/automanage/proposals?path={path}" if path else "/api/automanage/proposals"
        return {p["source_paths"][0] for p in client.get(q).json()["proposals"]}

    assert folders() == {"docs/old", "archive"}  # no filter → all
    assert folders("docs") == {"docs/old"}  # ancestor folder → subtree match
    assert folders("docs/old") == {"docs/old"}  # exact
    assert folders("archive") == {"archive"}
    assert folders("nope") == set()  # no overlap
