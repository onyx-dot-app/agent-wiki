"""Approve / reject endpoints for pending cleanup proposals.

Approve requires *write* on every path the proposal touches (the approver
becomes the acting user) and enqueues execution; `immediate_mode` runs it inline
so the folder is actually trashed within the request.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.tasks.queues import automanage_nearline_queue
from app.wiki import acl
from app.wiki import git as wiki_git
from app.wiki.change_proposals import (
    ProposalCreatedVia,
    ProposalOp,
    create as create_proposal,
    get as get_proposal,
)
from tests._auth import login_fastapi
from tests._seed import seed_user


@pytest.fixture
def client(tmp_repo):
    return TestClient(create_app())


def _mk(folder: str) -> int:
    meta = wiki_git.last_commit_meta_for_path(folder)
    base = meta[0] if meta else "0" * 40
    return create_proposal(
        op=ProposalOp.DELETE_EMPTY_FOLDER,
        source_paths=[folder],
        target_paths=[],
        base_shas={folder: base},
        summary=f"Delete empty folder “{folder}”",
        created_via=ProposalCreatedVia.SWEEP,
    )["id"]


def _status(pid: int) -> str:
    p = get_proposal(pid)
    assert p is not None
    return p["status"]


def test_approve_executes_and_applies(client):
    uid = seed_user(uid="u1", email="u@x.com", is_admin=True)
    login_fastapi(client, uid)
    wiki_git.commit_file("stale/.gitkeep", "", "create", author=None)
    pid = _mk("stale")

    with automanage_nearline_queue.immediate_mode():
        r = client.post(f"/api/automanage/proposals/{pid}/approve")
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert _status(pid) == "applied"
    assert "stale/.gitkeep" not in wiki_git.list_paths()  # trashed


def test_approve_requires_write(client):
    owner = seed_user(uid="owner", email="o@x.com", is_admin=True)  # first = admin
    other = seed_user(uid="other", email="ot@x.com", is_admin=False)
    wiki_git.commit_file("restricted/.gitkeep", "", "create", author=None)
    pid = _mk("restricted")
    acl.set_owner("restricted", owner)  # owner + admins only

    login_fastapi(client, other)
    assert client.post(f"/api/automanage/proposals/{pid}/approve").status_code == 403
    assert _status(pid) == "pending"  # untouched


def test_reject_marks_rejected_and_leaves_folder(client):
    uid = seed_user(uid="u1", email="u@x.com", is_admin=True)
    login_fastapi(client, uid)
    wiki_git.commit_file("junk/.gitkeep", "", "create", author=None)
    pid = _mk("junk")

    r = client.post(f"/api/automanage/proposals/{pid}/reject")
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert _status(pid) == "rejected"
    assert "junk/.gitkeep" in wiki_git.list_paths()  # untouched


def test_approve_twice_conflicts(client):
    uid = seed_user(uid="u1", email="u@x.com", is_admin=True)
    login_fastapi(client, uid)
    wiki_git.commit_file("dup/.gitkeep", "", "create", author=None)
    pid = _mk("dup")

    with automanage_nearline_queue.immediate_mode():
        assert client.post(f"/api/automanage/proposals/{pid}/approve").status_code == 200
        # already applied → no longer pending → 409
        assert client.post(f"/api/automanage/proposals/{pid}/approve").status_code == 409
