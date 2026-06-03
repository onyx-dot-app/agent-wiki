"""Tests for ``app.wiki.git``: rename-aware ref resolution.

History tracing across a rename is what makes the file-history endpoint
work for moved pages, but the content endpoint also needs to know the
old name when reading at a pre-rename ref.
"""
from __future__ import annotations


def test_path_at_ref_returns_old_name_for_pre_rename_commit(tmp_repo):
    from app.wiki import git as wiki_git

    create_sha = wiki_git.commit_file("old.md", "v1\n", "create", author=None)
    edit_sha = wiki_git.commit_file("old.md", "v2\n", "edit", author=None)
    wiki_git.move_path("old.md", "new.md", "rename", author=None)

    assert wiki_git.path_at_ref("new.md", create_sha) == "old.md"
    assert wiki_git.path_at_ref("new.md", edit_sha) == "old.md"


def test_path_at_ref_returns_new_name_for_rename_and_later_commits(tmp_repo):
    from app.wiki import git as wiki_git

    wiki_git.commit_file("old.md", "v1\n", "create", author=None)
    rename_sha, _ = wiki_git.move_path("old.md", "new.md", "rename", author=None)
    later_sha = wiki_git.commit_file("new.md", "v2\n", "edit", author=None)

    # The rename commit itself records the file at the new name.
    assert wiki_git.path_at_ref("new.md", rename_sha) == "new.md"
    assert wiki_git.path_at_ref("new.md", later_sha) == "new.md"


def test_path_at_ref_returns_none_for_unrelated_ref(tmp_repo):
    from app.wiki import git as wiki_git

    wiki_git.commit_file("a.md", "x\n", "create a", author=None)
    other_sha = wiki_git.commit_file("b.md", "y\n", "create b", author=None)

    assert wiki_git.path_at_ref("a.md", other_sha) is None


def test_read_at_pre_rename_ref_via_resolved_path(tmp_repo):
    """End-to-end: resolve the historical name, then ``read_file`` succeeds."""
    from app.wiki import git as wiki_git

    create_sha = wiki_git.commit_file("old.md", "original body\n", "create", author=None)
    wiki_git.move_path("old.md", "new.md", "rename", author=None)

    historical = wiki_git.path_at_ref("new.md", create_sha)
    assert historical == "old.md"
    assert wiki_git.read_file(historical, ref=create_sha) == "original body\n"


def test_read_file_opt_returns_body_when_present(tmp_repo):
    from app.wiki import git as wiki_git

    sha = wiki_git.commit_file("a.md", "hello\n", "create", author=None)
    assert wiki_git.read_file_opt("a.md", sha) == "hello\n"


def test_read_file_opt_returns_none_when_absent_at_ref(tmp_repo):
    """A path that doesn't exist at the ref returns None (no raise, no error
    log) — the expected case for a file that's new within a diff window."""
    from app.wiki import git as wiki_git

    base_sha = wiki_git.commit_file("a.md", "x\n", "create a", author=None)
    # b.md is added only later, so it doesn't exist at base_sha.
    wiki_git.commit_file("b.md", "y\n", "create b", author=None)

    assert wiki_git.read_file_opt("b.md", base_sha) is None
