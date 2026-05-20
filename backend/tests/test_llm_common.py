"""Unit tests for app.llm.agents.common."""
from __future__ import annotations

from app.llm.agents.common import TextEdit, apply_edits


def _e(find: str, replace: str) -> TextEdit:
    return TextEdit(find=find, replace=replace)


def test_apply_edits_basic():
    result = apply_edits("The limit is unknown.", [_e("unknown", "20")])
    assert result == "The limit is 20."


def test_apply_edits_multiple():
    result = apply_edits("foo and bar", [_e("foo", "baz"), _e("bar", "qux")])
    assert result == "baz and qux"


def test_apply_edits_replaces_first_occurrence_only():
    result = apply_edits("x x x", [_e("x", "y")])
    assert result == "y x x"


def test_apply_edits_find_missing_skipped():
    result = apply_edits("body text", [_e("not present", "replacement")])
    assert result is None


def test_apply_edits_empty_edits_returns_none():
    assert apply_edits("body", []) is None


def test_apply_edits_all_finds_missing_returns_none():
    result = apply_edits("body", [_e("x", "y"), _e("z", "w")])
    assert result is None


def test_apply_edits_partial_match_applies_found():
    result = apply_edits("foo bar", [_e("foo", "baz"), _e("missing", "x")])
    assert result == "baz bar"


def test_apply_edits_multiline():
    body = "## Section\n\nOld paragraph here.\n"
    result = apply_edits(body, [_e("Old paragraph here.", "New paragraph with info.")])
    assert result == "## Section\n\nNew paragraph with info.\n"


def test_apply_edits_empty_replace_deletes_text():
    result = apply_edits("prefix DELETE_ME suffix", [_e("DELETE_ME ", "")])
    assert result == "prefix suffix"
