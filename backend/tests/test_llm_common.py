"""Unit tests for app.llm.agents.common."""
from __future__ import annotations

from app.llm.agents.common import apply_edits


def test_apply_edits_basic():
    result = apply_edits("The limit is unknown.", [("unknown", "20")])
    assert result == "The limit is 20."


def test_apply_edits_multiple():
    result = apply_edits("foo and bar", [("foo", "baz"), ("bar", "qux")])
    assert result == "baz and qux"


def test_apply_edits_replaces_first_occurrence_only():
    result = apply_edits("x x x", [("x", "y")])
    assert result == "y x x"


def test_apply_edits_find_missing_skipped():
    result = apply_edits("body text", [("not present", "replacement")])
    assert result is None


def test_apply_edits_empty_edits_returns_none():
    assert apply_edits("body", []) is None


def test_apply_edits_all_finds_missing_returns_none():
    result = apply_edits("body", [("x", "y"), ("z", "w")])
    assert result is None


def test_apply_edits_partial_match_applies_found():
    result = apply_edits("foo bar", [("foo", "baz"), ("missing", "x")])
    assert result == "baz bar"


def test_apply_edits_multiline():
    body = "## Section\n\nOld paragraph here.\n"
    result = apply_edits(body, [("Old paragraph here.", "New paragraph with info.")])
    assert result == "## Section\n\nNew paragraph with info.\n"


def test_apply_edits_empty_replace_deletes_text():
    result = apply_edits("prefix DELETE_ME suffix", [("DELETE_ME ", "")])
    assert result == "prefix suffix"
