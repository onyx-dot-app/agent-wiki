"""HTTP-level tests for /api/groups, /api/wiki/acl, and transfer-ownership.

Phase 3 covers the *enforcement* boundary (existing wiki routes refusing
unauthorized callers); these tests cover the new mutation surface that
admins and owners use to manage permissions.
"""
from __future__ import annotations


def _strip_everyone(integration, path: str) -> None:
    """Helper: revoke the seeded ``everyone`` grants so the page becomes
    private for sharing tests."""
    resp = integration.client.get(f"/api/wiki/acl?path={path}")
    assert resp.status_code == 200, resp.text
    for entry in resp.json()["entries"]:
        if entry["principal_kind"] == "everyone" and entry["resource_kind"] == "page":
            r = integration.client.delete(f"/api/wiki/acl/{entry['id']}")
            assert r.status_code == 204


# --------------------------------------------------------------------------- #
# Groups CRUD                                                                 #
# --------------------------------------------------------------------------- #


def test_admin_can_create_and_list_groups(integration):
    integration.signup(email="admin@x.com")  # auto-admin (first user)
    resp = integration.client.post("/api/groups", json={"name": "eng", "description": "team"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "eng"
    assert body["id"].startswith("grp_")

    resp = integration.client.get("/api/groups")
    assert resp.status_code == 200
    names = [g["name"] for g in resp.json()["groups"]]
    assert names == ["eng"]


def test_non_admin_cannot_create_groups(integration):
    integration.signup(email="admin@x.com")  # consumes the auto-admin slot
    integration.signup(email="bob@x.com")
    integration.signin(email="bob@x.com")
    resp = integration.client.post("/api/groups", json={"name": "eng"})
    assert resp.status_code == 403


def test_member_addition_and_listing(integration):
    integration.signup(email="admin@x.com")
    bob = integration.signup(email="bob@x.com")
    integration.signin(email="admin@x.com")

    resp = integration.client.post("/api/groups", json={"name": "eng"})
    gid = resp.json()["id"]

    resp = integration.client.post(f"/api/groups/{gid}/members", json={"user_id": bob})
    assert resp.status_code == 204

    resp = integration.client.get(f"/api/groups/{gid}")
    assert resp.status_code == 200
    member_ids = [m["id"] for m in resp.json()["members"]]
    assert bob in member_ids

    resp = integration.client.delete(f"/api/groups/{gid}/members/{bob}")
    assert resp.status_code == 204
    resp = integration.client.get(f"/api/groups/{gid}")
    assert resp.json()["members"] == []


def test_user_sees_only_their_groups(integration):
    integration.signup(email="admin@x.com")
    alice = integration.signup(email="alice@x.com")
    bob = integration.signup(email="bob@x.com")
    integration.signin(email="admin@x.com")

    a_id = integration.client.post("/api/groups", json={"name": "a-team"}).json()["id"]
    b_id = integration.client.post("/api/groups", json={"name": "b-team"}).json()["id"]
    integration.client.post(f"/api/groups/{a_id}/members", json={"user_id": alice})
    integration.client.post(f"/api/groups/{b_id}/members", json={"user_id": bob})

    integration.signin(user_id=alice)
    resp = integration.client.get("/api/groups")
    assert resp.status_code == 200
    names = {g["name"] for g in resp.json()["groups"]}
    assert names == {"a-team"}


# --------------------------------------------------------------------------- #
# Wiki ACL endpoints                                                          #
# --------------------------------------------------------------------------- #


def test_owner_can_list_and_revoke_grants(integration):
    integration.signup(email="alice@x.com")
    integration.put_doc("docs/spec.md", "# Spec")

    resp = integration.client.get("/api/wiki/acl?path=docs/spec.md")
    assert resp.status_code == 200
    entries = resp.json()["entries"]
    everyone_ids = [e["id"] for e in entries if e["principal_kind"] == "everyone"]
    assert len(everyone_ids) == 2  # read + write

    for eid in everyone_ids:
        r = integration.client.delete(f"/api/wiki/acl/{eid}")
        assert r.status_code == 204
    resp = integration.client.get("/api/wiki/acl?path=docs/spec.md")
    assert resp.status_code == 200
    everyone = [e for e in resp.json()["entries"] if e["principal_kind"] == "everyone"]
    assert everyone == []


def test_owner_can_grant_user_access(integration):
    alice = integration.signup(email="alice@x.com")
    bob = integration.signup(email="bob@x.com")
    integration.signin(user_id=alice)
    integration.put_doc("docs/shared.md", "# Shared")

    resp = integration.client.post("/api/wiki/acl", json={
        "resource_kind": "page",
        "resource_path": "docs/shared.md",
        "principal_kind": "user",
        "principal_id": bob,
        "permission": "read",
    })
    assert resp.status_code == 201, resp.text
    eid = resp.json()["id"]
    assert eid.startswith("acl_")

    # Bob can now read.
    integration.signin(user_id=bob)
    r = integration.client.get("/api/documents/file?path=docs/shared.md")
    assert r.status_code == 200


def test_grant_to_nonexistent_user_returns_404(integration):
    integration.signup(email="alice@x.com")
    integration.put_doc("docs/x.md", "x")
    resp = integration.client.post("/api/wiki/acl", json={
        "resource_kind": "page",
        "resource_path": "docs/x.md",
        "principal_kind": "user",
        "principal_id": "u_nope",
        "permission": "read",
    })
    assert resp.status_code == 404


def test_transfer_ownership_changes_owner(integration):
    # Burn the auto-admin slot on a sentinel so Alice/Bob are regular
    # users — otherwise admin's bypass keeps Alice with full access
    # after the transfer and the assertion below is moot.
    integration.signup(email="admin@x.com")
    alice = integration.signup(email="alice@x.com")
    bob = integration.signup(email="bob@x.com")
    integration.signin(user_id=alice)
    integration.put_doc("docs/spec.md", "# Spec")
    _strip_everyone(integration, "docs/spec.md")

    resp = integration.client.post("/api/wiki/transfer-ownership", json={
        "path": "docs/spec.md",
        "new_owner_user_id": bob,
    })
    assert resp.status_code == 200
    assert resp.json()["owner_user_id"] == bob

    # Alice no longer has owner rights.
    integration.signin(user_id=alice)
    r = integration.client.get("/api/wiki/acl?path=docs/spec.md")
    assert r.status_code == 403

    # Bob now does.
    integration.signin(user_id=bob)
    r = integration.client.get("/api/wiki/acl?path=docs/spec.md")
    assert r.status_code == 200


def test_admin_can_list_and_grant_on_someone_elses_page(integration):
    admin_id = integration.signup(email="admin@x.com")
    bob = integration.signup(email="bob@x.com")
    integration.signin(user_id=bob)
    integration.put_doc("bob/notes.md", "# Notes")

    integration.signin(user_id=admin_id)
    resp = integration.client.get("/api/wiki/acl?path=bob/notes.md")
    assert resp.status_code == 200

    resp = integration.client.post("/api/wiki/acl", json={
        "resource_kind": "page",
        "resource_path": "bob/notes.md",
        "principal_kind": "user",
        "principal_id": admin_id,
        "permission": "write",
    })
    assert resp.status_code == 201


# --------------------------------------------------------------------------- #
# Anonymous → 401 across all permission endpoints                             #
# --------------------------------------------------------------------------- #


def test_anonymous_calls_to_permission_endpoints_return_401(integration):
    """Every endpoint behind ``@login_required`` / ``@admin_required``
    must reject an unauthenticated caller before any business logic
    runs. Lock the contract."""
    # Read paths.
    assert integration.client.get("/api/groups").status_code == 401
    assert integration.client.get("/api/groups/grp_x").status_code == 401
    assert integration.client.get("/api/wiki/acl?path=x.md").status_code == 401

    # Write paths.
    assert integration.client.post(
        "/api/groups", json={"name": "x"}
    ).status_code == 401
    assert integration.client.delete("/api/groups/grp_x").status_code == 401
    assert integration.client.post(
        "/api/groups/grp_x/members", json={"user_id": "u_x"}
    ).status_code == 401
    assert integration.client.delete(
        "/api/groups/grp_x/members/u_x"
    ).status_code == 401
    assert integration.client.post(
        "/api/wiki/acl", json={
            "resource_kind": "page", "resource_path": "x.md",
            "principal_kind": "everyone", "principal_id": None,
            "permission": "read",
        }
    ).status_code == 401
    assert integration.client.delete("/api/wiki/acl/acl_x").status_code == 401
    assert integration.client.post(
        "/api/wiki/transfer-ownership",
        json={"path": "x.md", "new_owner_user_id": "u_x"},
    ).status_code == 401


# --------------------------------------------------------------------------- #
# Non-admin → 403 across all admin-gated group endpoints                      #
# --------------------------------------------------------------------------- #


def test_non_admin_blocked_from_every_group_mutation(integration):
    """Full matrix of admin-gated /api/groups endpoints. A regular user
    must not be able to create, delete, add-member, or remove-member.
    """
    integration.signup(email="admin@x.com")  # auto-admin (first user)
    bob = integration.signup(email="bob@x.com")
    # Admin creates a group so the delete/member endpoints have a
    # target id; otherwise they'd return 404 before the auth check.
    integration.signin(email="admin@x.com")
    gid = integration.client.post(
        "/api/groups", json={"name": "eng"}
    ).json()["id"]

    integration.signin(user_id=bob)
    assert integration.client.post(
        "/api/groups", json={"name": "rogue"}
    ).status_code == 403
    assert integration.client.delete(f"/api/groups/{gid}").status_code == 403
    assert integration.client.post(
        f"/api/groups/{gid}/members", json={"user_id": bob}
    ).status_code == 403
    assert integration.client.delete(
        f"/api/groups/{gid}/members/{bob}"
    ).status_code == 403


def test_non_member_cannot_view_group_detail(integration):
    integration.signup(email="admin@x.com")
    integration.signup(email="alice@x.com")
    bob = integration.signup(email="bob@x.com")
    integration.signin(email="admin@x.com")
    gid = integration.client.post(
        "/api/groups", json={"name": "eng"}
    ).json()["id"]
    integration.client.post(
        f"/api/groups/{gid}/members", json={"user_id": bob}
    )

    # Alice is not a member, not admin.
    integration.signin(email="alice@x.com")
    r = integration.client.get(f"/api/groups/{gid}")
    assert r.status_code == 403

    # Bob (member) can see it.
    integration.signin(user_id=bob)
    assert integration.client.get(f"/api/groups/{gid}").status_code == 200


# --------------------------------------------------------------------------- #
# 404 paths on the ACL/group surface                                          #
# --------------------------------------------------------------------------- #


def test_grant_to_nonexistent_group_returns_404(integration):
    integration.signup(email="alice@x.com")
    integration.put_doc("docs/x.md", "x")
    resp = integration.client.post("/api/wiki/acl", json={
        "resource_kind": "page",
        "resource_path": "docs/x.md",
        "principal_kind": "group",
        "principal_id": "grp_nope",
        "permission": "read",
    })
    assert resp.status_code == 404


def test_transfer_to_nonexistent_user_returns_404(integration):
    integration.signup(email="alice@x.com")
    integration.put_doc("docs/x.md", "x")
    resp = integration.client.post("/api/wiki/transfer-ownership", json={
        "path": "docs/x.md",
        "new_owner_user_id": "u_nope",
    })
    assert resp.status_code == 404


def test_revoke_nonexistent_acl_entry_returns_404(integration):
    integration.signup(email="alice@x.com")
    resp = integration.client.delete("/api/wiki/acl/acl_does_not_exist")
    assert resp.status_code == 404


def test_member_add_with_nonexistent_user_returns_404(integration):
    integration.signup(email="admin@x.com")
    integration.signin(email="admin@x.com")
    gid = integration.client.post(
        "/api/groups", json={"name": "eng"}
    ).json()["id"]
    r = integration.client.post(
        f"/api/groups/{gid}/members", json={"user_id": "u_nope"}
    )
    assert r.status_code == 404


def test_get_or_delete_nonexistent_group_returns_404(integration):
    integration.signup(email="admin@x.com")
    integration.signin(email="admin@x.com")
    assert integration.client.get("/api/groups/grp_nope").status_code == 404
    assert integration.client.delete("/api/groups/grp_nope").status_code == 404


# --------------------------------------------------------------------------- #
# Validation                                                                  #
# --------------------------------------------------------------------------- #


def test_grant_with_invalid_path_returns_400(integration):
    integration.signup(email="alice@x.com")
    # Path traversal attempt — caught by safe_rel_path.
    resp = integration.client.post("/api/wiki/acl", json={
        "resource_kind": "page",
        "resource_path": "../escape.md",
        "principal_kind": "everyone",
        "principal_id": None,
        "permission": "read",
    })
    assert resp.status_code == 400


def test_grant_with_missing_path_returns_400(integration):
    integration.signup(email="alice@x.com")
    resp = integration.client.get("/api/wiki/acl")
    assert resp.status_code == 400
