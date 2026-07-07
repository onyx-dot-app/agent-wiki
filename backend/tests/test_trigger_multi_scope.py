"""Multi-scope watch lists and line-range gating: matching covers every
entry, the most specific entry's line range governs, YAML round-trips the
list, and the API validates and mirrors it."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.triggers import repo as triggers_repo
from app.triggers import storage
from app.triggers.diff import change_touches_lines
from app.triggers.engine import (
    TriggerRecord,
    TriggerScope,
    find_matching_triggers,
    matched_scope,
)

from tests._auth import login_fastapi
from tests._seed import seed_trigger, seed_user


@pytest.fixture
def client(tmp_repo):
    return TestClient(create_app())


def _record(scopes: list[TriggerScope]) -> TriggerRecord:
    return TriggerRecord(
        id="trg_x",
        owner_user_id="u1",
        scope_path=scopes[0].path,
        scopes=scopes,
        kind="delta",
        nl_description="always",
        actions=[],
        enabled=True,
        file_path=None,
        created_at=None,
        last_edited_at=None,
    )


def test_find_matching_covers_extra_scopes(tmp_db):
    uid = seed_user(email="u@x.com")
    seed_trigger(
        tid="trg_multi",
        owner_user_id=uid,
        scope_path="a.md",
        scopes=[{"path": "a.md"}, {"path": "notes"}],
    )
    assert [t.id for t in find_matching_triggers("a.md")] == ["trg_multi"]
    assert [t.id for t in find_matching_triggers("notes/deep/b.md")] == ["trg_multi"]
    assert find_matching_triggers("other.md") == []


def test_legacy_row_without_scopes_json_still_matches(tmp_db):
    uid = seed_user(email="u@x.com")
    seed_trigger(tid="trg_legacy", owner_user_id=uid, scope_path="a.md")
    assert [t.id for t in find_matching_triggers("a.md")] == ["trg_legacy"]


def test_matched_scope_prefers_most_specific():
    rec = _record(
        [
            TriggerScope(path=""),
            TriggerScope(path="notes"),
            TriggerScope(path="notes/a.md", start_line=6, end_line=9),
        ]
    )
    match = matched_scope(rec, "notes/a.md")
    assert match is not None and match.path == "notes/a.md"
    assert match.start_line == 6
    folder = matched_scope(rec, "notes/other.md")
    assert folder is not None and folder.path == "notes"


def test_change_touches_lines_gates_correctly():
    before = "\n".join(f"line {i}" for i in range(1, 11)) + "\n"
    inside = before.replace("line 7", "line 7 CHANGED")
    outside = before.replace("line 2", "line 2 CHANGED")
    assert change_touches_lines(before, inside, 6, 9)
    assert not change_touches_lines(before, outside, 6, 9)
    assert not change_touches_lines(before, before, 6, 9)
    # New file: fires only when the range exists in the body.
    assert change_touches_lines("", before, 6, 9)
    assert not change_touches_lines("", "one line\n", 6, 9)
    # Whole-file deletion always fires.
    assert change_touches_lines(before, "", 6, 9)


def test_yaml_round_trip_preserves_scopes(tmp_db):
    trigger = {
        "id": "trg_yaml",
        "owner_user_id": "u1",
        "scope_path": "a.md",
        "scopes": [
            {"path": "a.md", "start_line": 6, "end_line": 9},
            {"path": "notes"},
        ],
        "kind": "delta",
        "nl_description": "always",
        "actions": [{"destination_config_id": None, "message": "m"}],
        "enabled": True,
        "created_at": None,
    }
    parsed = storage.parse(storage.serialize(trigger))
    assert parsed["scopes"] == trigger["scopes"]


def test_yaml_single_scope_stays_clean(tmp_db):
    trigger = {
        "id": "trg_single",
        "owner_user_id": "u1",
        "scope_path": "a.md",
        "scopes": [{"path": "a.md"}],
        "kind": "delta",
        "nl_description": "always",
        "actions": [{"destination_config_id": None, "message": "m"}],
        "enabled": True,
        "created_at": None,
    }
    text = storage.serialize(trigger)
    assert "scopes:" not in text
    assert storage.parse(text)["scopes"] is None


def test_api_create_with_scopes_and_view(client):
    uid = seed_user(email="u@x.com")
    login_fastapi(client, uid)
    r = client.post(
        "/api/triggers",
        json={
            "scope_path": "a.md",
            "scopes": [
                {"path": "a.md", "start_line": 6, "end_line": 9},
                {"path": "notes"},
            ],
            "nl_description": "always",
            "actions": [{"destination_config_id": None, "message": "m"}],
        },
    )
    assert r.status_code == 201, r.text
    view = r.json()
    assert view["scope_path"] == "a.md"
    assert view["scopes"] == [
        {"path": "a.md", "start_line": 6, "end_line": 9},
        {"path": "notes", "start_line": None, "end_line": None},
    ]
    # The cache row matches both watched paths.
    assert [t.id for t in find_matching_triggers("notes/x.md")] == [view["id"]]


def test_api_rejects_bad_ranges(client):
    uid = seed_user(email="u@x.com")
    login_fastapi(client, uid)
    base = {
        "scope_path": "a.md",
        "nl_description": "always",
        "actions": [{"destination_config_id": None, "message": "m"}],
    }
    r = client.post(
        "/api/triggers",
        json={**base, "scopes": [{"path": "a.md", "start_line": 9, "end_line": 6}]},
    )
    assert r.status_code == 400
    r = client.post(
        "/api/triggers",
        json={**base, "scopes": [{"path": "notes", "start_line": 1, "end_line": 2}]},
    )
    assert r.status_code == 400


def test_update_replaces_watch_list(client):
    uid = seed_user(email="u@x.com")
    login_fastapi(client, uid)
    created = client.post(
        "/api/triggers",
        json={
            "scope_path": "a.md",
            "scopes": [{"path": "a.md"}, {"path": "notes"}],
            "nl_description": "always",
            "actions": [{"destination_config_id": None, "message": "m"}],
        },
    ).json()
    r = client.put(
        f"/api/triggers/{created['id']}",
        json={"scopes": [{"path": "a.md", "start_line": 1, "end_line": 3}]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["scopes"] == [
        {"path": "a.md", "start_line": 1, "end_line": 3}
    ]
    assert find_matching_triggers("notes/x.md") == []


def test_rebuild_restores_scopes(tmp_repo):
    uid = seed_user(email="u@x.com")
    created = triggers_repo.create(
        owner_user_id=uid,
        scope_path="a.md",
        scopes=[{"path": "a.md", "start_line": 2}, {"path": "notes"}],
        nl_description="always",
        actions=[{"destination_config_id": None, "message": "m"}],
    )
    triggers_repo.rebuild_from_filesystem()
    row = triggers_repo.get(created["id"])
    assert row is not None
    assert row["scopes"] == [
        {"path": "a.md", "start_line": 2},
        {"path": "notes"},
    ]


def test_update_rejects_empty_scopes(client):
    uid = seed_user(email="u@x.com")
    login_fastapi(client, uid)
    created = client.post(
        "/api/triggers",
        json={
            "scope_path": "a.md",
            "scopes": [{"path": "a.md"}, {"path": "notes"}],
            "nl_description": "always",
            "actions": [{"destination_config_id": None, "message": "m"}],
        },
    ).json()
    r = client.put(f"/api/triggers/{created['id']}", json={"scopes": []})
    assert r.status_code == 400
    # The old watch list is untouched.
    row = triggers_repo.get(created["id"])
    assert row is not None and len(row["scopes"]) == 2


def test_rebuild_drops_invalid_scope_lists(tmp_repo):
    uid = seed_user(email="u@x.com")
    created = triggers_repo.create(
        owner_user_id=uid,
        scope_path="a.md",
        scopes=[{"path": "a.md"}, {"path": "notes"}],
        nl_description="always",
        actions=[{"destination_config_id": None, "message": "m"}],
    )
    # Hand-corrupt the YAML: a range on a folder entry fails validation.
    from app.wiki import git as wiki_git

    file_path = created["file_path"]
    body = wiki_git.read_file(file_path)
    body = body.replace("- path: notes", "- path: notes\n  start_line: 3")
    wiki_git.commit_file(file_path, body, message="corrupt", author="t <t@x>")
    triggers_repo.rebuild_from_filesystem()
    row = triggers_repo.get(created["id"])
    assert row is not None
    assert row["scopes"] == [{"path": "a.md"}]


def test_scope_path_rebind_resets_ranged_single_scope(client):
    uid = seed_user(email="u@x.com")
    login_fastapi(client, uid)
    created = client.post(
        "/api/triggers",
        json={
            "scope_path": "a.md",
            "scopes": [{"path": "a.md", "start_line": 6, "end_line": 9}],
            "nl_description": "always",
            "actions": [{"destination_config_id": None, "message": "m"}],
        },
    ).json()
    r = client.put(f"/api/triggers/{created['id']}", json={"scope_path": "b.md"})
    assert r.status_code == 200, r.text
    assert r.json()["scopes"] == [{"path": "b.md", "start_line": None, "end_line": None}]
    assert [t.id for t in find_matching_triggers("b.md")] == [created["id"]]
    assert find_matching_triggers("a.md") == []


def test_rebuild_normalizes_slash_whole_wiki(tmp_repo):
    uid = seed_user(email="u@x.com")
    created = triggers_repo.create(
        owner_user_id=uid,
        scope_path="",
        nl_description="always",
        actions=[{"destination_config_id": None, "message": "m"}],
    )
    # Hand-edit the YAML to the "/" spelling of the whole wiki.
    from app.wiki import git as wiki_git

    file_path = created["file_path"]
    body = wiki_git.read_file(file_path)
    body = body.replace("scope_path: ''", "scope_path: /")
    wiki_git.commit_file(file_path, body, message="hand edit", author="t <t@x>")
    triggers_repo.rebuild_from_filesystem()
    row = triggers_repo.get(created["id"])
    assert row is not None
    assert row["scope_path"] == ""
    assert [t.id for t in find_matching_triggers("anything/x.md")] == [created["id"]]
