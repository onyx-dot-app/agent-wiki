"""Tests for ``app/api/events.py``."""
from __future__ import annotations

import json

import pytest
from flask import Flask

from app.api import events as events_api
from app.db.sqlite import connect


@pytest.fixture
def client(tmp_db):
    app = Flask(__name__)
    app.config.update(SECRET_KEY="test-secret", TESTING=True)
    app.register_blueprint(events_api.bp, url_prefix="/api/events")
    return app.test_client()


def _seed_user(uid: str = "usr_1") -> str:
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO users(id, email, password_hash, is_admin) VALUES (?, ?, ?, 0)",
            (uid, f"{uid}@x.com", "x"),
        )
    finally:
        conn.close()
    return uid


def _login(client, uid: str) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = uid


def _insert_event(kind: str, target: str, payload: dict) -> None:
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO events(kind, actor, target, payload_json) VALUES (?, NULL, ?, ?)",
            (kind, target, json.dumps(payload)),
        )
    finally:
        conn.close()


def test_unauthenticated_is_401(client):
    assert client.get("/api/events").status_code == 401


def test_list_returns_newest_first_with_parsed_payload(client):
    uid = _seed_user()
    _login(client, uid)
    _insert_event("trigger.fire", "trg_a", {"reason": "first"})
    _insert_event("trigger.fire", "trg_b", {"reason": "second"})

    body = client.get("/api/events").get_json()
    targets = [e["target"] for e in body["events"]]
    assert targets == ["trg_b", "trg_a"]
    assert body["events"][0]["payload"]["reason"] == "second"


def test_filter_by_kind(client):
    uid = _seed_user()
    _login(client, uid)
    _insert_event("trigger.fire", "trg_a", {})
    _insert_event("doc.update", "doc_a", {})

    body = client.get("/api/events?kind=trigger.fire").get_json()
    assert len(body["events"]) == 1
    assert body["events"][0]["kind"] == "trigger.fire"


def test_limit_clamped(client):
    uid = _seed_user()
    _login(client, uid)
    for i in range(5):
        _insert_event("trigger.fire", f"trg_{i}", {})
    body = client.get("/api/events?limit=2").get_json()
    assert len(body["events"]) == 2


def test_get_event_by_id(client):
    uid = _seed_user()
    _login(client, uid)
    _insert_event("trigger.fire", "trg_a", {"reason": "hi"})
    eid = client.get("/api/events").get_json()["events"][0]["id"]

    body = client.get(f"/api/events/{eid}").get_json()
    assert body["target"] == "trg_a"
    assert body["payload"]["reason"] == "hi"

    assert client.get("/api/events/99999").status_code == 404
