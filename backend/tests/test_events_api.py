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
from app.wiki import acl

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


# --------------------------------------------------------------------------- #
# Admin audit view of automanage.* events                                     #
# --------------------------------------------------------------------------- #


def test_admin_sees_automanage_events_on_unowned_paths(client):
    """Auto Organize auto-applies on paths with no owner — an admin sees those
    ``automanage.*`` events regardless of ownership (the space-wide audit)."""
    admin = seed_user("adm", "adm@x.com", is_admin=True)
    insert_event("automanage.applied", "orphan/folder", {"op": "delete_empty_folder"})

    login_fastapi(client, admin)
    body = client.get("/api/events").json()
    targets = [e["target"] for e in body["events"]]
    assert "orphan/folder" in targets


def test_non_admin_does_not_see_automanage_event_on_unowned_path(client):
    """A non-admin who owns neither the path nor a trigger does not see the
    auto-applied event — the admin audit clause is admin-only."""
    user = seed_user("usr", "usr@x.com")
    insert_event("automanage.applied", "orphan/folder", {"op": "delete_empty_folder"})

    login_fastapi(client, user)
    assert client.get("/api/events").json()["events"] == []


def test_path_owner_sees_automanage_event_on_their_path(client):
    """A non-admin who owns the event's (surviving) target path sees it — this
    is why the auto-apply event targets the surviving parent folder rather than
    the deleted path, whose owner row was re-pointed to trash."""
    user = seed_user("usr", "usr@x.com")
    acl.set_owner("area", user)  # owns the parent folder the event targets
    insert_event("automanage.applied", "area", {"op": "delete_empty_folder"})

    login_fastapi(client, user)
    targets = [e["target"] for e in client.get("/api/events").json()["events"]]
    assert targets == ["area"]


def test_admin_can_fetch_automanage_event_detail(client):
    """The detail endpoint mirrors the list: an admin can read an automanage
    event even when the target path isn't a trigger they own."""
    admin = seed_user("adm", "adm@x.com", is_admin=True)
    eid = insert_event("automanage.applied", "orphan/folder", {"op": "x"})

    login_fastapi(client, admin)
    assert client.get(f"/api/events/{eid}").status_code == 200
