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


# --------------------------------------------------------------------------- #
# fuzzy (trailing-whitespace-normalized) matching                              #
# --------------------------------------------------------------------------- #


def test_apply_edits_fuzzy_trailing_space_on_find():
    # Model quoted "old text " with a trailing space; body has "old text"
    body = "line one\nold text\nline three\n"
    result = apply_edits(body, [_e("old text ", "new text")])
    assert result == "line one\nnew text\nline three\n"


def test_apply_edits_fuzzy_trailing_space_on_body():
    # Body has trailing spaces on lines; model quoted cleanly without them
    body = "The deploy takes ~5 minutes.   \nRun the script after merging.  \n"
    find = "The deploy takes ~5 minutes.\nRun the script after merging."
    result = apply_edits(body, [_e(find, "The deploy takes ~2 minutes.\nRun the script after merging.")])
    assert result == "The deploy takes ~2 minutes.\nRun the script after merging.\n"


def test_apply_edits_fuzzy_trailing_spaces_multiline():
    # Model quoted a multi-line block with trailing spaces on each line
    body = "## Section\n\nfoo bar\nbaz qux\n\nend\n"
    find = "foo bar  \nbaz qux  "  # trailing spaces the model added
    result = apply_edits(body, [_e(find, "updated block")])
    assert result == "## Section\n\nupdated block\n\nend\n"


def test_apply_edits_fuzzy_preserves_body_trailing_newline():
    body = "alpha\nbeta\n"
    result = apply_edits(body, [_e("beta  ", "gamma")])
    assert result == "alpha\ngamma\n"


def test_apply_edits_fuzzy_no_match_still_skips():
    # Text genuinely absent even after normalization — should still be skipped
    body = "hello world"
    result = apply_edits(body, [_e("completely different   ", "x")])
    assert result is None


def test_apply_edits_fuzzy_mixed_exact_and_fuzzy():
    # First edit matches exactly, second matches only after normalization
    body = "foo\nbar baz\nqux\n"
    result = apply_edits(body, [_e("foo", "FOO"), _e("bar baz  ", "BAR BAZ")])
    assert result == "FOO\nBAR BAZ\nqux\n"
