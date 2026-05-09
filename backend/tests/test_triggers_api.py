"""HTTP tests for ``app/api/triggers.py`` via Flask test client.

Builds a minimal app (no wiki / FTS bootstrap) so the suite stays fast and
doesn't need a real wiki dir.
"""
from __future__ import annotations

import pytest
from flask import Flask, jsonify

from app.api import triggers as triggers_api
from app.auth import PermissionDenied
from app.models._helpers import RequestError

from tests._seed import seed_user


@pytest.fixture
def app(tmp_repo):
    app = Flask(__name__)
    app.config.update(SECRET_KEY="test-secret", TESTING=True)
    app.register_blueprint(triggers_api.bp, url_prefix="/api/triggers")

    @app.errorhandler(RequestError)
    def _on_request_error(err: RequestError):
        return jsonify(error=err.message), err.status

    @app.errorhandler(PermissionDenied)
    def _on_permission_denied(err: PermissionDenied):
        return jsonify(error=err.message), 403

    return app


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client, user_id: str) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = user_id


def test_unauthenticated_list_is_401(client):
    res = client.get("/api/triggers")
    assert res.status_code == 401


def test_create_then_list(client):
    uid = seed_user(email="a@b.com")
    _login(client, uid)

    res = client.post(
        "/api/triggers",
        json={"scope_path": "projects/foo.md", "nl_description": "fire on status flip", "message": "status flipped"},
    )
    assert res.status_code == 201, res.get_json()
    body = res.get_json()
    assert body["id"].startswith("trg_")
    assert body["enabled"] is True
    assert body["message"] == "status flipped"
    assert body["destination"] == "event_log"

    res = client.get("/api/triggers")
    assert res.status_code == 200
    rows = res.get_json()["triggers"]
    assert len(rows) == 1
    assert rows[0]["scope_path"] == "projects/foo.md"


def test_create_validation_errors(client):
    uid = seed_user(email="a@b.com")
    _login(client, uid)

    # missing scope_path
    res = client.post("/api/triggers", json={"nl_description": "x", "message": "m"})
    assert res.status_code == 400

    # missing nl_description
    res = client.post("/api/triggers", json={"scope_path": "a.md", "message": "m"})
    assert res.status_code == 400

    # missing message
    res = client.post("/api/triggers", json={"scope_path": "a.md", "nl_description": "x"})
    assert res.status_code == 400

    # path traversal
    res = client.post(
        "/api/triggers",
        json={"scope_path": "../escape", "nl_description": "x", "message": "m"},
    )
    assert res.status_code == 400

    # unsupported kind
    res = client.post(
        "/api/triggers",
        json={"scope_path": "a.md", "nl_description": "x", "message": "m", "kind": "schedule"},
    )
    assert res.status_code == 400

    # unknown destination id
    res = client.post(
        "/api/triggers",
        json={"scope_path": "a.md", "nl_description": "x", "message": "m",
              "destination": "no_such_destination"},
    )
    assert res.status_code == 400


def test_owner_isolation_on_list(client):
    a = seed_user("usr_a", "a@x.com")
    b = seed_user("usr_b", "b@x.com")

    _login(client, a)
    client.post("/api/triggers", json={"scope_path": "a.md", "nl_description": "x", "message": "m"})

    _login(client, b)
    client.post("/api/triggers", json={"scope_path": "b.md", "nl_description": "y", "message": "m"})
    rows = client.get("/api/triggers").get_json()["triggers"]
    assert {r["scope_path"] for r in rows} == {"b.md"}


def test_update_disable_then_re_enable(client):
    uid = seed_user(email="a@b.com")
    _login(client, uid)
    tid = client.post(
        "/api/triggers",
        json={"scope_path": "a.md", "nl_description": "orig", "message": "m"},
    ).get_json()["id"]

    res = client.put(f"/api/triggers/{tid}", json={"enabled": False})
    assert res.status_code == 200
    assert res.get_json()["enabled"] is False

    res = client.put(
        f"/api/triggers/{tid}",
        json={"enabled": True, "nl_description": "new", "message": "m2"},
    )
    body = res.get_json()
    assert body["enabled"] is True
    assert body["nl_description"] == "new"
    assert body["message"] == "m2"

    # destination updates: known ids are ok, unknown ones rejected.
    res = client.put(f"/api/triggers/{tid}", json={"destination": "event_log"})
    assert res.status_code == 200
    res = client.put(
        f"/api/triggers/{tid}", json={"destination": "no_such_destination"}
    )
    assert res.status_code == 400


def test_cannot_modify_anothers_trigger(client):
    a = seed_user("usr_a", "a@x.com")
    b = seed_user("usr_b", "b@x.com")

    _login(client, a)
    tid = client.post(
        "/api/triggers", json={"scope_path": "a.md", "nl_description": "x", "message": "m"}
    ).get_json()["id"]

    _login(client, b)
    assert client.put(f"/api/triggers/{tid}", json={"enabled": False}).status_code == 403
    assert client.delete(f"/api/triggers/{tid}").status_code == 403


def test_delete_then_404(client):
    uid = seed_user(email="a@b.com")
    _login(client, uid)
    tid = client.post(
        "/api/triggers", json={"scope_path": "a.md", "nl_description": "x", "message": "m"}
    ).get_json()["id"]

    assert client.delete(f"/api/triggers/{tid}").status_code == 204
    assert client.put(f"/api/triggers/{tid}", json={"enabled": False}).status_code == 404
    assert client.delete(f"/api/triggers/{tid}").status_code == 404


# --------------------------------------------------------------------------- #
# scope_path ACL gating                                                        #
# --------------------------------------------------------------------------- #


def test_create_blocks_when_scope_path_unreadable(client):
    """A user without read access to a managed path can't create a trigger
    that watches it."""
    from app.wiki import acl

    owner = seed_user("usr_owner", "owner@x.com")
    other = seed_user("usr_other", "other@x.com")
    # Owner stamp + private ACL → ``other`` lacks read.
    acl.set_owner("private/secret.md", owner)
    acl.grant(
        resource_kind="page",
        resource_path="private/secret.md",
        principal_kind="user",
        principal_id=owner,
        permission="read",
        granted_by_user_id=owner,
    )

    _login(client, other)
    res = client.post(
        "/api/triggers",
        json={
            "scope_path": "private/secret.md",
            "nl_description": "fire",
            "message": "msg",
        },
    )
    assert res.status_code == 403


def test_update_blocks_when_rebinding_to_unreadable_scope(client):
    """A user can't repoint their own trigger at a path they can't read."""
    from app.wiki import acl

    owner = seed_user("usr_owner", "owner@x.com")
    other = seed_user("usr_other", "other@x.com")
    acl.set_owner("private/secret.md", owner)
    acl.grant(
        resource_kind="page",
        resource_path="private/secret.md",
        principal_kind="user",
        principal_id=owner,
        permission="read",
        granted_by_user_id=owner,
    )

    _login(client, other)
    tid = client.post(
        "/api/triggers",
        json={"scope_path": "public.md", "nl_description": "x", "message": "m"},
    ).get_json()["id"]

    res = client.put(
        f"/api/triggers/{tid}", json={"scope_path": "private/secret.md"}
    )
    assert res.status_code == 403


# --------------------------------------------------------------------------- #
# Positive ACL regression cases — make sure the gate isn't accidentally       #
# over-restrictive. Each scenario exercises a different way a user can        #
# legitimately reach a managed scope.                                          #
# --------------------------------------------------------------------------- #


def _lock_path_to(owner_id: str, path: str) -> None:
    """Stamp ``owner_id`` as the owner so ``path`` is no longer
    implicit-public — without this, the resolver short-circuits and the
    ACL gate never actually fires."""
    from app.wiki import acl

    acl.set_owner(path, owner_id)
    # Make sure the owner has an explicit row too so revoking the per-user
    # grant in negative tests doesn't accidentally take their own access.
    acl.grant(
        resource_kind="page",
        resource_path=path,
        principal_kind="user",
        principal_id=owner_id,
        permission="read",
        granted_by_user_id=owner_id,
    )


def test_create_allowed_when_user_has_explicit_read_grant(client):
    """Positive case: user holds an explicit per-user `read` grant on a
    managed path → trigger creation succeeds (201)."""
    from app.wiki import acl

    owner = seed_user("usr_owner", "owner@x.com")
    invitee = seed_user("usr_invitee", "invitee@x.com")
    _lock_path_to(owner, "private/secret.md")
    acl.grant(
        resource_kind="page",
        resource_path="private/secret.md",
        principal_kind="user",
        principal_id=invitee,
        permission="read",
        granted_by_user_id=owner,
    )

    _login(client, invitee)
    res = client.post(
        "/api/triggers",
        json={
            "scope_path": "private/secret.md",
            "nl_description": "fire",
            "message": "msg",
        },
    )
    assert res.status_code == 201, res.get_json()


def test_create_allowed_for_admin_on_private_scope(client):
    """Positive case: admin override — admins always pass the ACL gate."""
    owner = seed_user("usr_owner", "owner@x.com")
    admin = seed_user("usr_admin", "admin@x.com", is_admin=True)
    _lock_path_to(owner, "private/secret.md")

    _login(client, admin)
    res = client.post(
        "/api/triggers",
        json={
            "scope_path": "private/secret.md",
            "nl_description": "fire",
            "message": "msg",
        },
    )
    assert res.status_code == 201, res.get_json()


def test_create_allowed_via_folder_grant(client):
    """Positive case: a folder-level `read` grant cascades to a doc inside.

    Today the resolver matches folder rows for any descendant; this test
    pins the behavior so a future regressive narrowing of folder cascade
    surfaces here.
    """
    from app.wiki import acl

    owner = seed_user("usr_owner", "owner@x.com")
    invitee = seed_user("usr_invitee", "invitee@x.com")
    # Stamp the doc as managed by giving it an owner.
    _lock_path_to(owner, "private/secret.md")
    # Grant invitee read on the *folder*, not the doc itself.
    acl.grant(
        resource_kind="folder",
        resource_path="private",
        principal_kind="user",
        principal_id=invitee,
        permission="read",
        granted_by_user_id=owner,
    )

    _login(client, invitee)
    res = client.post(
        "/api/triggers",
        json={
            "scope_path": "private/secret.md",
            "nl_description": "fire",
            "message": "msg",
        },
    )
    assert res.status_code == 201, res.get_json()


# --------------------------------------------------------------------------- #
# Update-time gate — both rebinding and toggling-without-rebind                #
# --------------------------------------------------------------------------- #


def test_update_blocks_when_existing_scope_unreadable(client):
    """Negative regression: even a no-op-ish update (e.g. toggling
    ``enabled``) is blocked when the user has lost read access to the
    *existing* scope. Without this, a revoked user could still mutate
    their old triggers."""
    from app.wiki import acl

    owner = seed_user("usr_owner", "owner@x.com")
    invitee = seed_user("usr_invitee", "invitee@x.com")
    _lock_path_to(owner, "private/secret.md")
    grant_id = acl.grant(
        resource_kind="page",
        resource_path="private/secret.md",
        principal_kind="user",
        principal_id=invitee,
        permission="read",
        granted_by_user_id=owner,
    )

    _login(client, invitee)
    tid = client.post(
        "/api/triggers",
        json={
            "scope_path": "private/secret.md",
            "nl_description": "x",
            "message": "m",
        },
    ).get_json()["id"]

    # Revoke after creation.
    acl.revoke(grant_id)

    res = client.put(f"/api/triggers/{tid}", json={"enabled": False})
    assert res.status_code == 403


def test_update_allowed_when_user_has_explicit_grant(client):
    """Positive regression: the gate doesn't block legitimate updates."""
    from app.wiki import acl

    owner = seed_user("usr_owner", "owner@x.com")
    invitee = seed_user("usr_invitee", "invitee@x.com")
    _lock_path_to(owner, "private/secret.md")
    acl.grant(
        resource_kind="page",
        resource_path="private/secret.md",
        principal_kind="user",
        principal_id=invitee,
        permission="read",
        granted_by_user_id=owner,
    )

    _login(client, invitee)
    tid = client.post(
        "/api/triggers",
        json={
            "scope_path": "private/secret.md",
            "nl_description": "x",
            "message": "m",
        },
    ).get_json()["id"]
    res = client.put(f"/api/triggers/{tid}", json={"enabled": False})
    assert res.status_code == 200
    assert res.get_json()["enabled"] is False


# --------------------------------------------------------------------------- #
# /destinations endpoint                                                       #
# --------------------------------------------------------------------------- #


def test_list_destinations_unauthenticated_is_401(client):
    res = client.get("/api/triggers/destinations")
    assert res.status_code == 401


def test_list_destinations_returns_event_log(client):
    uid = seed_user(email="a@b.com")
    _login(client, uid)
    res = client.get("/api/triggers/destinations")
    assert res.status_code == 200
    body = res.get_json()
    assert "destinations" in body
    ids = {d["id"] for d in body["destinations"]}
    assert "event_log" in ids
    event_log = next(d for d in body["destinations"] if d["id"] == "event_log")
    assert event_log["name"]
    assert event_log["description"]
