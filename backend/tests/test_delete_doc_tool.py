"""The `delete_doc` tool — agent-initiated soft delete (chat + MCP).

Same Trash flow as the UI delete: trash-move + full lifecycle, restorable.
Pages only; write-gated; folder paths and missing files are errors.
"""
from __future__ import annotations

from typing import Any

from app.auth import User, set_current_user
from app.llm.agents.tools import dispatch as registry_dispatch
from app.wiki import git as wiki_git
from app.wiki import trash
from app.wiki import utils as wiki_utils
from tests._seed import seed_user


def _delete(path: str) -> dict[str, Any]:
    return registry_dispatch("delete_doc", {"path": path})


def test_delete_moves_page_to_trash(tmp_repo):
    wiki_git.commit_file("notes/old.md", "# Old\n\nstale\n", "seed", author=None)

    out = _delete("notes/old.md")

    assert out["deleted"] == "notes/old.md"
    assert not wiki_utils.file_exists("notes/old.md")
    entry = trash.entry_for_original_path("notes/old.md")
    assert entry is not None  # restorable from Trash


def test_delete_missing_page_errors(tmp_repo):
    out = _delete("nope.md")
    assert "file not found" in out["error"]


def test_delete_folder_path_errors(tmp_repo):
    wiki_git.commit_file("dir/page.md", "# P\n", "seed", author=None)
    out = _delete("dir")
    assert "error" in out  # not a .md page


def test_delete_requires_write_access(tmp_repo):
    wiki_git.commit_file("locked/page.md", "# L\n", "seed", author=None)
    owner = seed_user(uid="owner", email="o@x.com")
    from app.wiki import acl

    # Owner stamp with no grants = private to the owner (managed page).
    acl.set_owner("locked/page.md", owner)

    outsider = seed_user(uid="outsider", email="x@x.com")
    with set_current_user(
        User(id=outsider, email="x@x.com", name=None, is_admin=False)
    ):
        out = _delete("locked/page.md")
    assert "error" in out
    assert wiki_utils.file_exists("locked/page.md")  # untouched
