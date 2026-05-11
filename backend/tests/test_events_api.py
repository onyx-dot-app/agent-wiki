"""Tests for ``app/api/events.py``.

Owner-scoping (2026-05-09): the events endpoints filter to
``trigger.fire`` rows whose target trigger is owned by the current
user. Tests that assert on visibility seed both the user and the
trigger so the join finds them.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

from tests._auth import login_fastapi
from tests._seed import insert_event, seed_trigger, seed_user


@pytest.fixture
def client(tmp_db):
    return TestClient(create_app())


def test_unauthenticated_is_401(client):
    assert client.get("/api/events").status_code == 401


def test_list_returns_newest_first_with_parsed_payload(client):
    uid = seed_user(email="usr_1@x.com")
    login_fastapi(client, uid)
    seed_trigger(tid="trg_a", owner_user_id=uid, scope_path="a.md", message="m")
    seed_trigger(tid="trg_b", owner_user_id=uid, scope_path="b.md", message="m")
    insert_event("trigger.fire", "trg_a", {"reason": "first"})
    insert_event("trigger.fire", "trg_b", {"reason": "second"})

    body = client.get("/api/events").json()
    targets = [e["target"] for e in body["events"]]
    assert targets == ["trg_b", "trg_a"]
    assert body["events"][0]["payload"]["reason"] == "second"


def test_filter_by_kind(client):
    uid = seed_user(email="usr_1@x.com")
    login_fastapi(client, uid)
    seed_trigger(tid="trg_a", owner_user_id=uid, scope_path="a.md", message="m")
    insert_event("trigger.fire", "trg_a", {})
    # ``doc.update`` events have no owning trigger and shouldn't surface
    # in the owner-scoped list — owner-scoping is the point of this
    # endpoint in v0.
    insert_event("doc.update", "doc_a", {})

    body = client.get("/api/events?kind=trigger.fire").json()
    assert len(body["events"]) == 1
    assert body["events"][0]["kind"] == "trigger.fire"


def test_limit_clamped(client):
    uid = seed_user(email="usr_1@x.com")
    login_fastapi(client, uid)
    for i in range(5):
        seed_trigger(tid=f"trg_{i}", owner_user_id=uid, scope_path="a.md", message="m")
        insert_event("trigger.fire", f"trg_{i}", {})
    body = client.get("/api/events?limit=2").json()
    assert len(body["events"]) == 2


def test_get_event_by_id(client):
    uid = seed_user(email="usr_1@x.com")
    login_fastapi(client, uid)
    seed_trigger(tid="trg_a", owner_user_id=uid, scope_path="a.md", message="m")
    insert_event("trigger.fire", "trg_a", {"reason": "hi"})
    eid = client.get("/api/events").json()["events"][0]["id"]

    body = client.get(f"/api/events/{eid}").json()
    assert body["target"] == "trg_a"
    assert body["payload"]["reason"] == "hi"

    assert client.get("/api/events/99999").status_code == 404


# --------------------------------------------------------------------------- #
# Owner scoping                                                                #
# --------------------------------------------------------------------------- #


def test_list_hides_other_owners_fires(client):
    """User B's events list does NOT include user A's trigger fires."""
    a = seed_user("usr_a", "a@x.com")
    b = seed_user("usr_b", "b@x.com")
    seed_trigger(tid="trg_a", owner_user_id=a, scope_path="a.md", message="m")
    seed_trigger(tid="trg_b", owner_user_id=b, scope_path="b.md", message="m")
    insert_event("trigger.fire", "trg_a", {"reason": "a's fire"})
    insert_event("trigger.fire", "trg_b", {"reason": "b's fire"})

    login_fastapi(client, b)
    body = client.get("/api/events").json()
    targets = [e["target"] for e in body["events"]]
    assert targets == ["trg_b"]


def test_get_event_404s_for_other_owners_event(client):
    """Cross-owner reads on the detail endpoint return 404 (not 403)
    so we don't leak existence."""
    a = seed_user("usr_a", "a@x.com")
    b = seed_user("usr_b", "b@x.com")
    seed_trigger(tid="trg_a", owner_user_id=a, scope_path="a.md", message="m")
    eid = insert_event("trigger.fire", "trg_a", {})

    login_fastapi(client, b)
    assert client.get(f"/api/events/{eid}").status_code == 404
