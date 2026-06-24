"""Tests for the too-frequent-update guardrails (app/tasks/update_frequency.py)."""

from __future__ import annotations

import pytest

from app.app_settings import settings as app_settings
from app.auth import users as users_repo
from app.db import notifications as notifications_repo
from app.tasks.update_frequency import (
    NOTIF_AUTO_UPDATE_DISABLED,
    NOTIF_FREQUENT_UPDATES,
    _check_update_frequency_inline,
)
from app.wiki import acl
from app.wiki import git as wiki_git
from app.wiki import update_policy
from app.wiki import utils as wiki_utils

PATH = "team/page.md"


@pytest.fixture(autouse=True)
def _db(tmp_db: None, tmp_repo: None) -> None:
    return None


def _owned_page(*, commits: int) -> str:
    uid = users_repo.create(email="owner@x.com", password="hunter2-x", name="Owner")
    acl.set_owner(PATH, uid)
    for i in range(commits):
        wiki_git.commit_file(PATH, f"body {i}\n", "ingest", author=wiki_utils.INGEST_AUTHOR)
    return uid


def _counts(uid: str, notif_type: str) -> int:
    rows = notifications_repo.list_for_user(uid)["notifications"]
    return sum(1 for r in rows if r["notif_type"] == notif_type)


def test_below_threshold_no_notification() -> None:
    uid = _owned_page(commits=2)
    update_policy.set_policy(PATH, warn_update_threshold=5)
    _check_update_frequency_inline(PATH)
    assert _counts(uid, NOTIF_FREQUENT_UPDATES) == 0


def test_warns_owner_over_threshold() -> None:
    uid = _owned_page(commits=3)
    update_policy.set_policy(PATH, warn_update_threshold=2)
    _check_update_frequency_inline(PATH)
    assert _counts(uid, NOTIF_FREQUENT_UPDATES) == 1


def test_unowned_page_no_notification_no_error() -> None:
    # No owner stamped — must not raise and must not notify anyone.
    for i in range(3):
        wiki_git.commit_file(PATH, f"b{i}\n", "ingest", author=wiki_utils.INGEST_AUTHOR)
    update_policy.set_policy(PATH, warn_update_threshold=1)
    _check_update_frequency_inline(PATH)  # no exception
    assert acl.get_owner(PATH) is None


def test_cap_disables_and_notifies() -> None:
    uid = _owned_page(commits=2)
    app_settings.upsert(warn_update_threshold_default=10, auto_update_cap=2)
    _check_update_frequency_inline(PATH)
    assert update_policy.resolve_for_path(PATH).ingestion_auto_update_disabled is True
    assert _counts(uid, NOTIF_AUTO_UPDATE_DISABLED) == 1
    # The warning notification is not also sent when the cap fires.
    assert _counts(uid, NOTIF_FREQUENT_UPDATES) == 0


def test_warning_dedups_until_dismissed() -> None:
    uid = _owned_page(commits=3)
    update_policy.set_policy(PATH, warn_update_threshold=2)
    _check_update_frequency_inline(PATH)
    _check_update_frequency_inline(PATH)
    listing = notifications_repo.list_for_user(uid)
    assert listing["undismissed_count"] == 1
