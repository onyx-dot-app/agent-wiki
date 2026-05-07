"""HTTP tests for ``app/api/triggers.py`` via Flask test client.

Builds a minimal app (no wiki / FTS bootstrap) so the suite stays fast and
doesn't need a real wiki dir.
"""
from __future__ import annotations

import pytest
from flask import Flask

from app.api import triggers as triggers_api
from app.db.sqlite import connect


@pytest.fixture
def app(tmp_db):
    app = Flask(__name__)
    app.config.update(SECRET_KEY="test-secret", TESTING=True)
    app.register_blueprint(triggers_api.bp, url_prefix="/api/triggers")
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def _seed_user(uid: str = "usr_1", email: str = "a@b.com") -> str:
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO users(id, email, password_hash, is_admin) VALUES (?, ?, ?, 0)",
            (uid, email, "x"),
        )
    finally:
        conn.close()
    return uid


def _login(client, user_id: str) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = user_id


def test_unauthenticated_list_is_401(client):
    res = client.get("/api/triggers")
    assert res.status_code == 401


def test_create_then_list(client):
    uid = _seed_user()
    _login(client, uid)

    res = client.post(
        "/api/triggers",
        json={"scope_path": "projects/foo.md", "nl_description": "fire on status flip"},
    )
    assert res.status_code == 201, res.get_json()
    body = res.get_json()
    assert body["id"].startswith("trg_")
    assert body["enabled"] is True

    res = client.get("/api/triggers")
    assert res.status_code == 200
    rows = res.get_json()["triggers"]
    assert len(rows) == 1
    assert rows[0]["scope_path"] == "projects/foo.md"


def test_create_validation_errors(client):
    uid = _seed_user()
    _login(client, uid)

    # missing scope_path
    res = client.post("/api/triggers", json={"nl_description": "x"})
    assert res.status_code == 400

    # missing nl_description
    res = client.post("/api/triggers", json={"scope_path": "a.md"})
    assert res.status_code == 400

    # path traversal
    res = client.post(
        "/api/triggers",
        json={"scope_path": "../escape", "nl_description": "x"},
    )
    assert res.status_code == 400

    # unsupported kind
    res = client.post(
        "/api/triggers",
        json={"scope_path": "a.md", "nl_description": "x", "kind": "schedule"},
    )
    assert res.status_code == 400


def test_owner_isolation_on_list(client):
    a = _seed_user("usr_a", "a@x.com")
    b = _seed_user("usr_b", "b@x.com")

    _login(client, a)
    client.post("/api/triggers", json={"scope_path": "a.md", "nl_description": "x"})

    _login(client, b)
    client.post("/api/triggers", json={"scope_path": "b.md", "nl_description": "y"})
    rows = client.get("/api/triggers").get_json()["triggers"]
    assert {r["scope_path"] for r in rows} == {"b.md"}


def test_update_disable_then_re_enable(client):
    uid = _seed_user()
    _login(client, uid)
    tid = client.post(
        "/api/triggers",
        json={"scope_path": "a.md", "nl_description": "orig"},
    ).get_json()["id"]

    res = client.put(f"/api/triggers/{tid}", json={"enabled": False})
    assert res.status_code == 200
    assert res.get_json()["enabled"] is False

    res = client.put(f"/api/triggers/{tid}", json={"enabled": True, "nl_description": "new"})
    body = res.get_json()
    assert body["enabled"] is True
    assert body["nl_description"] == "new"


def test_cannot_modify_anothers_trigger(client):
    a = _seed_user("usr_a", "a@x.com")
    b = _seed_user("usr_b", "b@x.com")

    _login(client, a)
    tid = client.post(
        "/api/triggers", json={"scope_path": "a.md", "nl_description": "x"}
    ).get_json()["id"]

    _login(client, b)
    assert client.put(f"/api/triggers/{tid}", json={"enabled": False}).status_code == 403
    assert client.delete(f"/api/triggers/{tid}").status_code == 403


def test_delete_then_404(client):
    uid = _seed_user()
    _login(client, uid)
    tid = client.post(
        "/api/triggers", json={"scope_path": "a.md", "nl_description": "x"}
    ).get_json()["id"]

    assert client.delete(f"/api/triggers/{tid}").status_code == 204
    assert client.put(f"/api/triggers/{tid}", json={"enabled": False}).status_code == 404
    assert client.delete(f"/api/triggers/{tid}").status_code == 404
