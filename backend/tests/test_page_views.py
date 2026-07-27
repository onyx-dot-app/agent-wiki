"""Last-viewed tracking — the read-side signal for staleness detection.

A view = a human page open (HTTP HEAD read) or an agent read over chat/MCP
(``read_doc``/``read_page``, HEAD only). Rows are keyed by stable doc id, so
history survives moves and trash/restore with no re-keying, and a recreated
page (new id) inherits nothing. Writes are coarse on purpose.
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
from app.wiki import doc_ids
from app.wiki import git as wiki_git
from app.wiki import page_views
from tests._auth import login_fastapi
from tests._seed import seed_user


@pytest.fixture(autouse=True)
def _fresh_throttle():
    page_views.reset_for_tests()
    yield
    page_views.reset_for_tests()


def _seed_page(path: str) -> None:
    wiki_git.commit_file(path, "# P\n\nbody\n", "seed", author=None)


def _set_stored(path: str, dt: datetime) -> None:
    doc_id = doc_ids.id_for_path(path)
    with session() as s:
        s.execute(
            sa_update(PageView)
            .where(PageView.doc_id == doc_id)
            .values(last_viewed_at=dt.strftime("%Y-%m-%d %H:%M:%S"))
        )


def test_touch_upserts_and_throttles(tmp_repo):
    _seed_page("a/b.md")
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
    _seed_page("x.md")
    page_views.touch("x.md")
    out = page_views.last_viewed(["x.md", "never-seen.md"])
    assert "x.md" in out and "never-seen.md" not in out


def test_should_enqueue_gates_per_window(tmp_repo):
    assert page_views.should_enqueue("p.md") is True
    assert page_views.should_enqueue("p.md") is False  # same window
    assert page_views.should_enqueue("q.md") is True  # other page unaffected


def test_history_follows_a_move_with_no_rekeying(tmp_repo):
    """Rows are id-keyed: after the id re-binds to the new path, the view
    history is simply there — no page_views lifecycle hook exists at all."""
    _seed_page("old/name.md")
    page_views.touch("old/name.md")
    stamp = page_views.last_viewed(["old/name.md"])["old/name.md"]

    _sha, moves = wiki_git.move_path("old/name.md", "new/name.md", "mv", author=None)
    doc_ids.on_path_moved(moves, root_move=PathMove(old="old/name.md", new="new/name.md"))

    out = page_views.last_viewed(["old/name.md", "new/name.md"])
    assert out == {"new/name.md": stamp}


def test_recreated_page_inherits_no_history(tmp_repo):
    """Delete tombstones the id; a page recreated at the same path gets a
    fresh id — and therefore no inherited view history."""
    _seed_page("gone.md")
    page_views.touch("gone.md")
    wiki_git.delete_path("gone.md", "rm", author=None)
    doc_ids.on_deleted("gone.md")

    _seed_page("gone.md")  # new document, new id
    doc_ids.mint_for_page("gone.md")
    assert page_views.last_viewed(["gone.md"]) == {}


def test_agent_read_records_a_view(tmp_repo):
    _seed_page("team/page.md")
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
    _seed_page("team/page.md")
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
