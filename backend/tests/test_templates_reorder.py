"""Tests for ``app.wiki.templates.reorder`` and its mismatch contract."""
from __future__ import annotations

import pytest


def _make_three(tmp_db):
    from app.wiki import templates as repo

    a = repo.create(name="A", body="a", description=None, system_prompt=None, created_by_user_id=None)
    b = repo.create(name="B", body="b", description=None, system_prompt=None, created_by_user_id=None)
    c = repo.create(name="C", body="c", description=None, system_prompt=None, created_by_user_id=None)
    return a, b, c


def test_create_assigns_increasing_sort_order(tmp_db):
    a, b, c = _make_three(tmp_db)
    # Created in order A, B, C — sort_order should march 0, 1, 2.
    assert (a["sort_order"], b["sort_order"], c["sort_order"]) == (0, 1, 2)


def test_list_all_returns_sort_order(tmp_db):
    from app.wiki import templates as repo

    a, b, c = _make_three(tmp_db)
    rows = repo.list_all()
    assert [r["id"] for r in rows] == [a["id"], b["id"], c["id"]]


def test_reorder_writes_new_indices(tmp_db):
    from app.wiki import templates as repo

    a, b, c = _make_three(tmp_db)
    repo.reorder([c["id"], a["id"], b["id"]])

    rows = repo.list_all()
    assert [r["id"] for r in rows] == [c["id"], a["id"], b["id"]]
    assert [r["sort_order"] for r in rows] == [0, 1, 2]


def test_reorder_rejects_missing_ids(tmp_db):
    from app.wiki import templates as repo

    a, b, _c = _make_three(tmp_db)
    with pytest.raises(repo.ReorderMismatch):
        repo.reorder([a["id"], b["id"]])


def test_reorder_rejects_unknown_ids(tmp_db):
    from app.wiki import templates as repo

    a, b, c = _make_three(tmp_db)
    with pytest.raises(repo.ReorderMismatch):
        repo.reorder([a["id"], b["id"], c["id"], "ghost-id"])


def test_reorder_rejects_duplicates(tmp_db):
    from app.wiki import templates as repo

    a, b, _c = _make_three(tmp_db)
    with pytest.raises(repo.ReorderMismatch):
        repo.reorder([a["id"], b["id"], a["id"]])
