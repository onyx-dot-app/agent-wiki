"""End-to-end permission tests through the Flask client.

Exercises the enforcement seam (``require_can``) against real API
routes: PUT/GET/DELETE on ``/api/documents/file``, search filtering,
and admin override. Two users + a private page is enough to surface
every interesting branch.
"""
from __future__ import annotations

from app.wiki import acl


def test_creator_becomes_owner_and_others_can_access_default_public(integration):
    alice = integration.signup(email="alice@x.com")
    integration.put_doc("docs/spec.md", "# Spec\n\nbody.")
    # Owner should be Alice.
    assert acl.get_owner("docs/spec.md") == alice

    # Bob (different account) can read because the page is default-public.
    integration.signup(email="bob@x.com")
    integration.signin(email="bob@x.com")
    resp = integration.client.get("/api/documents/file?path=docs/spec.md")
    assert resp.status_code == 200
    assert resp.get_json()["body"] == "# Spec\n\nbody."


def test_revoking_everyone_grant_makes_page_private(integration):
    alice = integration.signup(email="alice@x.com")
    integration.put_doc("docs/private.md", "# Secret")

    # Alice strips the everyone grants — page is now owner-only.
    grants = acl.list_for_path("docs/private.md")
    for g in grants:
        if g["principal_kind"] == "everyone":
            acl.revoke(g["id"])

    integration.signup(email="bob@x.com")
    integration.signin(email="bob@x.com")
    # Bob can no longer read or write the page.
    resp = integration.client.get("/api/documents/file?path=docs/private.md")
    assert resp.status_code == 403, resp.get_data(as_text=True)
    resp = integration.client.put(
        "/api/documents/file", json={"path": "docs/private.md", "body": "hijacked"}
    )
    assert resp.status_code == 403

    # Alice still has access.
    integration.signin(user_id=alice)
    resp = integration.client.get("/api/documents/file?path=docs/private.md")
    assert resp.status_code == 200


def test_explicit_user_grant_lets_other_user_in(integration):
    alice = integration.signup(email="alice@x.com")
    integration.put_doc("docs/shared.md", "# Shared")
    # Strip everyone grants so the page is private.
    for g in acl.list_for_path("docs/shared.md"):
        if g["principal_kind"] == "everyone":
            acl.revoke(g["id"])

    bob = integration.signup(email="bob@x.com")
    # Alice grants Bob read.
    acl.grant(
        resource_kind="page",
        resource_path="docs/shared.md",
        principal_kind="user",
        principal_id=bob,
        permission="read",
        granted_by_user_id=alice,
    )

    integration.signin(user_id=bob)
    # Bob can read but not write.
    resp = integration.client.get("/api/documents/file?path=docs/shared.md")
    assert resp.status_code == 200
    resp = integration.client.put(
        "/api/documents/file", json={"path": "docs/shared.md", "body": "edited"}
    )
    assert resp.status_code == 403


def test_admin_bypasses_per_page_acls(integration):
    """Admin can read/edit a page they don't own and weren't granted on."""
    # First user is auto-admin.
    admin = integration.signup(email="admin@x.com")
    # Second user owns a private page.
    bob = integration.signup(email="bob@x.com")
    integration.signin(user_id=bob)
    integration.put_doc("bob/notes.md", "# Bob's notes")
    for g in acl.list_for_path("bob/notes.md"):
        if g["principal_kind"] == "everyone":
            acl.revoke(g["id"])

    integration.signin(user_id=admin)
    # Admin can read.
    resp = integration.client.get("/api/documents/file?path=bob/notes.md")
    assert resp.status_code == 200
    # Admin can edit.
    resp = integration.client.put(
        "/api/documents/file",
        json={"path": "bob/notes.md", "body": "# Bob's notes\n\nadmin tweaked"},
    )
    assert resp.status_code in (200, 201)


def test_search_filters_out_unauthorized_hits(integration):
    alice = integration.signup(email="alice@x.com")
    integration.put_doc("docs/public.md", "# Public\n\nfindme keyword")
    integration.put_doc("docs/private.md", "# Private\n\nfindme keyword")
    # Make the second page private to Alice only.
    for g in acl.list_for_path("docs/private.md"):
        if g["principal_kind"] == "everyone":
            acl.revoke(g["id"])

    # New user signed up — can search.
    integration.signup(email="bob@x.com")
    integration.signin(email="bob@x.com")
    resp = integration.client.get("/api/documents/search?q=findme")
    assert resp.status_code == 200
    paths = {h["path"] for h in resp.get_json()["hits"]}
    assert paths == {"docs/public.md"}

    # Alice (the owner) sees both.
    integration.signin(user_id=alice)
    resp = integration.client.get("/api/documents/search?q=findme")
    paths = {h["path"] for h in resp.get_json()["hits"]}
    assert paths == {"docs/public.md", "docs/private.md"}


def test_list_documents_hides_unauthorized_pages(integration):
    alice = integration.signup(email="alice@x.com")
    integration.put_doc("docs/a.md", "a")
    integration.put_doc("docs/b.md", "b")
    for g in acl.list_for_path("docs/b.md"):
        if g["principal_kind"] == "everyone":
            acl.revoke(g["id"])

    # Bob can see a but not b.
    integration.signup(email="bob@x.com")
    integration.signin(email="bob@x.com")
    resp = integration.client.get("/api/documents")
    assert resp.status_code == 200
    md_paths = {e["path"] for e in resp.get_json()["entries"] if e["path"].endswith(".md")}
    assert "docs/a.md" in md_paths
    assert "docs/b.md" not in md_paths

    # Alice sees both.
    integration.signin(user_id=alice)
    resp = integration.client.get("/api/documents")
    md_paths = {e["path"] for e in resp.get_json()["entries"] if e["path"].endswith(".md")}
    assert {"docs/a.md", "docs/b.md"} <= md_paths


# --------------------------------------------------------------------------- #
# Read-only vs write grants                                                   #
# --------------------------------------------------------------------------- #


def test_read_only_grant_denies_write(integration):
    """A user granted only ``read`` on a private page can GET but not PUT."""
    integration.signup(email="admin@x.com")  # consume auto-admin slot
    alice = integration.signup(email="alice@x.com")
    bob = integration.signup(email="bob@x.com")
    integration.signin(user_id=alice)
    integration.put_doc("docs/spec.md", "# Spec")
    # Make private.
    for g in acl.list_for_path("docs/spec.md"):
        if g["principal_kind"] == "everyone":
            acl.revoke(g["id"])
    # Grant Bob read only.
    acl.grant(
        resource_kind="page",
        resource_path="docs/spec.md",
        principal_kind="user",
        principal_id=bob,
        permission="read",
        granted_by_user_id=alice,
    )

    integration.signin(user_id=bob)
    r = integration.client.get("/api/documents/file?path=docs/spec.md")
    assert r.status_code == 200
    r = integration.client.put(
        "/api/documents/file", json={"path": "docs/spec.md", "body": "tampered"}
    )
    assert r.status_code == 403


def test_read_public_write_private(integration):
    """A page with only ``everyone read`` (no write) is world-readable
    but only the owner / admin can edit."""
    integration.signup(email="admin@x.com")
    alice = integration.signup(email="alice@x.com")
    bob = integration.signup(email="bob@x.com")
    integration.signin(user_id=alice)
    integration.put_doc("docs/announce.md", "# Announce")
    # Strip the write grant only — keep read.
    for g in acl.list_for_path("docs/announce.md"):
        if g["principal_kind"] == "everyone" and g["permission"] == "write":
            acl.revoke(g["id"])

    integration.signin(user_id=bob)
    r = integration.client.get("/api/documents/file?path=docs/announce.md")
    assert r.status_code == 200
    r = integration.client.put(
        "/api/documents/file",
        json={"path": "docs/announce.md", "body": "hijacked"},
    )
    assert r.status_code == 403

    # Alice (owner) can still write.
    integration.signin(user_id=alice)
    r = integration.client.put(
        "/api/documents/file",
        json={"path": "docs/announce.md", "body": "# Announce v2"},
    )
    assert r.status_code in (200, 201)


# --------------------------------------------------------------------------- #
# Folder-level grants                                                         #
# --------------------------------------------------------------------------- #


def test_folder_grant_to_group_lets_members_read_descendants(integration):
    """Sharing a folder with a group should make every page underneath
    accessible to group members and non-members alike — but only after
    we revoke the default-public grants on each page (so the folder
    grant becomes the actual policy)."""
    admin = integration.signup(email="admin@x.com")
    alice = integration.signup(email="alice@x.com")
    bob = integration.signup(email="bob@x.com")
    eve = integration.signup(email="eve@x.com")
    integration.signin(user_id=alice)
    integration.put_doc("team/alpha.md", "alpha")
    integration.put_doc("team/sub/beta.md", "beta")
    # Lock down both pages so the folder grant becomes the access rule.
    for p in ("team/alpha.md", "team/sub/beta.md"):
        for g in acl.list_for_path(p):
            if g["principal_kind"] == "everyone":
                acl.revoke(g["id"])

    integration.signin(user_id=admin)
    gid = integration.client.post("/api/groups", json={"name": "team"}).get_json()["id"]
    integration.client.post(f"/api/groups/{gid}/members", json={"user_id": bob})

    # Folder grant — admin grants the team folder to the team group.
    r = integration.client.post("/api/wiki/acl", json={
        "resource_kind": "folder",
        "resource_path": "team",
        "principal_kind": "group",
        "principal_id": gid,
        "permission": "read",
    })
    assert r.status_code == 201

    # Bob (group member) can read both descendants.
    integration.signin(user_id=bob)
    for p in ("team/alpha.md", "team/sub/beta.md"):
        r = integration.client.get(f"/api/documents/file?path={p}")
        assert r.status_code == 200, p

    # Eve (not a member) is denied.
    integration.signin(user_id=eve)
    r = integration.client.get("/api/documents/file?path=team/alpha.md")
    assert r.status_code == 403


# --------------------------------------------------------------------------- #
# Page lifecycle: rename + delete + recreate                                  #
# --------------------------------------------------------------------------- #


def test_rename_preserves_owner_and_grants(integration):
    integration.signup(email="admin@x.com")
    alice = integration.signup(email="alice@x.com")
    bob = integration.signup(email="bob@x.com")
    integration.signin(user_id=alice)
    integration.put_doc("docs/old.md", "# Old")
    # Strip default-public; grant Bob read.
    for g in acl.list_for_path("docs/old.md"):
        if g["principal_kind"] == "everyone":
            acl.revoke(g["id"])
    acl.grant(
        resource_kind="page",
        resource_path="docs/old.md",
        principal_kind="user",
        principal_id=bob,
        permission="read",
        granted_by_user_id=alice,
    )

    # Rename via the API.
    r = integration.client.post(
        "/api/documents/move",
        json={"old_path": "docs/old.md", "new_path": "docs/new.md"},
    )
    assert r.status_code == 200, r.get_data(as_text=True)

    # Owner moved with the page; ACL grant moved too.
    assert acl.get_owner("docs/old.md") is None
    assert acl.get_owner("docs/new.md") == alice
    integration.signin(user_id=bob)
    r = integration.client.get("/api/documents/file?path=docs/new.md")
    assert r.status_code == 200


def test_delete_then_recreate_does_not_inherit_old_grants(integration):
    """Deleting a page should drop its owner row + page-level ACLs.
    Recreating at the same path starts fresh (default-public)."""
    integration.signup(email="admin@x.com")
    alice = integration.signup(email="alice@x.com")
    bob = integration.signup(email="bob@x.com")
    integration.signin(user_id=alice)
    integration.put_doc("docs/spec.md", "# Spec")
    # Make private + grant Bob.
    for g in acl.list_for_path("docs/spec.md"):
        if g["principal_kind"] == "everyone":
            acl.revoke(g["id"])
    acl.grant(
        resource_kind="page",
        resource_path="docs/spec.md",
        principal_kind="user",
        principal_id=bob,
        permission="read",
        granted_by_user_id=alice,
    )

    # Delete.
    r = integration.client.delete("/api/documents/file?path=docs/spec.md")
    assert r.status_code == 200
    assert acl.get_owner("docs/spec.md") is None
    assert acl.list_for_path("docs/spec.md") == []

    # Recreate by another user — they're the new owner; Bob's old grant is gone.
    integration.signin(user_id=bob)
    integration.put_doc("docs/spec.md", "# new content")
    assert acl.get_owner("docs/spec.md") == bob
    grants = acl.list_for_path("docs/spec.md")
    user_grants = [g for g in grants if g["principal_kind"] == "user"]
    assert user_grants == []  # no orphan grant for Bob (he's owner; no row)


# --------------------------------------------------------------------------- #
# Search visibility nuances                                                   #
# --------------------------------------------------------------------------- #


def test_search_returns_hits_via_group_grant(integration):
    admin = integration.signup(email="admin@x.com")
    alice = integration.signup(email="alice@x.com")
    bob = integration.signup(email="bob@x.com")
    integration.signin(user_id=alice)
    integration.put_doc("private/note.md", "# Note\n\nfindme keyword")
    for g in acl.list_for_path("private/note.md"):
        if g["principal_kind"] == "everyone":
            acl.revoke(g["id"])

    # Group grant.
    integration.signin(user_id=admin)
    gid = integration.client.post("/api/groups", json={"name": "team"}).get_json()["id"]
    integration.client.post(f"/api/groups/{gid}/members", json={"user_id": bob})
    integration.client.post("/api/wiki/acl", json={
        "resource_kind": "page",
        "resource_path": "private/note.md",
        "principal_kind": "group",
        "principal_id": gid,
        "permission": "read",
    })

    integration.signin(user_id=bob)
    resp = integration.client.get("/api/documents/search?q=findme")
    paths = {h["path"] for h in resp.get_json()["hits"]}
    assert "private/note.md" in paths


def test_search_returns_hits_via_folder_cascade(integration):
    integration.signup(email="admin@x.com")
    alice = integration.signup(email="alice@x.com")
    bob = integration.signup(email="bob@x.com")
    integration.signin(user_id=alice)
    integration.put_doc("zone/a.md", "# A\n\nzonekeyword")
    integration.put_doc("zone/sub/b.md", "# B\n\nzonekeyword")
    # Lock both pages — folder grant should be what lets Bob in.
    for p in ("zone/a.md", "zone/sub/b.md"):
        for g in acl.list_for_path(p):
            if g["principal_kind"] == "everyone":
                acl.revoke(g["id"])
    acl.grant(
        resource_kind="folder",
        resource_path="zone",
        principal_kind="user",
        principal_id=bob,
        permission="read",
        granted_by_user_id=alice,
    )

    integration.signin(user_id=bob)
    resp = integration.client.get("/api/documents/search?q=zonekeyword")
    paths = {h["path"] for h in resp.get_json()["hits"]}
    assert paths == {"zone/a.md", "zone/sub/b.md"}


# --------------------------------------------------------------------------- #
# Sharing rights — write-access can share, read-access cannot                 #
# --------------------------------------------------------------------------- #


def test_writer_can_share_and_change_acl(integration):
    """A non-owner with write access can list, grant, and revoke ACL
    entries on the page (matches the share UI's expectation that
    editors can manage access)."""
    integration.signup(email="admin@x.com")
    alice = integration.signup(email="alice@x.com")
    bob = integration.signup(email="bob@x.com")
    carol = integration.signup(email="carol@x.com")
    integration.signin(user_id=alice)
    integration.put_doc("docs/spec.md", "# Spec")
    # Lock the page down so the per-user grants are the only access.
    for g in acl.list_for_path("docs/spec.md"):
        if g["principal_kind"] == "everyone":
            acl.revoke(g["id"])
    # Grant Bob write (Bob is not the owner).
    acl.grant(
        resource_kind="page",
        resource_path="docs/spec.md",
        principal_kind="user",
        principal_id=bob,
        permission="write",
        granted_by_user_id=alice,
    )

    integration.signin(user_id=bob)

    # Bob can list the ACL.
    r = integration.client.get("/api/wiki/acl?path=docs/spec.md")
    assert r.status_code == 200, r.get_data(as_text=True)

    # Bob can grant Carol read access.
    r = integration.client.post(
        "/api/wiki/acl",
        json={
            "resource_kind": "page",
            "resource_path": "docs/spec.md",
            "principal_kind": "user",
            "principal_id": carol,
            "permission": "read",
        },
    )
    assert r.status_code == 201, r.get_data(as_text=True)
    new_eid = r.get_json()["id"]

    # Carol can now actually read the page.
    integration.signin(user_id=carol)
    r = integration.client.get("/api/documents/file?path=docs/spec.md")
    assert r.status_code == 200

    # Bob can revoke that grant too.
    integration.signin(user_id=bob)
    r = integration.client.delete(f"/api/wiki/acl/{new_eid}")
    assert r.status_code == 204

    # Carol is back to no-access.
    integration.signin(user_id=carol)
    r = integration.client.get("/api/documents/file?path=docs/spec.md")
    assert r.status_code == 403


def test_reader_cannot_share_or_change_acl(integration):
    """A user with only read access cannot list, grant, revoke, or
    transfer ownership — read-only really means read-only."""
    integration.signup(email="admin@x.com")
    alice = integration.signup(email="alice@x.com")
    bob = integration.signup(email="bob@x.com")
    carol = integration.signup(email="carol@x.com")
    integration.signin(user_id=alice)
    integration.put_doc("docs/spec.md", "# Spec")
    for g in acl.list_for_path("docs/spec.md"):
        if g["principal_kind"] == "everyone":
            acl.revoke(g["id"])
    # Bob gets read-only.
    acl.grant(
        resource_kind="page",
        resource_path="docs/spec.md",
        principal_kind="user",
        principal_id=bob,
        permission="read",
        granted_by_user_id=alice,
    )
    bob_grant_id = next(
        g["id"]
        for g in acl.list_for_path("docs/spec.md")
        if g.get("principal_id") == bob
    )

    integration.signin(user_id=bob)

    # Bob can read the page.
    r = integration.client.get("/api/documents/file?path=docs/spec.md")
    assert r.status_code == 200

    # But cannot list its ACL.
    r = integration.client.get("/api/wiki/acl?path=docs/spec.md")
    assert r.status_code == 403, r.get_data(as_text=True)

    # Cannot grant Carol access.
    r = integration.client.post(
        "/api/wiki/acl",
        json={
            "resource_kind": "page",
            "resource_path": "docs/spec.md",
            "principal_kind": "user",
            "principal_id": carol,
            "permission": "read",
        },
    )
    assert r.status_code == 403

    # Cannot revoke an existing entry (even one targeting himself).
    r = integration.client.delete(f"/api/wiki/acl/{bob_grant_id}")
    assert r.status_code == 403

    # Cannot transfer ownership.
    r = integration.client.post(
        "/api/wiki/transfer-ownership",
        json={"path": "docs/spec.md", "new_owner_user_id": bob},
    )
    assert r.status_code == 403

    # Sanity: Alice (owner) is still the owner; Bob's grant still exists.
    assert acl.get_owner("docs/spec.md") == alice
    assert any(
        g["id"] == bob_grant_id for g in acl.list_for_path("docs/spec.md")
    )


def test_writer_cannot_transfer_ownership(integration):
    """Write-access shares the ACL but does NOT include yanking
    ownership — transfer stays owner-or-admin."""
    integration.signup(email="admin@x.com")
    alice = integration.signup(email="alice@x.com")
    bob = integration.signup(email="bob@x.com")
    integration.signin(user_id=alice)
    integration.put_doc("docs/spec.md", "# Spec")
    for g in acl.list_for_path("docs/spec.md"):
        if g["principal_kind"] == "everyone":
            acl.revoke(g["id"])
    acl.grant(
        resource_kind="page",
        resource_path="docs/spec.md",
        principal_kind="user",
        principal_id=bob,
        permission="write",
        granted_by_user_id=alice,
    )

    integration.signin(user_id=bob)
    r = integration.client.post(
        "/api/wiki/transfer-ownership",
        json={"path": "docs/spec.md", "new_owner_user_id": bob},
    )
    assert r.status_code == 403
    assert acl.get_owner("docs/spec.md") == alice
