"""Tests for ``app/triggers/engine.py``.

Covers the SQL-driven matching layer. The NL evaluator is exercised in
``test_triggers_natural_language.py``; here we just verify the right rows
come back for a given doc path.
"""
from __future__ import annotations

from app.db.sqlite import connect


def _seed_user(conn) -> None:
    conn.execute(
        "INSERT INTO users(id, email, password_hash, is_admin) VALUES (?, ?, ?, ?)",
        ("usr_1", "u@x.com", "x", 1),
    )


def _seed_trigger(
    conn, *, scope_path: str, tid: str, enabled: int = 1, kind: str = "delta"
) -> None:
    conn.execute(
        "INSERT INTO triggers(id, owner_user_id, scope_path, kind, nl_description, action_json, enabled) "
        "VALUES (?, ?, ?, ?, ?, '{}', ?)",
        (tid, "usr_1", scope_path, kind, "fire when status changes", enabled),
    )


def test_find_matching_triggers_includes_file_and_parent_dirs(tmp_db):
    from app.triggers.engine import find_matching_triggers

    conn = connect()
    try:
        _seed_user(conn)
        _seed_trigger(conn, scope_path="projects/foo.md", tid="t_file")
        _seed_trigger(conn, scope_path="projects", tid="t_dir")
        _seed_trigger(conn, scope_path="other", tid="t_other")
    finally:
        conn.close()

    rows = find_matching_triggers("projects/foo.md")
    assert {r["id"] for r in rows} == {"t_file", "t_dir"}


def test_find_matching_triggers_skips_disabled_and_non_delta(tmp_db):
    from app.triggers.engine import find_matching_triggers

    conn = connect()
    try:
        _seed_user(conn)
        _seed_trigger(conn, scope_path="a.md", tid="t_on")
        _seed_trigger(conn, scope_path="a.md", tid="t_off", enabled=0)
        _seed_trigger(conn, scope_path="a.md", tid="t_sched", kind="schedule")
    finally:
        conn.close()

    rows = find_matching_triggers("a.md")
    assert {r["id"] for r in rows} == {"t_on"}


def test_find_matching_triggers_empty_when_nothing_seeded(tmp_db):
    from app.triggers.engine import find_matching_triggers

    assert find_matching_triggers("any/path.md") == []


def test_find_matching_triggers_includes_root_scope(tmp_db):
    """A trigger with scope_path='' (root) should fire on any doc update,
    including docs in nested directories and docs at the wiki root."""
    from app.triggers.engine import find_matching_triggers

    conn = connect()
    try:
        _seed_user(conn)
        _seed_trigger(conn, scope_path="", tid="t_root")
    finally:
        conn.close()

    assert {r["id"] for r in find_matching_triggers("a/b/c.md")} == {"t_root"}
    assert {r["id"] for r in find_matching_triggers("foo.md")} == {"t_root"}
