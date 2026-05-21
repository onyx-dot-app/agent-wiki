"""Unit tests for app.llm.agents.common."""
from __future__ import annotations

from app.llm.agents.common import TextEdit, _fuzzy_replace, apply_edits


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
# _fuzzy_replace                                                               #
# --------------------------------------------------------------------------- #


def test_fuzzy_replace_trailing_space_on_find():
    assert _fuzzy_replace("line\nold text\nend", "old text ", "new text") == "line\nnew text\nend"


def test_fuzzy_replace_trailing_space_on_body():
    body = "foo bar   \nbaz\n"
    assert _fuzzy_replace(body, "foo bar", "updated") == "updated\nbaz\n"


def test_fuzzy_replace_multiline_trailing_spaces():
    body = "## Title\n\nfoo  \nbar  \n\nend\n"
    assert _fuzzy_replace(body, "foo\nbar", "replaced") == "## Title\n\nreplaced\n\nend\n"


def test_fuzzy_replace_no_match_returns_none():
    assert _fuzzy_replace("hello world", "not here", "x") is None


def test_fuzzy_replace_exact_text_also_works():
    # _fuzzy_replace handles the case where normalization produces an exact match
    assert _fuzzy_replace("foo\nbar\n", "foo\nbar", "baz") == "baz\n"


def test_fuzzy_replace_preserves_content_outside_match():
    body = "before\ntarget line  \nafter\n"
    result = _fuzzy_replace(body, "target line", "replacement")
    assert result == "before\nreplacement\nafter\n"


def test_fuzzy_replace_empty_replace_removes_match():
    body = "keep\nremove me  \nkeep\n"
    assert _fuzzy_replace(body, "remove me", "") == "keep\n\nkeep\n"


def test_fuzzy_replace_returns_none_when_only_whitespace_differs_midline():
    # Spaces in the middle of a line are not normalised — not a trailing-space issue
    assert _fuzzy_replace("foo  bar", "foo bar", "x") is None


# --------------------------------------------------------------------------- #
# fuzzy matching via apply_edits                                               #
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
