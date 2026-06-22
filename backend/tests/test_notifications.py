"""Notification center — repo dedup semantics + the /api/notifications surface."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import notifications as repo
from app.db.session import init_db
from app.main import create_app

from tests._auth import login_fastapi
from tests._seed import seed_user


@pytest.fixture
def client(tmp_config):
    init_db()
    return TestClient(create_app())


# --------------------------------------------------------------------------- #
# Repo dedup semantics                                                        #
# --------------------------------------------------------------------------- #


def test_create_dedups_and_bumps_last_shown(client):
    uid = seed_user()
    key = {"agent_session_id": "as_1"}
    repo.create(user_id=uid, notif_type="craft_ready", title="t1", data=key)
    first = repo.list_for_user(uid)["notifications"][0]

    repo.create(user_id=uid, notif_type="craft_ready", title="t1", data=key)
    page = repo.list_for_user(uid)
    assert page["total_items"] == 1
    assert page["notifications"][0]["last_shown"] >= first["last_shown"]

    # Different dedup key → second row.
    repo.create(
        user_id=uid, notif_type="craft_ready", title="t2", data={"agent_session_id": "as_2"}
    )
    assert repo.list_for_user(uid)["total_items"] == 2


def test_create_does_not_resurrect_dismissed(client):
    uid = seed_user()
    key = {"agent_session_id": "as_1"}
    repo.create(user_id=uid, notif_type="craft_ready", title="t", data=key)
    nid = repo.list_for_user(uid)["notifications"][0]["id"]
    assert repo.dismiss(nid, user_id=uid)

    repo.create(user_id=uid, notif_type="craft_ready", title="t", data=key)
    page = repo.list_for_user(uid)
    assert page["total_items"] == 1
    assert page["undismissed_count"] == 0
    assert page["notifications"][0]["dismissed"] is True


# --------------------------------------------------------------------------- #
# API surface                                                                 #
# --------------------------------------------------------------------------- #


def test_list_and_counts(client):
    uid = seed_user()
    login_fastapi(client, uid)
    for i in range(3):
        repo.create(user_id=uid, notif_type="craft_ready", title=f"t{i}", data={"k": i})

    res = client.get("/api/notifications")
    assert res.status_code == 200
    body = res.json()
    assert body["total_items"] == 3
    assert body["undismissed_count"] == 3
    assert body["has_more"] is False
    assert len(body["notifications"]) == 3

    paged = client.get("/api/notifications", params={"limit": 2}).json()
    assert len(paged["notifications"]) == 2
    assert paged["has_more"] is True


def test_dismiss_scoped_to_owner(client):
    alice = seed_user(uid="alice", email="a@x.com")
    bob = seed_user(uid="bob", email="b@x.com")
    repo.create(user_id=alice, notif_type="craft_ready", title="t", data={})
    nid = repo.list_for_user(alice)["notifications"][0]["id"]

    login_fastapi(client, bob)
    assert client.post(f"/api/notifications/{nid}/dismiss").status_code == 404

    login_fastapi(client, alice)
    assert client.post(f"/api/notifications/{nid}/dismiss").status_code == 200
    assert repo.list_for_user(alice)["undismissed_count"] == 0


def test_dismiss_all(client):
    uid = seed_user()
    login_fastapi(client, uid)
    for i in range(4):
        repo.create(user_id=uid, notif_type="craft_failed", title=f"t{i}", data={"k": i})
    res = client.post("/api/notifications/dismiss-all")
    assert res.status_code == 200
    assert res.json()["dismissed"] == 4
    assert repo.list_for_user(uid)["undismissed_count"] == 0
