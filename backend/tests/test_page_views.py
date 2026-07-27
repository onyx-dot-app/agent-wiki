"""Last-viewed tracking — the read-side signal for staleness detection.

A view = a human page open (HTTP HEAD read) or an agent read over chat/MCP
(``read_doc``/``read_page``, HEAD only). Writes are coarse on purpose
(throttle window); moves re-key history; deletes drop it.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update as sa_update

from app.db.models import PageView
from app.db.session import session
from app.llm.agents.tools import dispatch as registry_dispatch
from app.main import create_app
from app.models.wiki import PathMove
from app.tasks.queues import lightweight_maintenance_queue
from app.wiki import git as wiki_git
from app.wiki import page_views
from tests._auth import login_fastapi
from tests._seed import seed_user


@pytest.fixture(autouse=True)
def _fresh_throttle():
    page_views._last_enqueued.clear()
    yield
    page_views._last_enqueued.clear()


def _set_stored(path: str, dt: datetime) -> None:
    with session() as s:
        s.execute(
            sa_update(PageView)
            .where(PageView.path == path)
            .values(last_viewed_at=dt.strftime("%Y-%m-%d %H:%M:%S"))
        )


def test_touch_upserts_and_throttles(tmp_repo):
    page_views.touch("a/b.md")
    first = page_views.last_viewed(["a/b.md"])["a/b.md"]

    # Within the window a second touch leaves the row alone.
    page_views.touch("a/b.md")
    assert page_views.last_viewed(["a/b.md"])["a/b.md"] == first

    # Past the window it refreshes.
    _set_stored("a/b.md", datetime.now(UTC) - page_views.THROTTLE * 2)
    old = page_views.last_viewed(["a/b.md"])["a/b.md"]
    page_views.touch("a/b.md")
    assert page_views.last_viewed(["a/b.md"])["a/b.md"] > old


def test_last_viewed_missing_paths_are_absent(tmp_repo):
    page_views.touch("x.md")
    out = page_views.last_viewed(["x.md", "never-seen.md"])
    assert "x.md" in out and "never-seen.md" not in out


def test_should_enqueue_gates_per_window(tmp_repo):
    assert page_views.should_enqueue("p.md") is True
    assert page_views.should_enqueue("p.md") is False  # same window
    assert page_views.should_enqueue("q.md") is True  # other page unaffected


def test_move_rekeys_history(tmp_repo):
    page_views.touch("old/name.md")
    stamp = page_views.last_viewed(["old/name.md"])["old/name.md"]
    page_views.on_path_moved([PathMove(old="old/name.md", new="new/name.md")])
    out = page_views.last_viewed(["old/name.md", "new/name.md"])
    assert out == {"new/name.md": stamp}


def test_delete_drops_history(tmp_repo):
    page_views.touch("gone.md")
    page_views.on_page_deleted("gone.md")
    assert page_views.last_viewed(["gone.md"]) == {}


def test_agent_read_records_a_view(tmp_repo):
    wiki_git.commit_file("team/page.md", "# P\n\nbody\n", "seed", author=None)
    with lightweight_maintenance_queue.immediate_mode():
        out = registry_dispatch("read_doc", {"path": "team/page.md"})
    assert "error" not in out
    assert "team/page.md" in page_views.last_viewed(["team/page.md"])


def test_historical_agent_read_is_not_a_view(tmp_repo):
    sha = wiki_git.commit_file("team/page.md", "# P\n\nbody\n", "seed", author=None)
    with lightweight_maintenance_queue.immediate_mode():
        out = registry_dispatch("read_doc", {"path": "team/page.md", "sha": sha})
    assert "error" not in out
    assert page_views.last_viewed(["team/page.md"]) == {}


def test_http_page_open_records_a_view(tmp_repo):
    wiki_git.commit_file("team/page.md", "# P\n\nbody\n", "seed", author=None)
    client = TestClient(create_app())
    uid = seed_user(uid="u1", email="u@x.com")
    login_fastapi(client, uid)
    with lightweight_maintenance_queue.immediate_mode():
        res = client.get("/api/wiki/file", params={"path": "team/page.md"})
    assert res.status_code == 200
    assert "team/page.md" in page_views.last_viewed(["team/page.md"])


def test_historical_http_read_is_not_a_view(tmp_repo):
    sha = wiki_git.commit_file("team/page.md", "# P\n\nbody\n", "seed", author=None)
    client = TestClient(create_app())
    uid = seed_user(uid="u1", email="u@x.com")
    login_fastapi(client, uid)
    with lightweight_maintenance_queue.immediate_mode():
        res = client.get(
            "/api/wiki/file", params={"path": "team/page.md", "ref": sha}
        )
    assert res.status_code == 200
    assert page_views.last_viewed(["team/page.md"]) == {}
