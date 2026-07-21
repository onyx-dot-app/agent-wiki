"""Admin detection API — sweep trigger + run history, admin-gated.

The sweep runs on the detection queue; ``immediate_mode`` runs it inline so the
request completes synchronously and a ``detection_runs`` row is recorded. With
the real (2-day) age gate the fresh tmp repo yields no proposals — this asserts
the pipe (run recorded, admin gating), not detector output.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.tasks.queues import detection_queue
from tests._auth import login_fastapi
from tests._seed import seed_user


@pytest.fixture
def client(tmp_repo, tmp_config):
    from app.wiki import git as wiki_git

    wiki_git.commit_file("guide.md", "# Guide\n", "seed", author=None)
    return TestClient(create_app())


def test_sweep_requires_admin(client):
    uid = seed_user(uid="usr_x", email="x@x.com", is_admin=False)
    login_fastapi(client, uid)
    assert client.post("/api/automanage/sweep").status_code == 403


def test_admin_sweep_records_a_run(client):
    uid = seed_user(uid="admin_1", email="a@x.com", is_admin=True)
    login_fastapi(client, uid)

    with detection_queue.immediate_mode():
        resp = client.post("/api/automanage/sweep")
    assert resp.status_code == 202
    assert resp.json()["status"] == "queued"

    runs = client.get("/api/automanage/runs").json()["runs"]
    assert len(runs) == 1
    assert runs[0]["status"] == "completed"
    assert runs[0]["trigger"] == "sweep"
    assert runs[0]["triggered_by_user_id"] == uid
    assert runs[0]["paths_scanned"] >= 1


def test_runs_list_requires_admin(client):
    uid = seed_user(uid="usr_y", email="y@x.com", is_admin=False)
    login_fastapi(client, uid)
    assert client.get("/api/automanage/runs").status_code == 403
