"""Trash auto-purge: `git rm` past-retention entries + drop parked rows."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.main import create_app
from app.tasks import trash_purge
from app.tasks.queues import documents_queue
from app.wiki import acl, trash

from tests._auth import login_fastapi
from tests._seed import seed_user


def _client(user_id: str) -> TestClient:
    client = TestClient(create_app())
    login_fastapi(client, user_id)
    return client


def test_purge_removes_item_and_parked_rows(tmp_repo):
    user = seed_user()
    client = _client(user)
    client.put("/api/wiki/file", json={"path": "proj/a.md", "body": "# A\n"})
    tid = client.delete("/api/wiki/file?path=proj").json()["trash_id"]

    # Trashing parked the page's ACL rows at the trash location.
    trash_loc = trash.trash_location(tid, "proj/a.md")
    parked = [
        g for g in acl.list_for_path(trash_loc) if g["resource_path"].startswith(".trash/")
    ]
    assert parked  # some grant(s) parked under .trash/

    assert trash.purge(tid) is True

    # Gone from git + the Trash view, and the parked ACL rows are cleared.
    assert trash.entry_for(tid) is None
    assert client.get("/api/wiki/trash").json()["items"] == []
    assert not [
        g for g in acl.list_for_path(trash_loc) if g["resource_path"].startswith(".trash/")
    ]


def test_purge_noop_for_unknown_id(tmp_repo):
    seed_user()
    assert trash.purge("deadbeef0000") is False


def test_trashed_before_cutoff():
    cutoff = datetime(2026, 1, 15, tzinfo=timezone.utc)
    assert trash_purge._trashed_before("2026-01-14T00:00:00+00:00", cutoff)  # older
    assert not trash_purge._trashed_before("2026-01-16T00:00:00+00:00", cutoff)  # newer
    assert not trash_purge._trashed_before("", cutoff)  # missing → kept
    assert not trash_purge._trashed_before("garbage", cutoff)  # unparseable → kept
    # Naive timestamps are treated as UTC.
    assert trash_purge._trashed_before("2026-01-14T00:00:00", cutoff)


def test_sweep_purges_expired_entries(tmp_repo, monkeypatch):
    # Force every entry "expired" so the sweep's orchestration (iterate →
    # purge) is exercised without mocking wall-clock time.
    user = seed_user()
    client = _client(user)
    client.put("/api/wiki/file", json={"path": "old.md", "body": "# old\n"})
    old_tid = client.delete("/api/wiki/file?path=old.md").json()["trash_id"]

    monkeypatch.setattr(trash_purge, "_trashed_before", lambda ta, cutoff: True)
    with documents_queue.immediate_mode():
        trash_purge.purge_expired_trash()
    assert trash.entry_for(old_tid) is None  # purged


def test_sweep_keeps_recent_entries(tmp_repo):
    # Real time, 30-day retention, freshly trashed → not expired → kept.
    user = seed_user()
    client = _client(user)
    client.put("/api/wiki/file", json={"path": "recent.md", "body": "# r\n"})
    tid = client.delete("/api/wiki/file?path=recent.md").json()["trash_id"]

    with documents_queue.immediate_mode():
        trash_purge.purge_expired_trash()
    assert trash.entry_for(tid) is not None


def test_sweep_disabled_when_retention_not_positive(tmp_repo, monkeypatch):
    user = seed_user()
    client = _client(user)
    client.put("/api/wiki/file", json={"path": "keep.md", "body": "# k\n"})
    tid = client.delete("/api/wiki/file?path=keep.md").json()["trash_id"]

    monkeypatch.setattr(trash_purge, "TRASH_RETENTION_DAYS", 0)
    with documents_queue.immediate_mode():
        trash_purge.purge_expired_trash()
    assert trash.entry_for(tid) is not None  # 0 disables → nothing purged
