"""Live path-keyed caches follow a page rename/move via ``after_path_move``.

The hook re-points every Postgres row that is a *live pointer* to the page
(ACL/owner and comments are covered elsewhere; here: the agent-activity rail,
template-draft state, and per-(user, machine) working-dir bindings) and
reconverges the trigger cache from disk. Point-in-time records are left alone.
"""
from __future__ import annotations

from app.db import page_dirs
from app.db.models import DocumentTemplate
from app.db.session import session
from app.wiki import agent_activity, drafts, notify, update_policy
from app.wiki import git as wiki_git

from tests._seed import seed_user


def _seed_template(tid: str = "tmpl_1") -> str:
    with session() as s:
        s.add(DocumentTemplate(id=tid, name=f"name-{tid}", body="# T\n"))
    return tid


def test_after_path_move_repoints_agent_activity(tmp_repo):
    user = seed_user()
    sha = wiki_git.commit_file("a.md", "body\n", "seed", author=None)
    agent_activity.upsert_activity(
        user_id=user, agent_name=None, doc_path="a.md", activity="wrote", description=None
    )

    notify.after_path_move([("a.md", "b.md")], sha, actor=None)

    assert agent_activity.list_for_doc("a.md") == []
    assert len(agent_activity.list_for_doc("b.md")) == 1


def test_after_path_move_repoints_drafts(tmp_repo):
    user = seed_user()
    tid = _seed_template()
    sha = wiki_git.commit_file("a.md", "body\n", "seed", author=None)
    drafts.create(
        path="a.md", template_id=tid, template_body_snapshot="# T\n", created_by_user_id=user
    )

    notify.after_path_move([("a.md", "b.md")], sha, actor=None)

    assert drafts.get("a.md") is None
    moved = drafts.get("b.md")
    assert moved is not None and moved["template_id"] == tid


def test_after_path_move_repoints_page_working_dirs(tmp_repo):
    user = seed_user()
    sha = wiki_git.commit_file("a.md", "body\n", "seed", author=None)
    page_dirs.set_for_page(
        user_id=user, machine_id="m1", wiki_path="a.md", working_dir="/tmp/checkout"
    )

    notify.after_path_move([("a.md", "b.md")], sha, actor=None)

    assert page_dirs.get_for_page(user_id=user, machine_id="m1", wiki_path="a.md") is None
    assert (
        page_dirs.get_for_page(user_id=user, machine_id="m1", wiki_path="b.md") == "/tmp/checkout"
    )


def test_after_path_move_out_of_md_space_clears_activity_and_drafts(tmp_repo):
    # A page renamed out of .md-space has no new doc to carry these onto, so
    # they're dropped rather than stranded. (The move API blocks this, but the
    # hook handles it defensively, mirroring how comments are orphaned.)
    user = seed_user()
    tid = _seed_template()
    sha = wiki_git.commit_file("a.md", "body\n", "seed", author=None)
    agent_activity.upsert_activity(
        user_id=user, agent_name=None, doc_path="a.md", activity="wrote", description=None
    )
    drafts.create(
        path="a.md", template_id=tid, template_body_snapshot="# T\n", created_by_user_id=user
    )
    page_dirs.set_for_page(
        user_id=user, machine_id="m1", wiki_path="a.md", working_dir="/tmp/checkout"
    )

    notify.after_path_move([("a.md", "notes.txt")], sha, actor=None)

    assert agent_activity.list_for_doc("a.md") == []
    assert drafts.get("a.md") is None
    # No-TTL working-dir binding must be dropped too, or a future a.md inherits it.
    assert page_dirs.get_for_page(user_id=user, machine_id="m1", wiki_path="a.md") is None


def test_after_path_move_repoints_update_policy(tmp_repo):
    update_policy.set_policy("a.md", ingestion_auto_update_disabled=True)
    sha = wiki_git.commit_file("a.md", "body\n", "seed", author=None)

    notify.after_path_move([("a.md", "b.md")], sha, actor=None)

    assert update_policy.get("a.md") is None
    assert update_policy.get("b.md") is not None


def test_after_path_move_reconverges_trigger_cache(tmp_repo, monkeypatch):
    # Reconvergence lives in the hook (not the API route) so the agent-tool
    # move path — which also calls after_path_move — picks it up too.
    calls: list[int] = []
    monkeypatch.setattr(
        notify.triggers_repo, "rebuild_from_filesystem", lambda: calls.append(1)
    )
    sha = wiki_git.commit_file("a.md", "body\n", "seed", author=None)

    notify.after_path_move([("a.md", "b.md")], sha, actor=None)

    assert calls == [1]
