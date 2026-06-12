"""`find_user` chat tool — resolves a person to their user id for @mentions.

DB-backed (`tmp_db`): the tool searches real `users` rows via `users_repo`.
"""
from __future__ import annotations

from app.llm.agents.tools.find_user import handle
from tests._seed import seed_user

_DOC_USERS = [
    ("u_nik", "nik@onyx.app", "Nik Garza"),
    ("u_dane", "dane@onyx.app", "Dane Liu"),
    ("u_bo", "bo@onyx.app", "Bo Yang"),
]


def _seed_all() -> None:
    for uid, email, name in _DOC_USERS:
        seed_user(uid=uid, email=email, name=name)


def test_finds_by_name(tmp_db):
    _seed_all()
    out = handle({"query": "Nik"})
    assert out["users"] == [{"id": "u_nik", "name": "Nik Garza", "email": "nik@onyx.app"}]


def test_finds_by_email_fragment(tmp_db):
    _seed_all()
    out = handle({"query": "bo@"})
    assert [u["id"] for u in out["users"]] == ["u_bo"]


def test_returns_multiple_matches(tmp_db):
    _seed_all()
    # Every seeded user is @onyx.app, so this matches all three.
    out = handle({"query": "onyx.app"})
    assert {u["id"] for u in out["users"]} == {"u_nik", "u_dane", "u_bo"}


def test_no_match_returns_empty(tmp_db):
    _seed_all()
    out = handle({"query": "nobody-here"})
    assert out["users"] == []


def test_limit_is_clamped(tmp_db):
    _seed_all()
    out = handle({"query": "onyx.app", "limit": 1})
    assert len(out["users"]) == 1


def test_blank_query_errors(tmp_db):
    assert "error" in handle({"query": "   "})
    assert "error" in handle({})
