"""CRUD tests for ``app/triggers/repo.py``."""

from __future__ import annotations

import pytest

from tests._seed import insert_event, seed_user


def _create(repo, *, owner_user_id, scope_path, nl_description, **kw):
    """Test shorthand — supplies the now-required ``message`` field."""
    kw.setdefault("message", "default message")
    return repo.create(
        owner_user_id=owner_user_id,
        scope_path=scope_path,
        nl_description=nl_description,
        **kw,
    )


def test_create_and_get(tmp_repo):
    from app.triggers import repo

    uid = seed_user(email="a@b.com")
    t = _create(
        repo,
        owner_user_id=uid,
        scope_path="projects/foo.md",
        nl_description="status",
        message="status changed",
    )
    assert t["id"].startswith("trg_")
    assert t["scope_path"] == "projects/foo.md"
    assert t["enabled"] is True
    assert t["kind"] == "delta"
    assert t["message"] == "status changed"
    assert t["destination_config_id"] is None

    fetched = repo.get(t["id"])
    assert fetched == t


def test_list_for_owner_filters_by_owner(tmp_repo):
    from app.triggers import repo

    a = seed_user("usr_a", "a@x.com")
    b = seed_user("usr_b", "b@x.com")
    _create(repo, owner_user_id=a, scope_path="x.md", nl_description="x")
    _create(repo, owner_user_id=a, scope_path="y.md", nl_description="y")
    _create(repo, owner_user_id=b, scope_path="z.md", nl_description="z")

    a_rows = repo.list_for_owner(a)
    assert {r["scope_path"] for r in a_rows} == {"x.md", "y.md"}
    b_rows = repo.list_for_owner(b)
    assert {r["scope_path"] for r in b_rows} == {"z.md"}


def test_update_partial(tmp_repo):
    from app.triggers import repo

    uid = seed_user(email="a@b.com")
    t = _create(
        repo,
        owner_user_id=uid,
        scope_path="a.md",
        nl_description="original",
        message="m",
    )

    updated = repo.update(t["id"], nl_description="changed")
    assert updated is not None
    assert updated["nl_description"] == "changed"
    assert updated["scope_path"] == "a.md"
    assert updated["enabled"] is True
    assert updated["message"] == "m"

    re_msg = repo.update(t["id"], message="m2")
    assert re_msg is not None
    assert re_msg["message"] == "m2"
    assert re_msg["nl_description"] == "changed"

    toggled = repo.update(t["id"], enabled=False)
    assert toggled is not None
    assert toggled["enabled"] is False
    assert toggled["message"] == "m2"


def test_update_with_no_fields_returns_current(tmp_repo):
    from app.triggers import repo

    uid = seed_user(email="a@b.com")
    t = _create(repo, owner_user_id=uid, scope_path="a.md", nl_description="x")
    out = repo.update(t["id"])
    assert out == t


def test_delete(tmp_repo):
    from app.triggers import repo

    uid = seed_user(email="a@b.com")
    t = _create(repo, owner_user_id=uid, scope_path="a.md", nl_description="x")
    assert repo.delete(t["id"]) is True
    assert repo.get(t["id"]) is None
    assert repo.delete(t["id"]) is False  # second call no-op


def test_create_rejects_unsupported_kind(tmp_repo):
    from app.triggers import repo

    uid = seed_user(email="a@b.com")
    with pytest.raises(ValueError):
        _create(
            repo,
            owner_user_id=uid,
            scope_path="a.md",
            nl_description="x",
            kind="schedule",
        )


def test_create_rejects_missing_message(tmp_repo):
    from app.triggers import repo

    uid = seed_user(email="a@b.com")
    with pytest.raises(ValueError, match="message"):
        repo.create(
            owner_user_id=uid,
            scope_path="a.md",
            nl_description="x",
            message="",
        )


def test_create_rejects_unowned_destination_config(tmp_repo):
    from app.triggers import repo

    uid = seed_user(email="a@b.com")
    with pytest.raises(ValueError, match="destination_config_id"):
        repo.create(
            owner_user_id=uid,
            scope_path="a.md",
            nl_description="x",
            message="m",
            destination_config_id="dst_nonexistent",
        )


def test_update_rejects_empty_message(tmp_repo):
    from app.triggers import repo

    uid = seed_user(email="a@b.com")
    t = _create(repo, owner_user_id=uid, scope_path="a.md", nl_description="x")
    with pytest.raises(ValueError, match="message"):
        repo.update(t["id"], message="")


def test_fire_counts_by_sha_tallies_only_matching_fires(tmp_repo):
    from app.triggers import repo

    sha_a = "a" * 40
    sha_b = "b" * 40
    sha_unfired = "c" * 40
    insert_event("trigger.fire", "trg_1", {"sha": sha_a, "doc_path": "x.md"})
    insert_event("trigger.fire", "trg_2", {"sha": sha_a, "doc_path": "y.md"})
    insert_event("trigger.fire", "trg_3", {"sha": sha_b, "doc_path": "z.md"})
    # Wrong kind — must not count even though the sha matches.
    insert_event("trigger.eval", "trg_4", {"sha": sha_a})

    counts = repo.fire_counts_by_sha({sha_a, sha_b, sha_unfired})

    assert counts == {sha_a: 2, sha_b: 1}


def test_fire_counts_by_sha_empty_input(tmp_repo):
    from app.triggers import repo

    assert repo.fire_counts_by_sha(set()) == {}


# --------------------------------------------------------------------------- #
# Scope follows a path move (repoint_scopes_for_moves via after_path_move)     #
# --------------------------------------------------------------------------- #


def test_folder_rename_repoints_doc_scoped_trigger(tmp_repo):
    # A folder move sweeps the doc *and* its sibling .trigger YAML; the YAML's
    # scope_path content must be rewritten to the doc's new path.
    from app.triggers import repo
    from app.wiki import git as wiki_git, notify

    uid = seed_user(email="a@b.com")
    wiki_git.commit_file("proj/old/doc.md", "# Doc\n", "seed", author=None)
    t = _create(
        repo, owner_user_id=uid, scope_path="proj/old/doc.md", nl_description="x", message="m"
    )

    sha, moves = wiki_git.move_path("proj/old", "proj/new", "move folder", author=None)
    notify.after_path_move(moves, sha, actor=None)

    got = repo.get(t["id"])
    assert got is not None
    assert got["scope_path"] == "proj/new/doc.md"
    assert got["file_path"] == f"proj/new/.trigger_{t['id']}_doc.yaml"


def test_folder_rename_repoints_folder_scoped_trigger(tmp_repo):
    from app.triggers import repo
    from app.wiki import git as wiki_git, notify

    uid = seed_user(email="a@b.com")
    t = _create(repo, owner_user_id=uid, scope_path="proj/old", nl_description="x", message="m")

    sha, moves = wiki_git.move_path("proj/old", "proj/new", "move folder", author=None)
    notify.after_path_move(moves, sha, actor=None)

    got = repo.get(t["id"])
    assert got is not None
    assert got["scope_path"] == "proj/new"
    assert got["file_path"] == f"proj/new/.trigger_{t['id']}.yaml"


def test_single_doc_rename_relocates_doc_scoped_trigger(tmp_repo):
    # Renaming just the doc does NOT sweep the sibling YAML, so the trigger must
    # be relocated + rewritten to the doc's new path (and renamed docbase).
    from app.triggers import repo
    from app.wiki import git as wiki_git, notify

    uid = seed_user(email="a@b.com")
    wiki_git.commit_file("notes/doc.md", "# Doc\n", "seed", author=None)
    t = _create(
        repo, owner_user_id=uid, scope_path="notes/doc.md", nl_description="x", message="m"
    )

    sha, moves = wiki_git.move_path("notes/doc.md", "notes/renamed.md", "rename doc", author=None)
    notify.after_path_move(moves, sha, actor=None)

    got = repo.get(t["id"])
    assert got is not None
    assert got["scope_path"] == "notes/renamed.md"
    assert got["file_path"] == f"notes/.trigger_{t['id']}_renamed.yaml"


def test_parse_actions_reads_legacy_single_destination_blob():
    """A pre-multi-action ``action_json`` blob loads as one action."""
    import json

    from app.triggers.repo import _parse_actions

    legacy = json.dumps({"message": "hi", "destination": "slack"})
    assert _parse_actions(legacy) == [{"destination_config_id": None, "message": "hi"}]
