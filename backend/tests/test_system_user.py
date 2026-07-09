"""The seeded AI system user (users.kind='system') and its guards.

Real DB per test; the seed migration runs via init_db's `alembic upgrade head`,
so the AI user row exists in every schema without test-side seeding.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.auth import users as users_repo
from app.auth.basic import authenticate
from app.main import create_app
from tests._auth import login_fastapi
from tests._seed import seed_user


def test_ai_user_is_seeded_by_migrations(tmp_db):
    row = users_repo.get_ai_user()
    assert row["id"] == users_repo.AI_USER_ID
    assert row["kind"] == "system"
    assert row["is_admin"] is False
    assert row["is_active"] is True
    assert row["password_hash"] is None


def test_first_human_signup_still_auto_admin(tmp_db):
    # The seeded AI user must not steal the first-signup promotion.
    uid = users_repo.create(email="first@x.com", password="hunter2-x")
    row = users_repo.get_by_id(uid)
    assert row is not None and row["is_admin"] is True
    # And only the first human — the second signup stays basic.
    uid2 = users_repo.create(email="second@x.com", password="hunter2-x")
    row2 = users_repo.get_by_id(uid2)
    assert row2 is not None and row2["is_admin"] is False


def test_system_user_cannot_authenticate(tmp_db):
    # No password hash: authenticate fails on the hash check.
    assert authenticate("wiki-ai@system.local", "anything") is None
    # Even with a hash forced onto the row, the kind guard refuses.
    from app.auth.passwords import hash_password
    from app.db.models import User
    from app.db.session import session

    with session() as s:
        u = s.get(User, users_repo.AI_USER_ID)
        assert u is not None
        u.password_hash = hash_password("anything")
    assert authenticate("wiki-ai@system.local", "anything") is None


def test_listings_exclude_system_by_default(tmp_db):
    seed_user(uid="u_h", email="human@x.com")
    all_default = users_repo.list_all()
    assert all(r["kind"] == "human" for r in all_default)
    all_with_system = users_repo.list_all(include_system=True)
    assert any(r["id"] == users_repo.AI_USER_ID for r in all_with_system)
    # Typeahead search never returns the AI user.
    assert all(r["id"] != users_repo.AI_USER_ID for r in users_repo.search("agent"))
    assert all(r["id"] != users_repo.AI_USER_ID for r in users_repo.search(""))


def test_get_many_includes_system_for_attribution(tmp_db):
    got = users_repo.get_many([users_repo.AI_USER_ID])
    assert users_repo.AI_USER_ID in got
    assert got[users_repo.AI_USER_ID]["name"] == "Agent Wiki AI"


def test_admin_cannot_modify_or_delete_system_user(tmp_db, tmp_repo):
    seed_user(uid="u_admin", email="admin@x.com", is_admin=True)
    client = TestClient(create_app())
    login_fastapi(client, "u_admin")

    r = client.patch(
        f"/api/admin/users/{users_repo.AI_USER_ID}", json={"is_admin": True}
    )
    assert r.status_code == 400
    assert "system user" in r.json()["error"]

    r = client.delete(f"/api/admin/users/{users_repo.AI_USER_ID}")
    assert r.status_code == 400
    assert "system user" in r.json()["error"]

    # Untouched.
    row = users_repo.get_ai_user()
    assert row["is_admin"] is False and row["is_active"] is True


def test_kind_check_constraint_rejects_unknown_values(tmp_db):
    import pytest
    from sqlalchemy.exc import IntegrityError

    from app.db.models import User
    from app.db.session import session

    with pytest.raises(IntegrityError):
        with session() as s:
            s.add(
                User(
                    id="u_bad_kind",
                    email="bad@x.com",
                    password_hash="x",
                    kind="robot",
                )
            )
