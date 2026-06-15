"""Page-scoped live state dies with the page on delete.

``after_doc_delete`` drops the page's ACL/owner, comments (orphaned as
tombstones), agent-activity, draft, and working-dir state. The delete endpoint
fans this out per nested page so a *folder* delete cleans up too — important for
the no-TTL pointers (drafts, working-dirs), which would otherwise mis-bind a
page later recreated at the same path.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import page_dirs
from app.db.models import DocumentTemplate
from app.db.session import session
from app.main import create_app
from app.wiki import agent_activity, drafts, notify, update_policy
from app.wiki import git as wiki_git

from tests._auth import login_fastapi
from tests._seed import seed_user


def _seed_template(tid: str = "tmpl_1") -> str:
    with session() as s:
        s.add(DocumentTemplate(id=tid, name=f"name-{tid}", body="# T\n"))
    return tid


def test_after_doc_delete_drops_page_scoped_state(tmp_repo):
    user = seed_user()
    tid = _seed_template()
    sha = wiki_git.commit_file("a.md", "# A\n", "seed", author=None)
    agent_activity.upsert_activity(
        user_id=user, agent_name=None, doc_path="a.md", activity="wrote", description=None
    )
    drafts.create(
        path="a.md", template_id=tid, template_body_snapshot="# T\n", created_by_user_id=user
    )
    page_dirs.set_for_page(
        user_id=user, machine_id="m1", wiki_path="a.md", working_dir="/tmp/x"
    )

    notify.after_doc_delete("a.md", sha, actor=None)

    assert agent_activity.list_for_doc("a.md") == []
    assert drafts.get("a.md") is None
    assert page_dirs.get_for_page(user_id=user, machine_id="m1", wiki_path="a.md") is None


def test_after_doc_delete_drops_update_policy(tmp_repo):
    update_policy.set_policy("a.md", ingestion_auto_update_disabled=True)
    sha = wiki_git.commit_file("a.md", "# A\n", "seed", author=None)

    notify.after_doc_delete("a.md", sha, actor=None)

    assert update_policy.get("a.md") is None


def test_delete_folder_fans_out_to_nested_pages(tmp_repo):
    # Deleting a folder must clean every nested page's state, not just the
    # folder path (which isn't a .md and would otherwise no-op the hook).
    admin = seed_user(uid="u_admin", email="admin@x.com", is_admin=True)
    tid = _seed_template()
    nested = ("proj/a.md", "proj/b.md")
    for p in nested:
        wiki_git.commit_file(p, "# Page\n", f"seed {p}", author=None)
        page_dirs.set_for_page(user_id=admin, machine_id="m1", wiki_path=p, working_dir="/tmp/x")
        drafts.create(
            path=p, template_id=tid, template_body_snapshot="# T\n", created_by_user_id=admin
        )
        agent_activity.upsert_activity(
            user_id=admin, agent_name=p, doc_path=p, activity="wrote", description=None
        )

    client = TestClient(create_app())
    login_fastapi(client, admin)
    resp = client.delete("/api/wiki/file?path=proj")
    assert resp.status_code == 200

    # Every nested page's live state is cleared — not just the working-dir one.
    for p in nested:
        assert page_dirs.get_for_page(user_id=admin, machine_id="m1", wiki_path=p) is None
        assert drafts.get(p) is None
        assert agent_activity.list_for_doc(p) == []
