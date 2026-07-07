"""PUT /api/user/password requires the current password and rejects short
replacements (validation surfaces as the app's 400 envelope); a successful change invalidates the old password for login."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.auth import users as users_repo
from app.auth.passwords import hash_password, verify_password

from tests._auth import login_fastapi
from tests._seed import seed_user


@pytest.fixture
def client(tmp_db):
    return TestClient(create_app())


def _seed_with_password(uid: str, password: str) -> str:
    seed_user(uid=uid, email=f"{uid}@x.com", password_hash=hash_password(password))
    return uid


def test_requires_auth(client):
    r = client.put(
        "/api/user/password",
        json={"current_password": "a", "new_password": "longenough"},
    )
    assert r.status_code == 401


def test_wrong_current_password_is_400(client):
    uid = _seed_with_password("u_1", "orig-password")
    login_fastapi(client, uid)
    r = client.put(
        "/api/user/password",
        json={"current_password": "wrong", "new_password": "new-password-1"},
    )
    assert r.status_code == 400
    row = users_repo.get_by_id(uid)
    assert row is not None and verify_password("orig-password", row["password_hash"])


def test_short_new_password_is_rejected(client):
    uid = _seed_with_password("u_1", "orig-password")
    login_fastapi(client, uid)
    r = client.put(
        "/api/user/password",
        json={"current_password": "orig-password", "new_password": "short"},
    )
    assert r.status_code == 400


def test_change_invalidates_other_sessions_but_not_own(client, tmp_db):
    uid = _seed_with_password("u_1", "orig-password")
    login_fastapi(client, uid)
    # a second client with its own pre-change session
    other = TestClient(create_app())
    login_fastapi(other, uid)
    assert other.get("/api/user/settings").status_code == 200

    r = client.put(
        "/api/user/password",
        json={"current_password": "orig-password", "new_password": "new-password-1"},
    )
    assert r.status_code == 204
    # the changer's session keeps working; the other session is dead
    assert client.get("/api/user/settings").status_code == 200
    assert other.get("/api/user/settings").status_code == 401


def test_change_succeeds_and_replaces_hash(client):
    uid = _seed_with_password("u_1", "orig-password")
    login_fastapi(client, uid)
    r = client.put(
        "/api/user/password",
        json={"current_password": "orig-password", "new_password": "new-password-1"},
    )
    assert r.status_code == 204
    row = users_repo.get_by_id(uid)
    assert row is not None
    assert verify_password("new-password-1", row["password_hash"])
    assert not verify_password("orig-password", row["password_hash"])
