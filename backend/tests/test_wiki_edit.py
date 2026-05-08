"""Tests for the fuzzy find-and-replace chain in ``app.wiki.edit``."""
from __future__ import annotations

import pytest

from app.wiki import edit


def test_simple_exact_replace():
    assert edit.replace("hello world", "world", "there") == "hello there"


def test_no_op_raises():
    with pytest.raises(edit.ReplaceNoOp):
        edit.replace("foo", "foo", "foo")


def test_not_found_raises():
    with pytest.raises(edit.ReplaceNotFound):
        edit.replace("abc", "xyz", "q")


def test_ambiguous_without_replace_all_raises():
    with pytest.raises(edit.ReplaceAmbiguous):
        edit.replace("foo foo foo", "foo", "bar")


def test_replace_all_swaps_every_occurrence():
    assert edit.replace("foo foo foo", "foo", "bar", replace_all=True) == "bar bar bar"


def test_line_trimmed_handles_leading_whitespace_drift():
    content = "    line one\n    line two\n    line three\n"
    # Search omits the leading indent; line-trimmed should still match,
    # and the matched block (with its indent) is replaced wholesale.
    assert (
        edit.replace(content, "line one\nline two\nline three", "REPLACED")
        == "REPLACED\n"
    )


def test_block_anchor_handles_middle_drift():
    # First and last lines match exactly; middle has a drifted token.
    content = "def foo():\n    x = 1\n    return x\n"
    search = "def foo():\n    y = 1\n    return x"
    assert edit.replace(content, search, "DONE") == "DONE\n"


def test_indentation_flexible():
    content = "    if x:\n        do_thing()\n"
    search = "if x:\n    do_thing()"
    assert edit.replace(content, search, "PASS") == "PASS\n"


def test_whitespace_normalized_collapses_runs():
    content = "alpha   beta  gamma"
    assert edit.replace(content, "alpha beta gamma", "X") == "X"


def test_trimmed_boundary():
    content = "hello world"
    assert edit.replace(content, "  hello world  ", "X") == "X"


def test_escape_normalized():
    content = "first\nsecond\nthird"
    # Model double-escaped the newlines — should still match.
    assert edit.replace(content, "first\\nsecond\\nthird", "X") == "X"


def test_context_aware_recovers_from_minor_middle_changes():
    content = "BEGIN\nalpha\nbeta\ngamma\nEND"
    search = "BEGIN\nalpha\nDRIFTED\ngamma\nEND"
    assert edit.replace(content, search, "BLOCK") == "BLOCK"


def test_replace_all_with_fuzzy_chain_is_safe():
    # When `replace_all` is set we still go through the chain, but the
    # SimpleReplacer hit short-circuits before any fuzzy replacer fires.
    content = "x = 1\nx = 1\nx = 1\n"
    assert edit.replace(content, "x = 1", "y = 2", replace_all=True) == (
        "y = 2\ny = 2\ny = 2\n"
    )
