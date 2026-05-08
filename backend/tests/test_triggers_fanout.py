"""End-to-end test for the post-commit trigger fan-out.

Doesn't go through git — the helpers that read BEFORE/AFTER and build the
wiki snapshot are patched so we can exercise the SQL match + LLM verdict
+ message render + events insert path without needing a real wiki repo.
(Direct git coverage is in the engine/git tests elsewhere.)
"""
from __future__ import annotations

import json

from app.db.sqlite import connect


def _seed_user_and_trigger(
    conn, *, scope_path: str, tid: str = "trg_1",
    message: str = "tell me when status changes", destination=None,
) -> None:
    conn.execute(
        "INSERT INTO users(id, email, password_hash, is_admin) VALUES (?, ?, ?, ?)",
        ("usr_1", "u@x.com", "x", 1),
    )
    action_json = json.dumps({"message": message, "destination": destination})
    conn.execute(
        "INSERT INTO triggers(id, owner_user_id, scope_path, kind, nl_description, action_json, enabled) "
        "VALUES (?, ?, ?, 'delta', ?, ?, 1)",
        (tid, "usr_1", scope_path, "fire when status changes", action_json),
    )


def _patch_io(monkeypatch, before: str, after: str) -> None:
    """Stub git read + wiki snapshot so fan-out doesn't need a real repo."""
    from app.tasks import triggers as trig_task
    from app.triggers import diff as diff_helper

    def fake_read(ref, rel_path):
        return after if not ref.endswith("^") else before

    monkeypatch.setattr(trig_task, "_read_at", fake_read)
    monkeypatch.setattr(
        diff_helper, "build_wiki_snapshot", lambda: "=== WIKI (latest version) ===\n"
    )


def _enable_immediate(monkeypatch) -> None:
    from app.tasks.huey_app import triggers_huey

    monkeypatch.setattr(triggers_huey, "immediate", True)


def test_fan_out_records_event_on_match_with_rendered_message(tmp_db, monkeypatch):
    from app.tasks import triggers as trig_task
    from app.triggers import engine

    _enable_immediate(monkeypatch)
    conn = connect()
    try:
        _seed_user_and_trigger(
            conn,
            scope_path="projects/foo.md",
            tid="trg_match",
            message="tell me when status flips",
        )
    finally:
        conn.close()

    _patch_io(monkeypatch, before="status: green\n", after="status: yellow\n")
    monkeypatch.setattr(
        engine, "nl_matches", lambda *a, **kw: (True, "green→yellow")
    )

    captured: dict = {}

    def fake_render(instruction, payload, *, reason):
        captured["instruction"] = instruction
        captured["payload"] = payload
        captured["reason"] = reason
        return "projects/foo.md flipped from green to yellow."

    monkeypatch.setattr(engine, "nl_render_message", fake_render)

    trig_task.fan_out_trigger_eval("projects/foo.md", "deadbeef", "edit", "u@x.com")

    conn = connect()
    try:
        rows = conn.execute("SELECT * FROM events WHERE kind='trigger.fire'").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    payload = json.loads(rows[0]["payload_json"])
    assert payload["doc_path"] == "projects/foo.md"
    assert payload["change_kind"] == "edit"
    assert payload["sha"] == "deadbeef"
    assert "green" in payload["reason"]
    # The rendered message lands in the event payload; the raw instruction
    # is preserved alongside it for audit / re-render later.
    assert payload["message"] == "projects/foo.md flipped from green to yellow."
    assert payload["message_instruction"] == "tell me when status flips"
    assert payload["destination"] is None

    # Render saw the same payload + reason the matcher produced.
    assert captured["instruction"] == "tell me when status flips"
    assert captured["reason"] == "green→yellow"
    assert "WIKI (latest version)" in captured["payload"]
    assert "Path: projects/foo.md" in captured["payload"]


def test_fan_out_skips_event_when_no_match(tmp_db, monkeypatch):
    from app.tasks import triggers as trig_task
    from app.triggers import engine

    _enable_immediate(monkeypatch)
    conn = connect()
    try:
        _seed_user_and_trigger(conn, scope_path="projects/foo.md")
    finally:
        conn.close()

    _patch_io(monkeypatch, before="x", after="y")
    monkeypatch.setattr(engine, "nl_matches", lambda *a, **kw: (False, "no signal"))

    def boom(*a, **kw):
        raise AssertionError("render should not run when match=False")

    monkeypatch.setattr(engine, "nl_render_message", boom)

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


def test_fan_out_directory_scope_new_file_uses_combined_json_call(tmp_db, monkeypatch):
    """Directory-scoped trigger + change_kind=create routes to the
    single-call JSON evaluator, not the two-phase matches/render flow."""
    from app.tasks import triggers as trig_task
    from app.triggers import engine

    _enable_immediate(monkeypatch)
    conn = connect()
    try:
        _seed_user_and_trigger(
            conn,
            scope_path="projects",
            tid="trg_dir",
            message="tell me about new project docs",
        )
    finally:
        conn.close()

    _patch_io(monkeypatch, before="", after="# Project Foo\n\nstatus: green\n")

    captured: dict = {}

    def fake_new_file_eval(nl, instruction, payload):
        captured["nl"] = nl
        captured["instruction"] = instruction
        captured["payload"] = payload
        return True, "New project doc 'Foo' added with status green."

    monkeypatch.setattr(
        engine, "nl_evaluate_new_file_in_dir", fake_new_file_eval
    )

    def boom_match(*a, **kw):
        raise AssertionError("delta path should not run for new-file-in-dir")

    monkeypatch.setattr(engine, "nl_matches", boom_match)
    monkeypatch.setattr(engine, "nl_render_message", boom_match)

    trig_task.fan_out_trigger_eval("projects/new.md", "abc123", "create", None)

    conn = connect()
    try:
        rows = conn.execute("SELECT * FROM events WHERE kind='trigger.fire'").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0]["target"] == "trg_dir"
    payload = json.loads(rows[0]["payload_json"])
    assert payload["message"] == "New project doc 'Foo' added with status green."
    assert payload["message_instruction"] == "tell me about new project docs"
    assert payload["reason"] == "new file under directory scope"

    # The new-file payload, not the diff payload, was handed to the evaluator
    assert "=== NEW FILE ===" in captured["payload"]
    assert "Path: projects/new.md" in captured["payload"]
    assert "=== CHANGE ===" not in captured["payload"]
    assert captured["instruction"] == "tell me about new project docs"


def test_fan_out_directory_scope_edit_uses_two_phase_flow(tmp_db, monkeypatch):
    """Directory-scoped trigger on an EDIT (not a new file) still uses the
    standard matches/render two-phase flow."""
    from app.tasks import triggers as trig_task
    from app.triggers import engine

    _enable_immediate(monkeypatch)
    conn = connect()
    try:
        _seed_user_and_trigger(conn, scope_path="projects", tid="trg_dir")
    finally:
        conn.close()

    _patch_io(monkeypatch, before="status: green\n", after="status: yellow\n")
    monkeypatch.setattr(
        engine, "nl_matches", lambda *a, **kw: (True, "green→yellow")
    )
    monkeypatch.setattr(
        engine, "nl_render_message", lambda *a, **kw: "rendered text"
    )

    def boom_new_file(*a, **kw):
        raise AssertionError("new-file path should not run for edits")

    monkeypatch.setattr(engine, "nl_evaluate_new_file_in_dir", boom_new_file)

    trig_task.fan_out_trigger_eval("projects/foo.md", "abc123", "edit", None)

    conn = connect()
    try:
        rows = conn.execute("SELECT * FROM events WHERE kind='trigger.fire'").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    payload = json.loads(rows[0]["payload_json"])
    assert payload["message"] == "rendered text"
    assert payload["reason"] == "green→yellow"


def test_fan_out_doc_scope_create_uses_two_phase_flow(tmp_db, monkeypatch):
    """A doc-scoped trigger (scope_path == doc_path) on create should NOT
    use the new-file path — that's reserved for directory-scoped triggers."""
    from app.tasks import triggers as trig_task
    from app.triggers import engine

    _enable_immediate(monkeypatch)
    conn = connect()
    try:
        _seed_user_and_trigger(
            conn, scope_path="projects/foo.md", tid="trg_doc"
        )
    finally:
        conn.close()

    _patch_io(monkeypatch, before="", after="# Foo\n")
    monkeypatch.setattr(
        engine, "nl_matches", lambda *a, **kw: (True, "doc created")
    )
    monkeypatch.setattr(
        engine, "nl_render_message", lambda *a, **kw: "doc created"
    )

    def boom(*a, **kw):
        raise AssertionError("new-file path should not run for doc scope")

    monkeypatch.setattr(engine, "nl_evaluate_new_file_in_dir", boom)

    trig_task.fan_out_trigger_eval("projects/foo.md", "abc123", "create", None)

    conn = connect()
    try:
        rows = conn.execute("SELECT * FROM events WHERE kind='trigger.fire'").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
