"""Tests for ``app/triggers/engine.py``.

Covers the SQL-driven matching layer. The NL evaluator is exercised in
``test_triggers_natural_language.py``; here we just verify the right rows
come back for a given doc path.
"""
from __future__ import annotations

from tests._seed import seed_trigger, seed_user


def test_find_matching_triggers_includes_file_and_parent_dirs(tmp_db):
    from app.triggers.engine import find_matching_triggers

    uid = seed_user(is_admin=True)
    seed_trigger(tid="t_file", owner_user_id=uid, scope_path="projects/foo.md")
    seed_trigger(tid="t_dir", owner_user_id=uid, scope_path="projects")
    seed_trigger(tid="t_other", owner_user_id=uid, scope_path="other")

    rows = find_matching_triggers("projects/foo.md")
    assert {r.id for r in rows} == {"t_file", "t_dir"}


def test_find_matching_triggers_skips_disabled_and_non_delta(tmp_db):
    from app.triggers.engine import find_matching_triggers

    uid = seed_user(is_admin=True)
    seed_trigger(tid="t_on", owner_user_id=uid, scope_path="a.md")
    seed_trigger(tid="t_off", owner_user_id=uid, scope_path="a.md", enabled=False)
    seed_trigger(tid="t_sched", owner_user_id=uid, scope_path="a.md", kind="schedule")

    rows = find_matching_triggers("a.md")
    assert {r.id for r in rows} == {"t_on"}


def test_find_matching_triggers_empty_when_nothing_seeded(tmp_db):
    from app.triggers.engine import find_matching_triggers

    assert find_matching_triggers("any/path.md") == []


def test_find_matching_triggers_includes_root_scope(tmp_db):
    """A trigger with scope_path='' (root) should fire on any doc update,
    including docs in nested directories and docs at the wiki root."""
    from app.triggers.engine import find_matching_triggers

    uid = seed_user(is_admin=True)
    seed_trigger(tid="t_root", owner_user_id=uid, scope_path="")

    assert {r.id for r in find_matching_triggers("a/b/c.md")} == {"t_root"}
    assert {r.id for r in find_matching_triggers("foo.md")} == {"t_root"}
