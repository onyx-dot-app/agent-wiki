"""Co-edit HTTP surface (app/api/coedit.py) — join / leave and their
permission gating. The SSE stream's live delivery is covered at the channel
level in test_coedit_channel.py; here we exercise the request layer.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth import users as users_repo
from app.main import create_app
from app.wiki import acl, coedit, git

from tests._auth import login_fastapi

_PATH = "guides/setup.md"


@pytest.fixture
def client(tmp_db, tmp_repo):
    return TestClient(create_app())


def _seed_page(body: str = "# Setup\n\nhello\n") -> str:
    return git.commit_file(_PATH, body, message="seed", author="t <t@x.com>")


def test_join_requires_auth(client):
    assert client.post("/api/coedit/join", json={"path": _PATH}).status_code == 401


def test_join_creates_session_seeded_from_head(client):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    login_fastapi(client, uid)
    sha = _seed_page()

    resp = client.post("/api/coedit/join", json={"path": _PATH})
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == 0
    assert body["buffer"] == "# Setup\n\nhello\n"
    assert body["base_sha"] == sha
    assert [p["user_id"] for p in body["participants"]] == [uid]


def test_join_is_idempotent_and_shared(client):
    a = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    b = users_repo.create(email="bo@x.com", password="hunter2-x", name="Bo")
    _seed_page()

    login_fastapi(client, a)
    first = client.post("/api/coedit/join", json={"path": _PATH}).json()
    login_fastapi(client, b)
    second = client.post("/api/coedit/join", json={"path": _PATH}).json()

    # Same shared session; both users are participants.
    assert first["session_id"] == second["session_id"]
    assert {p["user_id"] for p in second["participants"]} == {a, b}


def test_join_without_write_is_forbidden(client):
    owner = users_repo.create(email="owner@x.com", password="hunter2-x", name="Owner")
    other = users_repo.create(email="other@x.com", password="hunter2-x", name="Other")
    _seed_page()
    # Committing via git directly bypasses the lifecycle hook, so the page has
    # no ACL rows — setting an owner makes it owner-only (no public grant).
    acl.set_owner(_PATH, owner)

    login_fastapi(client, other)
    assert client.post("/api/coedit/join", json={"path": _PATH}).status_code == 403


def test_leave_removes_participant(client):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    login_fastapi(client, uid)
    _seed_page()

    sid = client.post("/api/coedit/join", json={"path": _PATH}).json()["session_id"]
    assert len(coedit.list_participants(sid)) == 1

    resp = client.post("/api/coedit/leave", json={"session_id": sid})
    assert resp.status_code == 200
    assert coedit.list_participants(sid) == []
