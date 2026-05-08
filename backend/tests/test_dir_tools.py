"""End-to-end tests for the directory/move tools.

`create_directory` drops a `.gitkeep` and commits; `move_path` renames a
file or directory via `git mv`. Both run against a tmp wiki git repo and
stub Huey side effects.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _stub_side_effects(monkeypatch):
    """Stub the post-write seam so tests don't queue Huey tasks.

    ``move_path.py`` calls ``wiki_notify.after_path_move``; patching it
    short-circuits both FTS updates and trigger fan-outs. Coverage of
    those lives in ``test_save_to_fire_e2e.py`` and ``test_triggers_fanout.py``.
    """
    monkeypatch.setattr(
        "app.llm.agents.tools.move_path.wiki_notify.after_path_move",
        lambda *a, **kw: None,
    )


@pytest.fixture
def repo(tmp_repo, tmp_config):
    from app.wiki import git as wiki_git

    wiki_git.commit_file("guide.md", "# Guide\n\nbody\n", "seed", author=None)
    wiki_git.commit_file(
        "auth/passwords.md", "# Passwords\n", "seed", author=None
    )
    wiki_git.commit_file("auth/sessions.md", "# Sessions\n", "seed", author=None)
    return tmp_config


# --------------------------------------------------------------------------- #
# create_directory                                                            #
# --------------------------------------------------------------------------- #


def test_create_directory_creates_gitkeep(repo):
    from app.llm.agents.tools.create_directory import handle

    out = handle({"path": "ops", "message": "add ops folder"})
    assert "error" not in out, out
    assert out["path"] == "ops"
    assert out["created"] is True
    assert Path(repo.wiki_dir, "ops", ".gitkeep").is_file()


def test_create_directory_rejects_md(repo):
    from app.llm.agents.tools.create_directory import handle

    out = handle({"path": "ops.md", "message": "x"})
    assert "error" in out
    assert ".md" in out["error"]


def test_create_directory_rejects_existing(repo):
    from app.llm.agents.tools.create_directory import handle

    out = handle({"path": "auth", "message": "x"})
    assert "error" in out
    assert "exists" in out["error"]


def test_create_directory_rejects_traversal(repo):
    from app.llm.agents.tools.create_directory import handle

    out = handle({"path": "../escape", "message": "x"})
    assert "error" in out


def test_create_directory_requires_message(repo):
    from app.llm.agents.tools.create_directory import handle

    out = handle({"path": "ops", "message": ""})
    assert "error" in out
    assert "message" in out["error"]


# --------------------------------------------------------------------------- #
# move_path                                                                   #
# --------------------------------------------------------------------------- #


def test_move_path_renames_file(repo):
    from app.llm.agents.tools.move_path import handle

    out = handle(
        {
            "old_path": "guide.md",
            "new_path": "intro.md",
            "message": "rename guide -> intro",
        }
    )
    assert "error" not in out, out
    assert out["old_path"] == "guide.md"
    assert out["new_path"] == "intro.md"
    assert out["moved"] == [{"old": "guide.md", "new": "intro.md"}]
    assert not Path(repo.wiki_dir, "guide.md").exists()
    assert Path(repo.wiki_dir, "intro.md").read_text().startswith("# Guide")


def test_move_path_moves_directory_recursively(repo):
    from app.llm.agents.tools.move_path import handle

    out = handle(
        {
            "old_path": "auth",
            "new_path": "identity",
            "message": "rename auth -> identity",
        }
    )
    assert "error" not in out, out
    moved = sorted((m["old"], m["new"]) for m in out["moved"])
    assert moved == [
        ("auth/passwords.md", "identity/passwords.md"),
        ("auth/sessions.md", "identity/sessions.md"),
    ]
    assert not Path(repo.wiki_dir, "auth").exists()
    assert Path(repo.wiki_dir, "identity/passwords.md").is_file()
    assert Path(repo.wiki_dir, "identity/sessions.md").is_file()


def test_move_path_rejects_missing_source(repo):
    from app.llm.agents.tools.move_path import handle

    out = handle(
        {"old_path": "nope.md", "new_path": "yep.md", "message": "x"}
    )
    assert "error" in out
    assert "not found" in out["error"]


def test_move_path_rejects_existing_target(repo):
    from app.llm.agents.tools.move_path import handle

    out = handle(
        {
            "old_path": "guide.md",
            "new_path": "auth/passwords.md",
            "message": "x",
        }
    )
    assert "error" in out
    assert "already exists" in out["error"]


def test_move_path_rejects_identical_paths(repo):
    from app.llm.agents.tools.move_path import handle

    out = handle({"old_path": "guide.md", "new_path": "guide.md", "message": "x"})
    assert "error" in out
    assert "identical" in out["error"]


def test_move_path_rejects_md_to_non_md(repo):
    from app.llm.agents.tools.move_path import handle

    out = handle(
        {"old_path": "guide.md", "new_path": "guide", "message": "x"}
    )
    assert "error" in out
    assert ".md" in out["error"]


def test_move_path_rejects_dir_to_md(repo):
    from app.llm.agents.tools.move_path import handle

    out = handle(
        {"old_path": "auth", "new_path": "auth.md", "message": "x"}
    )
    assert "error" in out
    assert ".md" in out["error"]


def test_move_path_rejects_traversal(repo):
    from app.llm.agents.tools.move_path import handle

    out = handle(
        {"old_path": "../oops", "new_path": "intro.md", "message": "x"}
    )
    assert "error" in out
