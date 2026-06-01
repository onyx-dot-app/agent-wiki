"""Unit tests for the comment diff-transform anchor (app/wiki/comment_anchor.py).

These are pure — no DB, no git, no fixtures. The most meaningful assertion is
that the *text under the remapped range* is what we expect, so most cases
slice ``new_body`` with the result.
"""
from __future__ import annotations

import pytest

from app.wiki.comment_anchor import remap_range


def _slice(new_body: str, result: tuple[int, int] | None) -> str:
    assert result is not None
    return new_body[result[0] : result[1]]


def test_unchanged_body_returns_same_range():
    body = "the quick brown fox"
    assert remap_range(body, body, 4, 9) == (4, 9)


def test_edit_before_span_shifts_both_endpoints():
    old = "intro. the target sentence."
    new = "a much longer intro. the target sentence."
    # "target sentence" in old
    s, e = old.index("target"), old.index("target") + len("target sentence")
    result = remap_range(old, new, s, e)
    assert _slice(new, result) == "target sentence"


def test_edit_after_span_leaves_it_untouched():
    old = "keep this. drop that."
    new = "keep this. drop that entirely, plus more."
    s, e = 0, len("keep this")
    assert remap_range(old, new, s, e) == (0, len("keep this"))


def test_mid_span_deletion_shrinks_highlight():
    # Comment spans "brown fox"; the inner " bro" ... delete chars from the middle.
    old = "the quick brown fox jumps"
    new = "the quick fox jumps"  # removed "brown "
    s, e = old.index("quick"), old.index("fox") + len("fox")  # "quick brown fox"
    result = remap_range(old, new, s, e)
    # Still anchored, now covering the surviving "quick fox".
    assert _slice(new, result) == "quick fox"


def test_mid_span_insertion_grows_highlight():
    old = "the quick fox"
    new = "the quick brown fox"  # inserted "brown " inside the span
    s, e = old.index("quick"), old.index("fox") + len("fox")  # "quick fox"
    result = remap_range(old, new, s, e)
    assert _slice(new, result) == "quick brown fox"


def test_whole_span_deleted_orphans():
    old = "alpha BETA gamma"
    new = "alpha gamma"  # "BETA " removed
    s, e = old.index("BETA"), old.index("BETA") + len("BETA")
    assert remap_range(old, new, s, e) is None


def test_clean_disjoint_replacement_orphans():
    # The span is exactly one replaced region with no characters in common,
    # so both endpoints collapse onto the replacement boundary -> orphan.
    old = "alpha QQQQ omega"
    new = "alpha ZZZZ omega"
    s, e = old.index("QQQQ"), old.index("QQQQ") + len("QQQQ")
    assert remap_range(old, new, s, e) is None


def test_rewrite_sharing_characters_migrates_not_orphan():
    # Realistic agent rewrite: shared words/spaces/letters mean the span does
    # NOT collapse — the comment migrates onto the rewritten text rather than
    # orphaning. Documents the deliberate exact-diff (non-fuzzy) behavior.
    old = "see the old wording here"
    new = "see the brand new phrasing here"
    s, e = old.index("old wording"), old.index("old wording") + len("old wording")
    result = remap_range(old, new, s, e)
    assert result is not None
    # The migrated range lands within the rewritten middle, never past the
    # untouched " here" suffix.
    assert result[1] <= new.index(" here")


def test_insertion_at_start_edge_stays_outside():
    # Insert immediately before the span — the new text must NOT be highlighted.
    old = "highlighted tail"
    new = "PREFIX highlighted tail"
    s, e = 0, len("highlighted")
    result = remap_range(old, new, s, e)
    assert _slice(new, result) == "highlighted"


def test_insertion_at_end_edge_stays_outside():
    # Insert immediately after the span — the new text must NOT be highlighted.
    old = "head highlighted"
    new = "head highlighted SUFFIX"
    s, e = old.index("highlighted"), len(old)
    result = remap_range(old, new, s, e)
    assert _slice(new, result) == "highlighted"


def test_deletion_overlapping_start_keeps_surviving_tail():
    old = "remove keep"
    new = "keep"  # "remove " deleted, span started inside the deleted part
    s, e = 0, len(old)  # whole "remove keep"
    result = remap_range(old, new, s, e)
    assert _slice(new, result) == "keep"


def test_sentence_removed_later_comment_stays_put():
    # A doc-shaped case: a comment on the 3rd sentence survives deleting the 1st.
    old = "First sentence. Second sentence. Third sentence."
    new = "Second sentence. Third sentence."
    s = old.index("Third sentence.")
    e = s + len("Third sentence.")
    result = remap_range(old, new, s, e)
    assert _slice(new, result) == "Third sentence."


def test_out_of_bounds_raises():
    with pytest.raises(ValueError):
        remap_range("short", "longer body", 0, 99)


def test_disjoint_full_rewrite_orphans():
    # No characters in common between the spanned region and its replacement
    # -> the span collapses and the comment orphans.
    old = "keep [[[[[[[[[[ keep"
    new = "keep )))))))))) keep"
    s, e = old.index("[["), old.index("[[") + 10
    assert remap_range(old, new, s, e) is None
