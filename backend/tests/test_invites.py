"""Invited-email repo + user active/status counts."""

from __future__ import annotations

from app.auth import invites
from app.auth import users as users_repo
from app.auth.basic import authenticate
from tests._seed import seed_user


def test_invite_add_list_remove(tmp_db):
    added = invites.add(["A@x.com", "b@x.com", "a@x.com"], invited_by_user_id=None)
    # Deduped + lowercased.
    assert sorted(added) == ["a@x.com", "b@x.com"]
    assert invites.list_emails() == ["a@x.com", "b@x.com"]
    assert invites.count() == 2
    assert invites.is_invited("A@x.com") is True

    invites.remove("a@x.com")
    assert invites.list_emails() == ["b@x.com"]


def test_invite_skips_existing_account_and_dupes(tmp_db):
    seed_user(uid="u1", email="taken@x.com")
    # Already an account → not added; second add of same email → no dupe.
    assert invites.add(["taken@x.com", "new@x.com"], invited_by_user_id=None) == [
        "new@x.com"
    ]
    assert invites.add(["new@x.com"], invited_by_user_id=None) == []
    assert invites.list_emails() == ["new@x.com"]


def test_invited_email_dropped_from_list_once_signed_up(tmp_db):
    invites.add(["future@x.com"], invited_by_user_id=None)
    assert invites.list_emails() == ["future@x.com"]
    # They sign up — list_emails filters out emails that now have an account
    # even before the invite row is consumed.
    seed_user(uid="u_future", email="future@x.com")
    assert invites.list_emails() == []
    assert invites.count() == 0


def test_set_active_and_status_counts(tmp_db):
    a = seed_user(uid="u_a", email="a@x.com")
    seed_user(uid="u_b", email="b@x.com")
    assert users_repo.status_counts() == {"active": 2, "inactive": 0}

    users_repo.set_active(a, False)
    assert users_repo.status_counts() == {"active": 1, "inactive": 1}
    row = users_repo.get_by_id(a)
    assert row is not None
    assert row["is_active"] is False


def test_admin_count_excludes_inactive(tmp_db):
    seed_user(uid="u_a", email="a@x.com", is_admin=True)
    b = seed_user(uid="u_b", email="b@x.com", is_admin=True)
    assert users_repo.admin_count() == 2

    # A deactivated admin can't log in, so the last-admin guards must not
    # count them — otherwise the sole active admin could be removed.
    users_repo.set_active(b, False)
    assert users_repo.admin_count() == 1


def test_deactivated_user_cannot_authenticate(tmp_db):
    uid = users_repo.create("a@x.com", "secret-pw")
    assert authenticate("a@x.com", "secret-pw") is not None

    users_repo.set_active(uid, False)
    assert authenticate("a@x.com", "secret-pw") is None
