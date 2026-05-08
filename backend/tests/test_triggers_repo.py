"""CRUD tests for ``app/triggers/repo.py``."""
from __future__ import annotations

import pytest

from app.db.sqlite import connect


def _seed_user(uid: str = "usr_1", email: str = "a@b.com") -> str:
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO users(id, email, password_hash, is_admin) VALUES (?, ?, ?, 0)",
            (uid, email, "x"),
        )
    finally:
        conn.close()
    return uid


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

    uid = _seed_user()
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
    assert t["destination"] is None

    fetched = repo.get(t["id"])
    assert fetched == t


def test_list_for_owner_filters_by_owner(tmp_repo):
    from app.triggers import repo

    a = _seed_user("usr_a", "a@x.com")
    b = _seed_user("usr_b", "b@x.com")
    _create(repo, owner_user_id=a, scope_path="x.md", nl_description="x")
    _create(repo, owner_user_id=a, scope_path="y.md", nl_description="y")
    _create(repo, owner_user_id=b, scope_path="z.md", nl_description="z")

    a_rows = repo.list_for_owner(a)
    assert {r["scope_path"] for r in a_rows} == {"x.md", "y.md"}
    b_rows = repo.list_for_owner(b)
    assert {r["scope_path"] for r in b_rows} == {"z.md"}


def test_update_partial(tmp_repo):
    from app.triggers import repo

    uid = _seed_user()
    t = _create(
        repo,
        owner_user_id=uid,
        scope_path="a.md",
        nl_description="original",
        message="m",
    )

    updated = repo.update(t["id"], nl_description="changed")
    assert updated["nl_description"] == "changed"
    assert updated["scope_path"] == "a.md"
    assert updated["enabled"] is True
    assert updated["message"] == "m"

    re_msg = repo.update(t["id"], message="m2")
    assert re_msg["message"] == "m2"
    assert re_msg["nl_description"] == "changed"

    toggled = repo.update(t["id"], enabled=False)
    assert toggled["enabled"] is False
    assert toggled["message"] == "m2"


def test_update_with_no_fields_returns_current(tmp_repo):
    from app.triggers import repo

    uid = _seed_user()
    t = _create(repo, owner_user_id=uid, scope_path="a.md", nl_description="x")
    out = repo.update(t["id"])
    assert out == t


def test_delete(tmp_repo):
    from app.triggers import repo

    uid = _seed_user()
    t = _create(repo, owner_user_id=uid, scope_path="a.md", nl_description="x")
    assert repo.delete(t["id"]) is True
    assert repo.get(t["id"]) is None
    assert repo.delete(t["id"]) is False  # second call no-op


def test_create_rejects_unsupported_kind(tmp_repo):
    from app.triggers import repo

    uid = _seed_user()
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

    uid = _seed_user()
    with pytest.raises(ValueError, match="message"):
        repo.create(
            owner_user_id=uid,
            scope_path="a.md",
            nl_description="x",
            message="",
        )


def test_create_rejects_unsupported_destination(tmp_repo):
    from app.triggers import repo

    uid = _seed_user()
    with pytest.raises(ValueError, match="destination"):
        repo.create(
            owner_user_id=uid,
            scope_path="a.md",
            nl_description="x",
            message="m",
            destination="https://example.com/hook",
        )


def test_update_rejects_empty_message(tmp_repo):
    from app.triggers import repo

    uid = _seed_user()
    t = _create(repo, owner_user_id=uid, scope_path="a.md", nl_description="x")
    with pytest.raises(ValueError, match="message"):
        repo.update(t["id"], message="")
