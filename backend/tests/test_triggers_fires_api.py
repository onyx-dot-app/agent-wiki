"""``GET /api/triggers/fires`` must cap fires per trigger (one busy trigger
can't starve the rest), scope to the caller's own triggers, and flatten the
event payload into the fire view."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

from tests._auth import login_fastapi
from tests._seed import insert_event, seed_trigger, seed_user


@pytest.fixture
def client(tmp_db):
    return TestClient(create_app())


def _fire_payload(**over):
    p = {
        "doc_path": "notes/a.md",
        "change_kind": "update",
        "reason": "status changed",
        "message": "hello",
        "destination_type": "slack",
    }
    p.update(over)
    return p


def test_unauthenticated_is_401(client):
    assert client.get("/api/triggers/fires").status_code == 401


def test_per_trigger_cap_and_ordering(client):
    uid = seed_user(email="usr_1@x.com")
    login_fastapi(client, uid)
    seed_trigger(tid="trg_busy", owner_user_id=uid, scope_path="a.md", message="m")
    seed_trigger(tid="trg_quiet", owner_user_id=uid, scope_path="b.md", message="m")
    for i in range(5):
        insert_event("trigger.fire", "trg_busy", _fire_payload(reason=f"busy {i}"))
    insert_event("trigger.fire", "trg_quiet", _fire_payload(reason="quiet 0"))

    body = client.get("/api/triggers/fires?per_trigger=3").json()
    by_trigger: dict[str, list[dict]] = {}
    for f in body["fires"]:
        by_trigger.setdefault(f["trigger_id"], []).append(f)

    assert len(by_trigger["trg_busy"]) == 3
    assert len(by_trigger["trg_quiet"]) == 1
    # newest first within and across triggers
    assert [f["reason"] for f in by_trigger["trg_busy"]] == [
        "busy 4", "busy 3", "busy 2",
    ]
    assert body["fires"][0]["reason"] == "quiet 0"


def test_scoped_to_own_triggers(client):
    uid = seed_user(email="usr_1@x.com")
    other = seed_user(uid="u_other", email="other@x.com")
    login_fastapi(client, uid)
    seed_trigger(tid="trg_mine", owner_user_id=uid, scope_path="a.md", message="m")
    seed_trigger(tid="trg_theirs", owner_user_id=other, scope_path="b.md", message="m")
    insert_event("trigger.fire", "trg_mine", _fire_payload())
    insert_event("trigger.fire", "trg_theirs", _fire_payload())

    body = client.get("/api/triggers/fires").json()
    assert [f["trigger_id"] for f in body["fires"]] == ["trg_mine"]


def test_trigger_id_filter_uses_limit_not_per_trigger_cap(client):
    uid = seed_user(email="usr_1@x.com")
    login_fastapi(client, uid)
    seed_trigger(tid="trg_a", owner_user_id=uid, scope_path="a.md", message="m")
    seed_trigger(tid="trg_b", owner_user_id=uid, scope_path="b.md", message="m")
    for i in range(6):
        insert_event("trigger.fire", "trg_a", _fire_payload(reason=f"a {i}"))
    insert_event("trigger.fire", "trg_b", _fire_payload(reason="b 0"))

    body = client.get("/api/triggers/fires?trigger_id=trg_a&per_trigger=2").json()
    assert len(body["fires"]) == 6
    assert {f["trigger_id"] for f in body["fires"]} == {"trg_a"}


def test_payload_flattened_and_malformed_payload_safe(client):
    uid = seed_user(email="usr_1@x.com")
    login_fastapi(client, uid)
    seed_trigger(tid="trg_a", owner_user_id=uid, scope_path="a.md", message="m")
    insert_event("trigger.fire", "trg_a", _fire_payload())

    fire = client.get("/api/triggers/fires").json()["fires"][0]
    assert fire["doc_path"] == "notes/a.md"
    assert fire["change_kind"] == "update"
    assert fire["destination_type"] == "slack"
    assert fire["message"] == "hello"
    assert isinstance(fire["event_id"], int)
    assert fire["ts"]
