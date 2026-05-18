"""Parse linked_repos from page frontmatter."""

from __future__ import annotations

from app.wiki.linked_repos import parse_linked_repos


def test_no_frontmatter_returns_empty():
    body = "# A doc\n\nNo frontmatter."
    assert parse_linked_repos(body) == []


def test_frontmatter_no_linked_repos_key():
    body = "---\ntitle: x\n---\n# Body"
    assert parse_linked_repos(body) == []


def test_single_repo_string():
    body = """---
linked_repos:
  - git@github.com:onyx-dot-app/onyx
---
# Body"""
    assert parse_linked_repos(body) == ["git@github.com:onyx-dot-app/onyx"]


def test_multiple_repos():
    body = """---
linked_repos:
  - git@github.com:onyx-dot-app/onyx
  - git@github.com:onyx-dot-app/agent-wiki
---
# Body"""
    assert parse_linked_repos(body) == [
        "git@github.com:onyx-dot-app/onyx",
        "git@github.com:onyx-dot-app/agent-wiki",
    ]


def test_non_list_value_returns_empty():
    body = "---\nlinked_repos: not-a-list\n---\n# Body"
    assert parse_linked_repos(body) == []


def test_non_string_items_filtered():
    body = """---
linked_repos:
  - git@github.com:a/b
  - 42
  - null
---
# Body"""
    assert parse_linked_repos(body) == ["git@github.com:a/b"]


def test_malformed_frontmatter_returns_empty():
    body = "---\nnot: yaml: at: all:::\n---\n# Body"
    assert parse_linked_repos(body) == []
