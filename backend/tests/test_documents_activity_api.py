"""Tests for ``GET /api/documents/file/activity``.

The endpoint lifts ``agent_activity.list_for_doc`` over the same
read-permission gate the body endpoint uses. Body lives in
``tests/test_wiki_agent_activity.py`` for the underlying repo;
this file just covers the HTTP surface.
"""
from __future__ import annotations

import pytest
from flask import Flask, jsonify

from app.api import documents as documents_api
from app.auth import PermissionDenied, users as users_repo
from app.wiki import acl, agent_activity


@pytest.fixture
def client(tmp_db, tmp_repo):
    app = Flask(__name__)
    app.config.update(SECRET_KEY="test-secret", TESTING=True)
    app.register_blueprint(documents_api.bp, url_prefix="/api/documents")

    @app.errorhandler(PermissionDenied)
    def _denied(err):  # type: ignore[unused-ignore]
        return jsonify({"error": err.message}), 403

    return app.test_client()


def _login(client, uid: str) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = uid


def test_unauthenticated_is_401(client):
    assert client.get("/api/documents/file/activity?path=guide.md").status_code == 401


def test_returns_active_rows_for_path(client):
    uid = users_repo.create(email="alice@x.com", password="hunter2-x", name="Alice")
    _login(client, uid)
    agent_activity.upsert_activity(
        user_id=uid, agent_name="claude", doc_path="guide.md",
        activity="read", description=None,
    )
    agent_activity.upsert_activity(
        user_id=uid, agent_name="claude", doc_path="guide.md",
        activity="wrote", description="touched headings",
    )

    resp = client.get("/api/documents/file/activity?path=guide.md")
    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["path"] == "guide.md"
    activities = sorted(a["activity"] for a in payload["agents"])
    assert activities == ["read", "wrote"]
    wrote = next(a for a in payload["agents"] if a["activity"] == "wrote")
    assert wrote["owner_display"] == "Alice"
    assert wrote["agent_name"] == "claude"
    assert wrote["description"] == "touched headings"


def test_returns_empty_list_when_no_rows(client):
    uid = users_repo.create(email="solo@x.com", password="hunter2-x", name="Solo")
    _login(client, uid)
    resp = client.get("/api/documents/file/activity?path=quiet.md")
    assert resp.status_code == 200
    assert resp.get_json() == {"path": "quiet.md", "agents": []}


def test_403_when_user_lacks_read_permission(client):
    """A doc with the everyone-read grant revoked is invisible to
    non-owners. The activity endpoint must honor the same gate as the
    body endpoint."""
    alice = users_repo.create(email="alice@x.com", password="hunter2-x", name="Alice")
    bob = users_repo.create(email="bob@x.com", password="hunter2-x", name="Bob")
    # Seed an owner + strip the default everyone grants so the doc is owner-only.
    acl.set_owner("private.md", alice)
    for grant in acl.list_for_path("private.md"):
        if grant["principal_kind"] == "everyone":
            acl.revoke(grant["id"])

    agent_activity.upsert_activity(
        user_id=alice, agent_name=None, doc_path="private.md",
        activity="read", description=None,
    )

    _login(client, bob)
    resp = client.get("/api/documents/file/activity?path=private.md")
    assert resp.status_code == 403


def test_400_when_path_missing(client):
    uid = users_repo.create(email="x@x.com", password="hunter2-x", name="X")
    _login(client, uid)
    resp = client.get("/api/documents/file/activity")
    assert resp.status_code == 400
