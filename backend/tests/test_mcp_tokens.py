"""Tests for the inbound MCP token surface — repo + HTTP endpoints."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth import mcp_tokens as tokens_repo
from app.main import create_app

from tests._auth import login_fastapi
from tests._seed import seed_user


# --------------------------------------------------------------------------- #
# Repo-level                                                                  #
# --------------------------------------------------------------------------- #


def test_create_returns_prefixed_raw_and_persists_hash(tmp_db):
    uid = seed_user(uid="u1", email="u1@x.com")

    token_id, raw = tokens_repo.create(uid, "my laptop")

    assert token_id.startswith("mtk_")
    assert raw.startswith("mcp_")
    assert len(raw) > 20

    rows = tokens_repo.list_for_user(uid)
    assert len(rows) == 1
    assert rows[0]["id"] == token_id
    assert rows[0]["name"] == "my laptop"
    # The summary must NOT leak the hash.
    assert "token_hash" not in rows[0]
    assert "token" not in rows[0]


def test_create_rejects_blank_name(tmp_db):
    uid = seed_user(uid="u1", email="u1@x.com")
    with pytest.raises(ValueError):
        tokens_repo.create(uid, "   ")


def test_verify_round_trip_returns_user_and_agent_name(tmp_db):
    uid = seed_user(uid="u1", email="u1@x.com", name="One")
    _, raw = tokens_repo.create(uid, "Claude Code")

    resolved = tokens_repo.verify(raw)
    assert resolved is not None
    user, agent_name = resolved
    assert user.id == uid
    assert user.email == "u1@x.com"
    assert user.name == "One"
    assert agent_name == "Claude Code"


def test_verify_rejects_unknown_token(tmp_db):
    uid = seed_user(uid="u1", email="u1@x.com")
    tokens_repo.create(uid, "k")
    # Right shape, wrong content.
    assert tokens_repo.verify("mcp_" + "z" * 32) is None
    # Wrong shape.
    assert tokens_repo.verify("not-a-token") is None
    assert tokens_repo.verify("") is None


def test_verify_after_revoke_fails(tmp_db):
    uid = seed_user(uid="u1", email="u1@x.com")
    token_id, raw = tokens_repo.create(uid, "k")
    assert tokens_repo.verify(raw) is not None

    assert tokens_repo.revoke(token_id, uid) is True
    assert tokens_repo.verify(raw) is None


def test_revoke_other_users_token_is_noop(tmp_db):
    a = seed_user(uid="ua", email="a@x.com")
    b = seed_user(uid="ub", email="b@x.com")
    token_id, raw = tokens_repo.create(a, "k")

    # User b can't revoke user a's token.
    assert tokens_repo.revoke(token_id, b) is False
    # Still works for a.
    assert tokens_repo.verify(raw) is not None


def test_two_users_each_see_only_their_tokens(tmp_db):
    a = seed_user(uid="ua", email="a@x.com")
    b = seed_user(uid="ub", email="b@x.com")
    tokens_repo.create(a, "alice-laptop")
    tokens_repo.create(a, "alice-desktop")
    tokens_repo.create(b, "bob-laptop")

    assert {r["name"] for r in tokens_repo.list_for_user(a)} == {
        "alice-laptop",
        "alice-desktop",
    }
    assert {r["name"] for r in tokens_repo.list_for_user(b)} == {"bob-laptop"}


def test_verify_bumps_last_used_at(tmp_db):
    uid = seed_user(uid="u1", email="u1@x.com")
    _, raw = tokens_repo.create(uid, "k")

    before = tokens_repo.list_for_user(uid)[0]["last_used_at"]
    assert before is None

    assert tokens_repo.verify(raw) is not None

    after = tokens_repo.list_for_user(uid)[0]["last_used_at"]
    assert after is not None


# --------------------------------------------------------------------------- #
# HTTP                                                                        #
# --------------------------------------------------------------------------- #


@pytest.fixture
def client(tmp_db):
    return TestClient(create_app())


def test_unauthenticated_is_401(client):
    assert client.get("/api/mcp/tokens").status_code == 401
    assert client.post("/api/mcp/tokens", json={"name": "x"}).status_code == 401
    assert client.delete("/api/mcp/tokens/anything").status_code == 401


def test_create_returns_raw_once_then_list_hides_it(client):
    uid = seed_user(uid="u1", email="u1@x.com")
    login_fastapi(client, uid)

    res = client.post("/api/mcp/tokens", json={"name": "claude-code"})
    assert res.status_code == 201
    body = res.json()
    assert body["name"] == "claude-code"
    assert body["token"].startswith("mcp_")
    token_id = body["id"]

    listing = client.get("/api/mcp/tokens").json()
    assert len(listing["tokens"]) == 1
    summary = listing["tokens"][0]
    assert summary["id"] == token_id
    assert summary["name"] == "claude-code"
    assert "token" not in summary


def test_create_validates_name(client):
    uid = seed_user(uid="u1", email="u1@x.com")
    login_fastapi(client, uid)
    assert client.post("/api/mcp/tokens", json={}).status_code == 400
    assert client.post("/api/mcp/tokens", json={"name": ""}).status_code == 400


def test_revoke_then_404(client):
    uid = seed_user(uid="u1", email="u1@x.com")
    login_fastapi(client, uid)

    token_id = client.post("/api/mcp/tokens", json={"name": "k"}).json()["id"]

    assert client.delete(f"/api/mcp/tokens/{token_id}").status_code == 204
    # Second time: gone.
    assert client.delete(f"/api/mcp/tokens/{token_id}").status_code == 404
    # Listing is empty.
    assert client.get("/api/mcp/tokens").json()["tokens"] == []


def test_user_cannot_revoke_other_users_token(client):
    a = seed_user(uid="ua", email="a@x.com")
    b = seed_user(uid="ub", email="b@x.com")

    login_fastapi(client, a)
    token_id = client.post("/api/mcp/tokens", json={"name": "alice"}).json()["id"]

    login_fastapi(client, b)
    assert client.delete(f"/api/mcp/tokens/{token_id}").status_code == 404

    # Bob's listing is still empty; alice's token still exists.
    assert client.get("/api/mcp/tokens").json()["tokens"] == []
    login_fastapi(client, a)
    assert len(client.get("/api/mcp/tokens").json()["tokens"]) == 1
