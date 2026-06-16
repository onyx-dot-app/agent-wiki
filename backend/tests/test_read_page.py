"""Tests for the `read_page` tool."""
from __future__ import annotations

import pytest


@pytest.fixture
def repo_with_doc(tmp_repo, tmp_config):
    from app.wiki import git as wiki_git

    wiki_git.commit_file(
        "guide.md", "# Guide\n\nFirst section.\n", "seed", author=None
    )
    return tmp_config


def test_read_page_returns_full_body(repo_with_doc):
    from app.llm.agents.tools.read_page import handle

    out = handle({"path": "guide.md"})
    assert "error" not in out, out
    assert out["path"] == "guide.md"
    assert out["title"] == "Guide"
    assert "First section." in out["body"]


def test_read_page_rejects_missing_file(repo_with_doc):
    from app.llm.agents.tools.read_page import handle

    out = handle({"path": "missing.md"})
    assert "error" in out
    assert "not found" in out["error"]


def test_read_page_rejects_non_md(repo_with_doc):
    from app.llm.agents.tools.read_page import handle

    out = handle({"path": "guide.txt"})
    assert "error" in out
    assert ".md" in out["error"]


def test_read_page_rejects_traversal(repo_with_doc):
    from app.llm.agents.tools.read_page import handle

    out = handle({"path": "../escape.md"})
    assert "error" in out
    assert "unsafe" in out["error"] or "invalid" in out["error"]


def test_read_page_falls_back_to_filename_when_no_h1(tmp_repo, tmp_config):
    from app.wiki import git as wiki_git
    from app.llm.agents.tools.read_page import handle

    wiki_git.commit_file("notes/raw.md", "Just a paragraph.\n", "seed", author=None)
    out = handle({"path": "notes/raw.md"})
    assert out["title"] == "raw"


def test_read_page_surfaces_update_instruction(repo_with_doc):
    from app.llm.agents.tools.read_page import handle
    from app.wiki import update_policy

    update_policy.set_policy("guide.md", update_instruction="Keep it terse.")
    out = handle({"path": "guide.md"})
    assert out["update_instruction"] == "Keep it terse."


def test_read_page_omits_update_instruction_when_none(repo_with_doc):
    from app.llm.agents.tools.read_page import handle

    out = handle({"path": "guide.md"})
    assert "update_instruction" not in out
