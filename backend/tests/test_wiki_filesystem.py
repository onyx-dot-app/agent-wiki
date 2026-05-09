"""Unit tests for ``app/wiki/filesystem.py``.

``safe_rel_path`` is the path-traversal gate every wiki write goes
through. It's tested transitively today — let's pin its behavior
explicitly so a refactor (e.g. switching to ``Path.resolve()``, which
follows symlinks) can't silently weaken the guard.

``parent_dirs`` powers the directory-scope trigger fan-out, so its
exact ordering and root convention matter and get their own tests.
"""
from __future__ import annotations

import pytest


# --------------------------------------------------------------------------- #
# safe_rel_path — accepted shapes                                             #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("a.md", "a.md"),
        ("dir/a.md", "dir/a.md"),
        ("a/b/c.md", "a/b/c.md"),
        # ``normpath`` collapses redundant separators and ``./`` segments.
        ("a//b.md", "a/b.md"),
        ("./a.md", "a.md"),
        ("a/./b.md", "a/b.md"),
        # Trailing slash on a folder path is normalized away.
        ("dir/", "dir"),
        # Non-md inputs are normalized; the .md check lives in callers.
        ("notes", "notes"),
    ],
)
def test_safe_rel_path_normalizes(raw, expected):
    from app.wiki.filesystem import safe_rel_path

    assert safe_rel_path(raw) == expected


# --------------------------------------------------------------------------- #
# safe_rel_path — rejected shapes                                             #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw",
    [
        "../escape.md",
        "a/../../escape.md",
        "../../../etc/passwd",
        "a/../b.md",          # rejected even though it would normalize cleanly
        "..",                 # bare parent reference
        "a/..",
    ],
)
def test_safe_rel_path_rejects_traversal(raw):
    from app.wiki.filesystem import safe_rel_path

    with pytest.raises(ValueError, match="unsafe path"):
        safe_rel_path(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "/abs/path.md",
        "/etc/passwd",
        "/",
    ],
)
def test_safe_rel_path_rejects_absolute(raw):
    from app.wiki.filesystem import safe_rel_path

    with pytest.raises(ValueError, match="unsafe path"):
        safe_rel_path(raw)


def test_safe_rel_path_does_not_treat_dotdot_inside_filename_as_traversal():
    """A literal filename containing ``..`` (no separator) is fine — the
    guard checks path *parts*, not substrings. A regression that
    matched on substring would falsely reject ``release-notes-1..2.md``.
    """
    from app.wiki.filesystem import safe_rel_path

    assert safe_rel_path("release-1..2.md") == "release-1..2.md"
    assert safe_rel_path("dir/release-1..2.md") == "dir/release-1..2.md"


# --------------------------------------------------------------------------- #
# absolute() — composes safe_rel_path with CONFIG.wiki_dir                    #
# --------------------------------------------------------------------------- #


@pytest.fixture
def fs_config(tmp_config, monkeypatch):
    """``tmp_config`` patches ``app.config.CONFIG``, but ``filesystem``
    captured ``CONFIG`` at import time. Patch the captured binding too.
    """
    monkeypatch.setattr("app.wiki.filesystem.CONFIG", tmp_config)
    return tmp_config


def test_absolute_anchors_under_wiki_dir(fs_config):
    from app.wiki.filesystem import absolute

    out = absolute("dir/sub/file.md")
    assert str(out).startswith(fs_config.wiki_dir)
    assert out.name == "file.md"


def test_absolute_rejects_traversal(fs_config):
    from app.wiki.filesystem import absolute

    with pytest.raises(ValueError):
        absolute("../escape.md")


# --------------------------------------------------------------------------- #
# parent_dirs — drives directory-scope trigger fan-out                        #
# --------------------------------------------------------------------------- #


def test_parent_dirs_root_doc_yields_only_root():
    from app.wiki.filesystem import parent_dirs

    assert parent_dirs("guide.md") == [""]


def test_parent_dirs_nested_yields_closest_first_then_root():
    """Order matters — ``find_matching_triggers`` candidates the doc,
    then walks up. A regression that flipped the order would still
    "work" but break expectations elsewhere.
    """
    from app.wiki.filesystem import parent_dirs

    assert parent_dirs("a/b/c/d.md") == ["a/b/c", "a/b", "a", ""]


def test_parent_dirs_normalizes_through_safe_rel_path():
    from app.wiki.filesystem import parent_dirs

    # ``./a/./b/c.md`` collapses to ``a/b/c.md``; parents must reflect that.
    assert parent_dirs("./a/./b/c.md") == ["a/b", "a", ""]


def test_parent_dirs_rejects_traversal():
    from app.wiki.filesystem import parent_dirs

    with pytest.raises(ValueError):
        parent_dirs("../boom.md")
