"""First-turn prompt composer."""

from __future__ import annotations

from app.launchers.prompt_builder import build_first_turn_prompt


def test_minimal_composition():
    p = build_first_turn_prompt(
        wiki_path=None,
        page_body=None,
        working_dir=None,
        linked_repos=[],
        user_message="audit auth",
    )
    assert "USER_MESSAGE" in p
    assert "audit auth" in p


def test_with_all_fields():
    p = build_first_turn_prompt(
        wiki_path="projects/auth.md",
        page_body="# Auth refactor\n\nDetails here.",
        working_dir="/Users/u/code/onyx",
        linked_repos=["git@github.com:onyx-dot-app/onyx"],
        user_message="kick off the refactor",
    )
    assert "WIKI_PATH: projects/auth.md" in p
    assert "WORKING_DIRECTORY: /Users/u/code/onyx" in p
    assert "LINKED_REPOS:" in p
    assert "git@github.com:onyx-dot-app/onyx" in p
    assert "Auth refactor" in p
    assert "kick off the refactor" in p


def test_no_wiki_path_skips_line():
    p = build_first_turn_prompt(
        wiki_path=None,
        page_body=None,
        working_dir="/tmp",
        linked_repos=[],
        user_message="x",
    )
    assert "WIKI_PATH" not in p


def test_no_linked_repos_skips_section():
    p = build_first_turn_prompt(
        wiki_path="x.md",
        page_body="body",
        working_dir=None,
        linked_repos=[],
        user_message="m",
    )
    assert "LINKED_REPOS" not in p


def test_page_body_section_only_when_provided():
    p = build_first_turn_prompt(
        wiki_path="x.md",
        page_body=None,
        working_dir=None,
        linked_repos=[],
        user_message="m",
    )
    assert "PAGE_BODY" not in p


def test_prompt_builder_truncates_oversized_body():
    """ — body capped at ~256KB; truncation marker present."""
    big_body = "X" * (300 * 1024)  # 300KB
    p = build_first_turn_prompt(
        wiki_path="x.md",
        page_body=big_body,
        working_dir="/tmp",
        linked_repos=["a/b"],
        user_message="m",
    )
    assert len(p.encode("utf-8")) <= 256 * 1024
    assert "[truncated]" in p
    # User message preserved.
    assert "USER_MESSAGE" in p
    assert p.rstrip().endswith("m")


def test_prompt_builder_unicode_body_truncation_safe():
    """Body containing multi-byte unicode must not produce invalid UTF-8 after slice."""
    body = "💩" * 100_000  # ~400KB of emoji
    p = build_first_turn_prompt(
        wiki_path="x.md",
        page_body=body,
        working_dir=None,
        linked_repos=[],
        user_message="m",
    )
    # Decodes without error.
    p.encode("utf-8").decode("utf-8")
    assert "[truncated]" in p
