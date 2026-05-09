"""End-to-end: ``PUT /api/documents/file`` → commit → trigger fan-out → event row.

The save-button save path. The unit-level fan-out tests
(``test_triggers_fanout.py``) exercise the task in isolation; this file
verifies that the API actually enqueues it and that the queue routing,
trigger matching, and event insert all line up.

We patch only the LLM evaluators (``nl_matches`` /
``nl_evaluate_new_file_in_dir`` / ``nl_render_message``) — everything
else (git commit, FTS reindex, task queues, find_matching_triggers) runs
for real against a tmp wiki repo.
"""
from __future__ import annotations

import json

import pytest

from app.triggers.natural_language import MatchResult, NewFileEvalResult

from tests._seed import list_events


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


@pytest.fixture
def app(tmp_repo):
    from app.main import create_app

    flask_app = create_app()
    flask_app.config["TESTING"] = True
    return flask_app


@pytest.fixture
def signed_in(app, tmp_repo):
    """Seed a user, log in, return ``(client, user_id)``."""
    from app.auth import users as users_repo

    user_id = users_repo.create(email="u@x.com", password="hunter2", name="U")
    client = app.test_client()
    resp = client.post(
        "/api/auth/login", json={"email": "u@x.com", "password": "hunter2"}
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return client, user_id


@pytest.fixture(autouse=True)
def _immediate_queues():
    """Run tasks synchronously so the test sees the event row before
    its assertions. Both queues touched by the save path go immediate.
    """
    from contextlib import ExitStack

    from app.tasks.queues import triggers_queue, wiki_bm25_queue

    with ExitStack() as stack:
        stack.enter_context(triggers_queue.immediate_mode())
        stack.enter_context(wiki_bm25_queue.immediate_mode())
        yield


def _seed_trigger(*, owner_user_id, scope_path, nl="fire when status changes"):
    from app.triggers import repo

    return repo.create(
        owner_user_id=owner_user_id,
        scope_path=scope_path,
        nl_description=nl,
        message="status changed",
    )


def _list_fires():
    """Return ``trigger.fire`` events oldest-first."""
    return list(reversed(list_events(kind="trigger.fire")))


def _put_doc(client, *, path, body):
    return client.put(
        "/api/documents/file",
        json={"path": path, "body": body},
    )


# --------------------------------------------------------------------------- #
# Doc-scoped trigger                                                          #
# --------------------------------------------------------------------------- #


def test_save_fires_doc_scoped_trigger_to_event_log(signed_in, monkeypatch):
    """Trigger attached directly to the saved doc — the LLM eval matches and
    the fire is recorded in the events table with the rendered message."""
    client, uid = signed_in

    trigger = _seed_trigger(owner_user_id=uid, scope_path="projects/foo.md")

    # Patch the two LLM hops the standard delta flow uses.
    from app.triggers import engine

    monkeypatch.setattr(
        engine, "nl_matches", lambda nl, payload: MatchResult(matched=True, reason="status flipped")
    )
    monkeypatch.setattr(
        engine,
        "nl_render_message",
        lambda instr, payload, *, reason: f"[msg] {instr}: {reason}",
    )
    monkeypatch.setattr(
        engine,
        "nl_evaluate_new_file_in_dir",
        lambda nl, instr, payload: NewFileEvalResult(triggered=False, message=""),
    )

    # First save: create the file. Trigger doesn't fire here because the
    # scope_path == doc_path (so the standard flow runs), but we want to
    # exercise the create path too.
    resp = _put_doc(
        client, path="projects/foo.md", body="status: green\n"
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)

    # Second save: an edit. This is what the save button typically triggers.
    resp = _put_doc(
        client, path="projects/foo.md", body="status: yellow\n"
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)

    fires = _list_fires()
    # Two fires expected — one for the create commit, one for the edit.
    # Both go through the standard delta flow because the trigger's scope
    # equals the doc path.
    assert len(fires) == 2

    last = fires[-1]
    assert last["target"] == trigger["id"]
    assert last["actor"] == "U <u@x.com>"

    payload = json.loads(last["payload_json"])
    assert payload["doc_path"] == "projects/foo.md"
    assert payload["change_kind"] == "edit"
    assert payload["reason"] == "status flipped"
    assert payload["message"] == "[msg] status changed: status flipped"
    assert payload["destination"] == "event_log"


# --------------------------------------------------------------------------- #
# Directory-scoped trigger (parent dir + ROOT)                                #
# --------------------------------------------------------------------------- #


def test_save_fires_parent_dir_scoped_trigger(signed_in, monkeypatch):
    """Trigger attached to the parent directory still fires when a doc
    inside that directory is edited (find_matching_triggers walks the
    parent chain)."""
    client, uid = signed_in

    trigger = _seed_trigger(
        owner_user_id=uid, scope_path="projects", nl="anything in projects"
    )

    from app.triggers import engine

    monkeypatch.setattr(engine, "nl_matches", lambda nl, payload: MatchResult(matched=True, reason="matched"))
    monkeypatch.setattr(
        engine, "nl_render_message", lambda instr, payload, *, reason: "rendered"
    )
    # Even though this is an edit-in-dir, the directory-scope edit path
    # uses the standard two-phase flow (per fan_out_trigger_eval), not the
    # combined create-only path. Stub both anyway so unintended dispatches
    # don't silently slip through.
    monkeypatch.setattr(
        engine,
        "nl_evaluate_new_file_in_dir",
        lambda nl, instr, payload: NewFileEvalResult(triggered=False, message=""),
    )

    # Create then edit. The CREATE goes through evaluate_new_file_in_dir
    # (which we stubbed to return False), so only the EDIT should fire.
    resp = _put_doc(client, path="projects/foo.md", body="initial\n")
    assert resp.status_code == 200

    resp = _put_doc(client, path="projects/foo.md", body="updated\n")
    assert resp.status_code == 200

    fires = _list_fires()
    assert len(fires) == 1
    assert fires[0]["target"] == trigger["id"]
    payload = json.loads(fires[0]["payload_json"])
    assert payload["doc_path"] == "projects/foo.md"
    assert payload["change_kind"] == "edit"


def test_save_fires_root_scoped_trigger(signed_in, monkeypatch):
    """Trigger attached to the wiki root (`scope_path = ""`) fires for any
    doc's edit, anywhere in the tree."""
    client, uid = signed_in

    trigger = _seed_trigger(
        owner_user_id=uid, scope_path="", nl="anything anywhere"
    )

    from app.triggers import engine

    monkeypatch.setattr(engine, "nl_matches", lambda nl, payload: MatchResult(matched=True, reason="root match"))
    monkeypatch.setattr(engine, "nl_render_message", lambda i, p, *, reason: "msg")
    monkeypatch.setattr(
        engine,
        "nl_evaluate_new_file_in_dir",
        lambda nl, instr, payload: NewFileEvalResult(triggered=False, message=""),
    )

    # Two unrelated docs in different parts of the tree.
    _put_doc(client, path="alpha.md", body="hello\n")
    _put_doc(client, path="beta/gamma.md", body="world\n")

    # Edit the second one — this is the case the user typically saves.
    resp = _put_doc(client, path="beta/gamma.md", body="changed\n")
    assert resp.status_code == 200

    fires = _list_fires()
    assert len(fires) >= 1
    last = fires[-1]
    assert last["target"] == trigger["id"]
    payload = json.loads(last["payload_json"])
    assert payload["doc_path"] == "beta/gamma.md"


# --------------------------------------------------------------------------- #
# No-match: trigger exists but the LLM says no                                #
# --------------------------------------------------------------------------- #


def test_save_does_not_fire_when_eval_returns_no_match(signed_in, monkeypatch):
    """The save commits and reindexes, but no event is written when the
    LLM says the change isn't relevant."""
    client, uid = signed_in
    _seed_trigger(owner_user_id=uid, scope_path="projects/foo.md")

    from app.triggers import engine

    monkeypatch.setattr(
        engine, "nl_matches", lambda nl, payload: MatchResult(matched=False, reason="irrelevant")
    )

    resp = _put_doc(client, path="projects/foo.md", body="status: green\n")
    assert resp.status_code == 200

    assert _list_fires() == []


def test_save_with_no_triggers_writes_no_events(signed_in):
    """Sanity check: a save with no triggers in the DB shouldn't insert
    anything into the events table."""
    client, _uid = signed_in

    resp = _put_doc(client, path="solo.md", body="alone\n")
    assert resp.status_code == 200

    assert _list_fires() == []


# --------------------------------------------------------------------------- #
# Owner attribution                                                           #
# --------------------------------------------------------------------------- #


def test_chat_agent_edit_fires_through_same_seam(signed_in, monkeypatch):
    """Chat-agent edits go through ``_doc_helpers.commit_and_fan_out``,
    which now routes through ``wiki.notify.after_doc_write`` — so the
    same trigger fan-out should fire when the agent edits a doc."""
    _, uid = signed_in

    trigger = _seed_trigger(owner_user_id=uid, scope_path="agent_doc.md")

    from app.triggers import engine

    monkeypatch.setattr(engine, "nl_matches", lambda nl, payload: MatchResult(matched=True, reason="agent edit"))
    monkeypatch.setattr(
        engine, "nl_render_message", lambda i, p, *, reason: f"[agent] {reason}"
    )

    # Seed an existing doc for edit_doc to mutate.
    from app.wiki import git as wiki_git
    wiki_git.commit_file("agent_doc.md", "before\n", "seed", author=None)

    # Mark the path as "seen" so the read-before-write guard passes,
    # then call the chat-agent edit tool directly.
    from app.llm.agents._session import seen_doc_paths
    from app.llm.agents.tools.edit_doc import handle as edit_doc

    token = seen_doc_paths.set({"agent_doc.md"})
    try:
        out = edit_doc(
            {
                "path": "agent_doc.md",
                "old_string": "before",
                "new_string": "after",
                "commit_message": "tighten",
            }
        )
    finally:
        seen_doc_paths.reset(token)
    assert "error" not in out, out

    fires = _list_fires()
    assert len(fires) == 1
    assert fires[0]["target"] == trigger["id"]


def test_move_fires_delete_on_old_and_create_on_new(signed_in, monkeypatch):
    """Renaming a doc: triggers attached to the old path's parent dir
    see a ``delete``, triggers attached to the new path's parent dir see
    a ``create``."""
    client, uid = signed_in

    old_dir_trigger = _seed_trigger(
        owner_user_id=uid, scope_path="src", nl="anything in src"
    )
    new_dir_trigger = _seed_trigger(
        owner_user_id=uid, scope_path="dst", nl="anything in dst"
    )

    from app.triggers import engine

    monkeypatch.setattr(
        engine, "nl_matches", lambda nl, payload: MatchResult(matched=True, reason="directory delta")
    )
    monkeypatch.setattr(
        engine, "nl_render_message", lambda i, p, *, reason: "moved"
    )
    # New-file-in-dir flow runs for create against directory scope.
    monkeypatch.setattr(
        engine,
        "nl_evaluate_new_file_in_dir",
        lambda nl, instr, payload: NewFileEvalResult(triggered=True, message="moved-in"),
    )

    # Seed a file under src/.
    from app.wiki import git as wiki_git
    wiki_git.commit_file("src/foo.md", "body\n", "seed", author=None)
    # Clear any fires generated by the seed commit (root scope etc. — none here).
    from tests._seed import clear_events
    clear_events()

    resp = client.post(
        "/api/documents/move",
        json={"old_path": "src/foo.md", "new_path": "dst/foo.md"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)

    fires_by_target = {row["target"]: row for row in _list_fires()}
    assert old_dir_trigger["id"] in fires_by_target, (
        "old-dir trigger should fire on the delete half of the move"
    )
    assert new_dir_trigger["id"] in fires_by_target, (
        "new-dir trigger should fire on the create half of the move"
    )

    old_payload = json.loads(fires_by_target[old_dir_trigger["id"]]["payload_json"])
    new_payload = json.loads(fires_by_target[new_dir_trigger["id"]]["payload_json"])
    assert old_payload["change_kind"] == "delete"
    assert old_payload["doc_path"] == "src/foo.md"
    assert new_payload["change_kind"] == "create"
    assert new_payload["doc_path"] == "dst/foo.md"


def test_delete_fires_with_change_kind_delete(signed_in, monkeypatch):
    """Deleting a doc fans out as ``change_kind=delete`` so dir-scoped
    triggers can react to the removal."""
    client, uid = signed_in

    trigger = _seed_trigger(
        owner_user_id=uid, scope_path="docs", nl="something removed"
    )

    from app.triggers import engine

    monkeypatch.setattr(engine, "nl_matches", lambda nl, payload: MatchResult(matched=True, reason="gone"))
    monkeypatch.setattr(engine, "nl_render_message", lambda i, p, *, reason: "deleted")

    from app.wiki import git as wiki_git
    wiki_git.commit_file("docs/old.md", "text\n", "seed", author=None)
    # Wipe any seed-time fires.
    from tests._seed import clear_events
    clear_events()

    resp = client.delete("/api/documents/file?path=docs/old.md")
    assert resp.status_code == 200, resp.get_data(as_text=True)

    fires = _list_fires()
    assert len(fires) == 1
    assert fires[0]["target"] == trigger["id"]
    payload = json.loads(fires[0]["payload_json"])
    assert payload["change_kind"] == "delete"
    assert payload["doc_path"] == "docs/old.md"


def test_fire_records_actor_from_save_author(signed_in, monkeypatch):
    """The actor field on the event row is the saving user's git author."""
    client, uid = signed_in
    _seed_trigger(owner_user_id=uid, scope_path="x.md")

    from app.triggers import engine

    monkeypatch.setattr(engine, "nl_matches", lambda nl, payload: MatchResult(matched=True, reason="ok"))
    monkeypatch.setattr(
        engine, "nl_render_message", lambda i, p, *, reason: "rendered"
    )

    _put_doc(client, path="x.md", body="hi\n")
    _put_doc(client, path="x.md", body="bye\n")

    fires = _list_fires()
    assert fires
    # _git_author shape: f"{name or email} <{email}>"
    assert fires[-1]["actor"] == "U <u@x.com>"
