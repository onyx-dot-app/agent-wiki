"""Permission gating in the chat-agent tool layer.

The tools in ``app/llm/agents/tools/`` run inside an authenticated chat
request. With permissions live, every wiki-touching tool calls
``require_can`` (read or write) before doing real work. These tests
exercise the negative + positive paths for the four most load-bearing
tools (read_page, read_doc, edit_doc, search_wiki) with two seeded
users and a private page.

The tools normally read ``flask.session`` via ``app.auth.current_user``;
here we patch ``current_user`` directly so we don't need to wire a real
Flask request — the rest of the pipeline (``acl.effective``, the
underlying readers/writers) hits real code.
"""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from app.auth import User
from app.wiki import acl, git as wiki_git
from tests._seed import seed_user
from tests.conftest import needs_opensearch


@pytest.fixture
def repo_with_private_page(tmp_repo):
    """Two users + a page private to Alice (default-public revoked)."""
    alice = seed_user(uid="u_alice", email="alice@x.com")
    bob = seed_user(uid="u_bob", email="bob@x.com")

    body = "# Spec\n\nfindme keyword body.\n"
    wiki_git.commit_file("docs/spec.md", body, "seed")
    # Trigger the production lifecycle hook so the page is owned by
    # Alice and starts out default-public.
    acl.on_page_created("docs/spec.md", owner_user_id=alice)
    # Strip the everyone grants — page is now Alice-only.
    for g in acl.list_for_path("docs/spec.md"):
        if g["principal_kind"] == "everyone":
            acl.revoke(g["id"])
    return {"alice": alice, "bob": bob}


@pytest.fixture(autouse=True)
def _stub_side_effects(monkeypatch):
    """Skip reindex / trigger fan-out so these tests focus on ACL logic."""
    monkeypatch.setattr(
        "app.wiki.utils.wiki_notify.after_doc_write",
        lambda *a, **kw: None,
    )


@contextmanager
def _as_user(monkeypatch, user_id: str | None, *, is_admin: bool = False):
    """Patch ``current_user`` everywhere it's been imported so tools see
    the requested principal. ``None`` simulates no-app-context (anonymous).
    """
    user = (
        User(id=user_id, email=f"{user_id}@x.com", name=None, is_admin=is_admin)
        if user_id is not None
        else None
    )
    # The tools that gate by permission import current_user from app.auth
    # via a local late import inside the helper (`from app.auth import ...
    # require_can`). require_can in turn calls ``current_user()``. Patching
    # the canonical binding is enough.
    monkeypatch.setattr("app.auth.current_user", lambda: user)
    # search_wiki captured ``current_user`` at module load.
    monkeypatch.setattr(
        "app.llm.agents.tools.search_wiki.current_user", lambda: user
    )
    yield user


# --------------------------------------------------------------------------- #
# read_page                                                                   #
# --------------------------------------------------------------------------- #


def test_read_page_allows_owner(repo_with_private_page, monkeypatch):
    from app.llm.agents.tools.read_page import handle

    with _as_user(monkeypatch, repo_with_private_page["alice"]):
        out = handle({"path": "docs/spec.md"})
    assert "error" not in out
    assert out["path"] == "docs/spec.md"
    assert "findme" in out["body"]


def test_read_page_denies_unauthorized_user(repo_with_private_page, monkeypatch):
    from app.llm.agents.tools.read_page import handle

    with _as_user(monkeypatch, repo_with_private_page["bob"]):
        out = handle({"path": "docs/spec.md"})
    assert "error" in out
    assert "forbidden" in out["error"].lower()


def test_read_page_admin_bypasses(repo_with_private_page, monkeypatch):
    from app.llm.agents.tools.read_page import handle

    with _as_user(monkeypatch, "u_admin", is_admin=True):
        out = handle({"path": "docs/spec.md"})
    assert "error" not in out


# --------------------------------------------------------------------------- #
# read_doc                                                                    #
# --------------------------------------------------------------------------- #


def test_read_doc_denies_unauthorized_user(repo_with_private_page, monkeypatch):
    from app.llm.agents.tools.read_doc import handle

    with _as_user(monkeypatch, repo_with_private_page["bob"]):
        out = handle({"path": "docs/spec.md"})
    assert "error" in out


def test_read_doc_allows_after_grant(repo_with_private_page, monkeypatch):
    from app.llm.agents.tools.read_doc import handle

    bob = repo_with_private_page["bob"]
    acl.grant(
        resource_kind="page",
        resource_path="docs/spec.md",
        principal_kind="user",
        principal_id=bob,
        permission="read",
        granted_by_user_id=repo_with_private_page["alice"],
    )
    with _as_user(monkeypatch, bob):
        out = handle({"path": "docs/spec.md"})
    assert "error" not in out
    assert out["path"] == "docs/spec.md"


# --------------------------------------------------------------------------- #
# edit_doc                                                                    #
# --------------------------------------------------------------------------- #


def test_edit_doc_denies_unauthorized_user(repo_with_private_page, monkeypatch):
    from app.llm.agents.tools.edit_doc import handle

    with _as_user(monkeypatch, repo_with_private_page["bob"]):
        out = handle({
            "path": "docs/spec.md",
            "old_string": "Spec",
            "new_string": "Hijacked",
            "commit_message": "x",
        })
    assert "error" in out
    assert "forbidden" in out["error"].lower()


def test_edit_doc_allows_user_with_write_grant(repo_with_private_page, monkeypatch):
    from app.llm.agents.tools.edit_doc import handle

    bob = repo_with_private_page["bob"]
    acl.grant(
        resource_kind="page",
        resource_path="docs/spec.md",
        principal_kind="user",
        principal_id=bob,
        permission="write",
        granted_by_user_id=repo_with_private_page["alice"],
    )
    with _as_user(monkeypatch, bob):
        out = handle({
            "path": "docs/spec.md",
            "old_string": "Spec",
            "new_string": "Spec (updated)",
            "commit_message": "tweak",
        })
    assert "error" not in out, out


def test_edit_doc_read_only_grant_still_denies_write(
    repo_with_private_page, monkeypatch
):
    """A user with ``read`` but not ``write`` should still be blocked
    by edit_doc — read-only grant doesn't promote to writer."""
    from app.llm.agents.tools.edit_doc import handle

    bob = repo_with_private_page["bob"]
    acl.grant(
        resource_kind="page",
        resource_path="docs/spec.md",
        principal_kind="user",
        principal_id=bob,
        permission="read",
        granted_by_user_id=repo_with_private_page["alice"],
    )
    with _as_user(monkeypatch, bob):
        out = handle({
            "path": "docs/spec.md",
            "old_string": "Spec",
            "new_string": "tampered",
            "commit_message": "x",
        })
    assert "error" in out


# --------------------------------------------------------------------------- #
# search_wiki                                                                 #
# --------------------------------------------------------------------------- #


@needs_opensearch
def test_search_wiki_filters_hits_per_user(repo_with_private_page, monkeypatch):
    """search_wiki should respect the calling user's visibility — Bob
    cannot see hits in Alice's private page even when the BM25 index
    has them."""
    from app.llm.agents.tools.search_wiki import handle
    from app.tasks.reindex import index_path_inline

    index_path_inline("docs/spec.md")

    # Alice (owner) finds the hit.
    with _as_user(monkeypatch, repo_with_private_page["alice"]):
        out = handle({"query": "findme"})
    paths = [r["path"] for r in out.get("results", [])]
    assert "docs/spec.md" in paths

    # Bob (no access) gets filtered to empty.
    with _as_user(monkeypatch, repo_with_private_page["bob"]):
        out = handle({"query": "findme"})
    paths = [r["path"] for r in out.get("results", [])]
    assert "docs/spec.md" not in paths


@needs_opensearch
def test_search_wiki_admin_sees_everything(repo_with_private_page, monkeypatch):
    from app.llm.agents.tools.search_wiki import handle
    from app.tasks.reindex import index_path_inline

    index_path_inline("docs/spec.md")
    with _as_user(monkeypatch, "u_admin", is_admin=True):
        out = handle({"query": "findme"})
    paths = [r["path"] for r in out.get("results", [])]
    assert "docs/spec.md" in paths
