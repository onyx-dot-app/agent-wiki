"""End-to-end test for the post-commit trigger fan-out.

Doesn't go through git — the helper that reads BEFORE/AFTER is patched so
we can exercise the SQL match + LLM verdict + events insert path without
needing a real wiki repo. (Direct git coverage is in the engine/git tests
elsewhere.)
"""
from __future__ import annotations

import json

from app.db.sqlite import connect


def _seed_user_and_trigger(conn, *, scope_path: str, tid: str = "trg_1") -> None:
    conn.execute(
        "INSERT INTO users(id, email, password_hash, is_admin) VALUES (?, ?, ?, ?)",
        ("usr_1", "u@x.com", "x", 1),
    )
    conn.execute(
        "INSERT INTO triggers(id, owner_user_id, scope_path, kind, nl_description, action_json, enabled) "
        "VALUES (?, ?, ?, 'delta', ?, '{}', 1)",
        (tid, "usr_1", scope_path, "fire when status changes"),
    )


def _stub_match(matches: bool, reason: str):
    return lambda *args, **kwargs: (matches, reason)


def _patch_read(monkeypatch, before: str, after: str) -> None:
    from app.tasks import triggers as trig_task

    def fake_read(ref, rel_path):
        return after if not ref.endswith("^") else before

    monkeypatch.setattr(trig_task, "_read_at", fake_read)


def _enable_immediate(monkeypatch) -> None:
    from app.tasks.huey_app import huey

    monkeypatch.setattr(huey, "immediate", True)


def test_fan_out_records_event_on_match(tmp_db, monkeypatch):
    from app.tasks import triggers as trig_task
    from app.triggers import engine

    _enable_immediate(monkeypatch)
    conn = connect()
    try:
        _seed_user_and_trigger(conn, scope_path="projects/foo.md", tid="trg_match")
    finally:
        conn.close()

    _patch_read(monkeypatch, before="status: green\n", after="status: yellow\n")
    monkeypatch.setattr(engine, "nl_matches", _stub_match(True, "green→yellow"))

    trig_task.fan_out_trigger_eval("projects/foo.md", "deadbeef", "edit", "u@x.com")

    conn = connect()
    try:
        rows = conn.execute("SELECT * FROM events WHERE kind='trigger.fire'").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0]["actor"] == "u@x.com"
    assert rows[0]["target"] == "trg_match"
    payload = json.loads(rows[0]["payload_json"])
    assert payload["doc_path"] == "projects/foo.md"
    assert payload["change_kind"] == "edit"
    assert payload["sha"] == "deadbeef"
    assert "green" in payload["reason"]


def test_fan_out_skips_event_when_no_match(tmp_db, monkeypatch):
    from app.tasks import triggers as trig_task
    from app.triggers import engine

    _enable_immediate(monkeypatch)
    conn = connect()
    try:
        _seed_user_and_trigger(conn, scope_path="projects/foo.md")
    finally:
        conn.close()

    _patch_read(monkeypatch, before="x", after="y")
    monkeypatch.setattr(engine, "nl_matches", _stub_match(False, "no signal"))

    trig_task.fan_out_trigger_eval("projects/foo.md", "deadbeef", "edit", None)

    conn = connect()
    try:
        n = conn.execute("SELECT COUNT(*) AS c FROM events").fetchone()["c"]
    finally:
        conn.close()
    assert n == 0


def test_fan_out_no_triggers_short_circuits(tmp_db, monkeypatch):
    from app.tasks import triggers as trig_task

    _enable_immediate(monkeypatch)

    called = {"n": 0}

    def boom(*a, **kw):
        called["n"] += 1
        raise AssertionError("should not be called when there are no matches")

    monkeypatch.setattr(trig_task, "_read_at", boom)

    trig_task.fan_out_trigger_eval("projects/foo.md", "deadbeef", "edit", None)

    assert called["n"] == 0


def test_fan_out_directory_scope_matches_via_parent(tmp_db, monkeypatch):
    from app.tasks import triggers as trig_task
    from app.triggers import engine

    _enable_immediate(monkeypatch)
    conn = connect()
    try:
        _seed_user_and_trigger(conn, scope_path="projects", tid="trg_dir")
    finally:
        conn.close()

    _patch_read(monkeypatch, before="", after="hello\n")
    monkeypatch.setattr(engine, "nl_matches", _stub_match(True, "new file added"))

    trig_task.fan_out_trigger_eval("projects/new.md", "abc123", "create", None)

    conn = connect()
    try:
        rows = conn.execute("SELECT * FROM events WHERE kind='trigger.fire'").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0]["target"] == "trg_dir"
