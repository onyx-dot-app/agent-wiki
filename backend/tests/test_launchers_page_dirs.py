"""Per-(user, machine, page) working-dir binding."""

from __future__ import annotations

from app.db.session import init_db
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
