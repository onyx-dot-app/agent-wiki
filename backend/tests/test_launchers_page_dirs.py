"""Per-(user, machine, page) working-dir binding."""

from __future__ import annotations

from app.db.models import PageWorkingDir
from app.db.session import init_db, session
from app.db import page_dirs

from tests._seed import seed_user


def test_set_then_get(tmp_config):
    init_db()
    uid = seed_user()
    page_dirs.set_for_page(
        user_id=uid,
        machine_id="m_a",
        wiki_path="docs/x.md",
        working_dir="/home/u/proj",
    )
    assert (
        page_dirs.get_for_page(user_id=uid, machine_id="m_a", wiki_path="docs/x.md")
        == "/home/u/proj"
    )


def test_get_returns_none_when_unset(tmp_config):
    init_db()
    uid = seed_user()
    assert page_dirs.get_for_page(user_id=uid, machine_id="m", wiki_path="x.md") is None


def test_set_is_upsert(tmp_config):
    init_db()
    uid = seed_user()
    page_dirs.set_for_page(
        user_id=uid,
        machine_id="m",
        wiki_path="x.md",
        working_dir="/a",
    )
    page_dirs.set_for_page(
        user_id=uid,
        machine_id="m",
        wiki_path="x.md",
        working_dir="/b",
    )
    assert page_dirs.get_for_page(user_id=uid, machine_id="m", wiki_path="x.md") == "/b"


def test_per_machine_isolation(tmp_config):
    init_db()
    uid = seed_user()
    page_dirs.set_for_page(
        user_id=uid,
        machine_id="m_laptop",
        wiki_path="x.md",
        working_dir="/home/u/proj",
    )
    page_dirs.set_for_page(
        user_id=uid,
        machine_id="m_desktop",
        wiki_path="x.md",
        working_dir="/work/proj",
    )
    assert (
        page_dirs.get_for_page(user_id=uid, machine_id="m_laptop", wiki_path="x.md")
        == "/home/u/proj"
    )
    assert (
        page_dirs.get_for_page(user_id=uid, machine_id="m_desktop", wiki_path="x.md")
        == "/work/proj"
    )


def test_per_user_isolation(tmp_config):
    init_db()
    a = seed_user("usr_a", email="a@x.com")
    b = seed_user("usr_b", email="b@x.com")
    page_dirs.set_for_page(
        user_id=a,
        machine_id="m",
        wiki_path="x.md",
        working_dir="/a",
    )
    page_dirs.set_for_page(
        user_id=b,
        machine_id="m",
        wiki_path="x.md",
        working_dir="/b",
    )
    assert page_dirs.get_for_page(user_id=a, machine_id="m", wiki_path="x.md") == "/a"
    assert page_dirs.get_for_page(user_id=b, machine_id="m", wiki_path="x.md") == "/b"


def test_clear(tmp_config):
    init_db()
    uid = seed_user()
    page_dirs.set_for_page(
        user_id=uid,
        machine_id="m",
        wiki_path="x.md",
        working_dir="/p",
    )
    page_dirs.clear(user_id=uid, machine_id="m", wiki_path="x.md")
    assert page_dirs.get_for_page(user_id=uid, machine_id="m", wiki_path="x.md") is None


def test_set_updates_updated_at(tmp_config, monkeypatch):
    init_db()
    uid = seed_user()

    monkeypatch.setattr(page_dirs, "_now_iso", lambda: "2025-01-01 00:00:00")
    page_dirs.set_for_page(
        user_id=uid,
        machine_id="m",
        wiki_path="x.md",
        working_dir="/a",
    )
    with session() as s:
        row = s.get(PageWorkingDir, (uid, "m", "x.md"))
        assert row is not None
        assert row.updated_at == "2025-01-01 00:00:00"

    monkeypatch.setattr(page_dirs, "_now_iso", lambda: "2025-01-02 00:00:00")
    page_dirs.set_for_page(
        user_id=uid,
        machine_id="m",
        wiki_path="x.md",
        working_dir="/b",
    )
    with session() as s:
        row = s.get(PageWorkingDir, (uid, "m", "x.md"))
        assert row is not None
        assert row.working_dir == "/b"
        assert row.updated_at == "2025-01-02 00:00:00"
