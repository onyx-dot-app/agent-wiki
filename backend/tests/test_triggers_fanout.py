"""End-to-end test for the post-commit trigger fan-out.

Doesn't go through git — the helpers that read BEFORE/AFTER and build the
wiki snapshot are patched so we can exercise the SQL match + LLM verdict
+ message render + events insert path without needing a real wiki repo.
(Direct git coverage is in the engine/git tests elsewhere.)
"""
from __future__ import annotations


import pytest

from app.triggers.natural_language import MatchResult, NewFileEvalResult

from tests._seed import Event, count_rows, list_events, seed_trigger, seed_user


@pytest.fixture(autouse=True)
def _immediate_triggers():
    """Run trigger-eval handlers inline so assertions land before yield."""
    from app.tasks.queues import triggers_queue

    with triggers_queue.immediate_mode():
        yield


def _seed_user_and_trigger(
    *,
    scope_path: str,
    tid: str = "trg_1",
    message: str = "tell me when status changes",
    destination: str = "event_log",
) -> None:
    seed_user(uid="usr_1", email="u@x.com", is_admin=True)
    seed_trigger(
        tid=tid,
        owner_user_id="usr_1",
        scope_path=scope_path,
        message=message,
        destination=destination,
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


def test_fan_out_records_event_on_match_with_rendered_message(tmp_db, monkeypatch):
    from app.tasks import triggers as trig_task
    from app.triggers import engine

    _seed_user_and_trigger(
        scope_path="projects/foo.md",
        tid="trg_match",
        message="tell me when status flips",
    )

    _patch_io(monkeypatch, before="status: green\n", after="status: yellow\n")
    monkeypatch.setattr(
        engine, "nl_matches", lambda *a, **kw: MatchResult(matched=True, reason="green→yellow")
    )

    captured: dict = {}

    def fake_render(instruction, payload, *, reason):
        captured["instruction"] = instruction
        captured["payload"] = payload
        captured["reason"] = reason
        return "projects/foo.md flipped from green to yellow."

    monkeypatch.setattr(engine, "nl_render_message", fake_render)

    trig_task.fan_out_trigger_eval("projects/foo.md", "deadbeef", "edit", "u@x.com")

    rows = list_events(kind="trigger.fire")
    assert len(rows) == 1
    payload = rows[0]["payload"]
    assert payload["doc_path"] == "projects/foo.md"
    assert payload["change_kind"] == "edit"
    assert payload["sha"] == "deadbeef"
    assert "green" in payload["reason"]
    # The rendered message lands in the event payload; the raw instruction
    # is preserved alongside it for audit / re-render later.
    assert payload["message"] == "projects/foo.md flipped from green to yellow."
    assert payload["message_instruction"] == "tell me when status flips"
    assert payload["destination"] == "event_log"

    # Render saw the same payload + reason the matcher produced.
    assert captured["instruction"] == "tell me when status flips"
    assert captured["reason"] == "green→yellow"
    assert "WIKI (latest version)" in captured["payload"]
    assert "Path: projects/foo.md" in captured["payload"]


def test_fan_out_skips_event_when_no_match(tmp_db, monkeypatch):
    from app.tasks import triggers as trig_task
    from app.triggers import engine

    _seed_user_and_trigger(scope_path="projects/foo.md")

    _patch_io(monkeypatch, before="x", after="y")
    monkeypatch.setattr(engine, "nl_matches", lambda *a, **kw: MatchResult(matched=False, reason="no signal"))

    def boom(*a, **kw):
        raise AssertionError("render should not run when match=False")

    monkeypatch.setattr(engine, "nl_render_message", boom)

    trig_task.fan_out_trigger_eval("projects/foo.md", "deadbeef", "edit", None)

    assert count_rows(Event) == 0


def test_fan_out_no_triggers_short_circuits(tmp_db, monkeypatch):
    from app.tasks import triggers as trig_task

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

    _seed_user_and_trigger(
        scope_path="projects",
        tid="trg_dir",
        message="tell me about new project docs",
    )

    _patch_io(monkeypatch, before="", after="# Project Foo\n\nstatus: green\n")

    captured: dict = {}

    def fake_new_file_eval(nl, instruction, payload):
        captured["nl"] = nl
        captured["instruction"] = instruction
        captured["payload"] = payload
        return NewFileEvalResult(triggered=True, message="New project doc 'Foo' added with status green.")

    monkeypatch.setattr(
        engine, "nl_evaluate_new_file_in_dir", fake_new_file_eval
    )

    def boom_match(*a, **kw):
        raise AssertionError("delta path should not run for new-file-in-dir")

    monkeypatch.setattr(engine, "nl_matches", boom_match)
    monkeypatch.setattr(engine, "nl_render_message", boom_match)

    trig_task.fan_out_trigger_eval("projects/new.md", "abc123", "create", None)

    rows = list_events(kind="trigger.fire")
    assert len(rows) == 1
    assert rows[0]["target"] == "trg_dir"
    payload = rows[0]["payload"]
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

    _seed_user_and_trigger(scope_path="projects", tid="trg_dir")

    _patch_io(monkeypatch, before="status: green\n", after="status: yellow\n")
    monkeypatch.setattr(
        engine, "nl_matches", lambda *a, **kw: MatchResult(matched=True, reason="green→yellow")
    )
    monkeypatch.setattr(
        engine, "nl_render_message", lambda *a, **kw: "rendered text"
    )

    def boom_new_file(*a, **kw):
        raise AssertionError("new-file path should not run for edits")

    monkeypatch.setattr(engine, "nl_evaluate_new_file_in_dir", boom_new_file)

    trig_task.fan_out_trigger_eval("projects/foo.md", "abc123", "edit", None)

    rows = list_events(kind="trigger.fire")
    assert len(rows) == 1
    payload = rows[0]["payload"]
    assert payload["message"] == "rendered text"
    assert payload["reason"] == "green→yellow"


def test_fan_out_fires_when_non_admin_owner_has_explicit_read_grant(tmp_db, monkeypatch):
    """Positive regression: a non-admin owner with an explicit `read`
    grant on a managed scope still fires through the fan-out gate. Pairs
    with the owner-revoked test below."""
    from app.tasks import triggers as trig_task
    from app.triggers import engine
    from app.wiki import acl

    seed_user(uid="usr_1", email="u@x.com", is_admin=False)
    other = seed_user(uid="usr_other", email="o@x.com")
    acl.set_owner("projects/foo.md", other)
    acl.grant(
        resource_kind="page",
        resource_path="projects/foo.md",
        principal_kind="user",
        principal_id="usr_1",
        permission="read",
        granted_by_user_id=other,
    )

    seed_trigger(
        tid="trg_grant",
        owner_user_id="usr_1",
        scope_path="projects/foo.md",
        message="msg",
    )

    _patch_io(monkeypatch, before="x", after="y")
    monkeypatch.setattr(
        engine, "nl_matches", lambda *a, **kw: MatchResult(matched=True, reason="x→y")
    )
    monkeypatch.setattr(engine, "nl_render_message", lambda *a, **kw: "rendered")

    trig_task.fan_out_trigger_eval("projects/foo.md", "deadbeef", "edit", None)

    rows = list_events(kind="trigger.fire")
    assert len(rows) == 1
    assert rows[0]["target"] == "trg_grant"


def test_fan_out_skips_event_when_owner_lacks_read_access(tmp_db, monkeypatch):
    """Owner-revocation safety: a trigger whose owner can no longer read
    the changed doc should NOT produce a fire row, even if the NL match
    would otherwise succeed."""
    from app.tasks import triggers as trig_task
    from app.triggers import engine
    from app.wiki import acl

    # Non-admin owner, then a private ACL on the doc that excludes them.
    seed_user(uid="usr_1", email="u@x.com", is_admin=False)
    other = seed_user(uid="usr_other", email="o@x.com")
    acl.set_owner("projects/foo.md", other)  # owned by someone else → usr_1 has no read

    seed_trigger(
        tid="trg_revoked",
        owner_user_id="usr_1",
        scope_path="projects/foo.md",
        message="msg",
    )

    _patch_io(monkeypatch, before="x", after="y")

    def boom_match(*a, **kw):
        raise AssertionError("match should not run when owner lacks read")

    monkeypatch.setattr(engine, "nl_matches", boom_match)
    monkeypatch.setattr(engine, "nl_render_message", boom_match)

    trig_task.fan_out_trigger_eval("projects/foo.md", "deadbeef", "edit", None)

    rows = list_events(kind="trigger.fire")
    assert rows == []


def test_fan_out_doc_scope_create_uses_two_phase_flow(tmp_db, monkeypatch):
    """A doc-scoped trigger (scope_path == doc_path) on create should NOT
    use the new-file path — that's reserved for directory-scoped triggers."""
    from app.tasks import triggers as trig_task
    from app.triggers import engine

    _seed_user_and_trigger(scope_path="projects/foo.md", tid="trg_doc")

    _patch_io(monkeypatch, before="", after="# Foo\n")
    monkeypatch.setattr(
        engine, "nl_matches", lambda *a, **kw: MatchResult(matched=True, reason="doc created")
    )
    monkeypatch.setattr(
        engine, "nl_render_message", lambda *a, **kw: "doc created"
    )

    def boom(*a, **kw):
        raise AssertionError("new-file path should not run for doc scope")

    monkeypatch.setattr(engine, "nl_evaluate_new_file_in_dir", boom)

    trig_task.fan_out_trigger_eval("projects/foo.md", "abc123", "create", None)

    rows = list_events(kind="trigger.fire")
    assert len(rows) == 1
