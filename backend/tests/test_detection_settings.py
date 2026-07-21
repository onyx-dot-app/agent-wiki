"""Auto Organize kill switch — org-wide settings + the freeze it enforces.

When disabled: no sweep (409), human/auto approve + reject frozen, executor
skips. Per-page policies are untouched.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.wiki import git as wiki_git
from app.wiki.automanage import executor, review, settings
from app.wiki.change_proposals import (
    ProposalCreatedVia,
    ProposalOp,
    approve as cp_approve,
    create as create_proposal,
    get as get_proposal,
)
from tests._auth import login_fastapi
from tests._seed import seed_user


# --------------------------------------------------------------------------- #
# settings repo                                                               #
# --------------------------------------------------------------------------- #


def test_default_is_enabled_off_schedule(tmp_db):
    s = settings.get()
    assert s.enabled is True
    assert s.schedule == "off"
    assert settings.is_enabled() is True


def test_update_toggles_and_validates(tmp_db):
    assert settings.update(enabled=False).enabled is False
    assert settings.is_enabled() is False
    assert settings.update(schedule="daily").schedule == "daily"
    # enabled untouched by a schedule-only patch
    assert settings.get().enabled is False
    with pytest.raises(ValueError):
        settings.update(schedule="hourly")


# --------------------------------------------------------------------------- #
# the freeze                                                                  #
# --------------------------------------------------------------------------- #


@pytest.fixture
def repo(tmp_repo):
    return tmp_repo


def _mk(folder: str) -> int:
    wiki_git.commit_file(f"{folder}/.gitkeep", "", "seed", author=None)
    return create_proposal(
        op=ProposalOp.DELETE_EMPTY_FOLDER,
        source_paths=[folder],
        target_paths=[],
        base_shas={folder: "0" * 40},
        summary=f"Delete empty folder “{folder}”",
        created_via=ProposalCreatedVia.SWEEP,
    )["id"]


def test_approve_and_reject_frozen_when_disabled(repo):
    uid = seed_user(uid="u1", email="u@x.com")
    pid = _mk("stale")
    settings.update(enabled=False)

    assert review.approve(pid, user_id=uid) is False
    assert review.reject(pid, user_id=uid) is False
    p = get_proposal(pid)
    assert p is not None and p["status"] == "pending"  # frozen, untouched


def test_executor_skips_when_disabled(repo):
    uid = seed_user(uid="u1", email="u@x.com")
    pid = _mk("stale")
    assert cp_approve(pid, user_id=uid)  # approved while enabled (no dispatch)
    settings.update(enabled=False)

    executor.execute(pid)

    p = get_proposal(pid)
    assert p is not None and p["status"] == "approved"  # not applied
    assert "stale/.gitkeep" in wiki_git.list_paths()  # folder untouched


# --------------------------------------------------------------------------- #
# admin API                                                                   #
# --------------------------------------------------------------------------- #


@pytest.fixture
def client(tmp_repo):
    return TestClient(create_app())


def test_settings_api_get_put_and_sweep_gate(client):
    uid = seed_user(uid="admin_1", email="a@x.com", is_admin=True)
    login_fastapi(client, uid)

    assert client.get("/api/detection/settings").json() == {
        "enabled": True,
        "schedule": "off",
        "updated_at": None,
    }
    put = client.put("/api/detection/settings", json={"enabled": False})
    assert put.status_code == 200 and put.json()["enabled"] is False

    # disabled → sweep 409
    assert client.post("/api/detection/sweep").status_code == 409
    # re-enable → sweep allowed
    client.put("/api/detection/settings", json={"enabled": True})
    assert client.post("/api/detection/sweep").status_code == 202


def test_settings_api_requires_admin_and_validates(client):
    seed_user(uid="admin_1", email="a@x.com", is_admin=True)  # first = admin
    uid = seed_user(uid="usr_2", email="u2@x.com", is_admin=False)
    login_fastapi(client, uid)
    assert client.get("/api/detection/settings").status_code == 403
    assert client.put("/api/detection/settings", json={"enabled": False}).status_code == 403

    login_fastapi(client, "admin_1")
    # The app translates request-validation errors to 400 (main.py handler).
    assert (
        client.put("/api/detection/settings", json={"schedule": "hourly"}).status_code
        == 400
    )
