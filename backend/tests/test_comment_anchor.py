"""Unit tests for the comment diff-transform anchor (app/wiki/comment_anchor.py).

These are pure — no DB, no git, no fixtures. The most meaningful assertion is
that the *text under the remapped range* is what we expect, so most cases
slice ``new_body`` with the result.
"""
from __future__ import annotations

import pytest

from app.wiki import comment_anchor
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


def test_rewrite_sharing_characters_orphans_via_survival_guard():
    # Realistic agent rewrite: difflib shares incidental characters
    # (spaces/letters) so the span doesn't fully collapse, but the remapped
    # range would land mostly on text the user never commented on. The
    # preserved-fraction guard orphans it rather than migrating to a wrong
    # location.
    old = "see the old wording here"
    new = "see the brand new phrasing here"
    s, e = old.index("old wording"), old.index("old wording") + len("old wording")
    assert remap_range(old, new, s, e) is None


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


def test_survival_guard_keeps_real_surviving_tail():
    # Even though most of the span's characters were deleted, the survivor
    # ("keep") is a contiguous preserved block, so the remapped highlight is
    # 100% preserved content -> kept, not orphaned.
    old = "remove this whole leading clause but keep"
    new = "keep"
    result = remap_range(old, new, 0, len(old))
    assert _slice(new, result) == "keep"


def test_survival_guard_orphans_when_highlight_would_be_mostly_new():
    # Tiny survivor swamped by inserted text the comment never referred to.
    old = "fox"
    new = "a long inserted sentence f o x with lots of new words around"
    assert remap_range(old, new, 0, 3) is None


def test_word_replacement_keeps_full_word_not_mid_word():
    # "variable" -> "parameter": the span must re-anchor to the whole new word,
    # not truncate mid-word (the reported "environment parame" bug).
    old = "set via the environment variable here"
    new = "set via the environment parameter here"
    s, e = old.index("environment variable"), old.index("environment variable") + len(
        "environment variable"
    )
    result = remap_range(old, new, s, e)
    assert _slice(new, result) == "environment parameter"


def test_in_place_word_edit_keeps_via_word_snap():
    # "weekly" -> "biweekly" inside a span that starts mid-word: snapping pulls
    # in the preserved word ("rotation"), so it re-anchors instead of orphaning.
    old = "The rotation is weekly here"
    new = "The rotation is biweekly here"
    s = old.index("n is weekly")
    e = s + len("n is weekly")
    result = remap_range(old, new, s, e)
    assert result is not None
    assert _slice(new, result) == "rotation is biweekly"


# --------------------------------------------------------------------------- #
# In-place human edits: a word changed inside the span keeps its anchor. The     #
# survival guard gives a replaced token partial credit by the character          #
# similarity of its old and new run, so high-overlap edits (typos, plurals,      #
# prefixes) stay trusted.                                                        #
# --------------------------------------------------------------------------- #


def test_single_word_span_edit_keeps():
    # The whole span is one word that's edited in place — no surrounding word to
    # lean on. Partial credit (high char overlap) keeps it.
    old = "on-call rotation is weekly."
    new = "on-call rotation is biweekly."
    s, e = old.index("weekly"), old.index("weekly") + len("weekly")
    result = remap_range(old, new, s, e)
    assert _slice(new, result) == "biweekly"


def test_typo_fix_inside_word_keeps():
    old = "set the threshold to 80 percent"
    new = "set the threshhold to 80 percent"  # doubled 'h'
    s, e = old.index("threshold"), old.index("threshold") + len("threshold")
    result = remap_range(old, new, s, e)
    assert _slice(new, result) == "threshhold"


def test_pluralize_single_word_keeps():
    old = "restart the pod now"
    new = "restart the pods now"
    s, e = old.index("pod"), old.index("pod") + len("pod")
    result = remap_range(old, new, s, e)
    assert _slice(new, result) == "pods"


def test_number_change_in_span_keeps():
    # Surrounding words survive; the changed token shares no chars but is a small
    # fraction of the span, so the span stays well above the threshold.
    old = "limit is 20 connections per worker"
    new = "limit is 50 connections per worker"
    s, e = old.index("20 connections"), old.index("20 connections") + len("20 connections")
    result = remap_range(old, new, s, e)
    assert _slice(new, result) == "50 connections"


def test_partial_credit_does_not_rescue_unrelated_rewrite():
    # The span maps onto replacement text (it does not collapse), so the survival
    # guard actually runs. Partial credit must NOT rescue it: rewording "quick
    # fix" to the unrelated "thorough overhaul" shares too few characters, so the
    # preserved fraction stays well below the threshold and the comment orphans.
    old = "a quick fix applied"
    new = "a thorough overhaul applied"
    s, e = old.index("quick fix"), old.index("quick fix") + len("quick fix")
    assert remap_range(old, new, s, e) is None


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


# --------------------------------------------------------------------------- #
# body_diff precomputation + cost caps                                        #
# --------------------------------------------------------------------------- #


def test_precomputed_diff_matches_per_call_results():
    old = "\n".join(f"line {i} stays the same" for i in range(30))
    new = old.replace("line 7 stays", "line 7 mostly stays").replace(
        "line 21 stays the same", "entirely new text here"
    )
    diff = comment_anchor.body_diff(old, new)
    spans = [
        (0, 10),
        (old.index("line 7"), old.index("line 7") + len("line 7 stays")),
        (old.index("line 21"), old.index("line 21") + len("line 21 stays the same")),
        (len(old) - 15, len(old)),
    ]
    for s, e in spans:
        assert remap_range(old, new, s, e, diff=diff) == remap_range(old, new, s, e)


def test_multiline_edit_keeps_unchanged_line_anchor_exact():
    old = "alpha\nbravo target words\ncharlie\n"
    new = "alpha\nbravo target words\nCHARLIE REWRITTEN\n"
    s = old.index("target")
    assert remap_range(old, new, s, s + len("target words")) == (s, s + len("target words"))


def test_over_cap_hunk_degrades_to_coarse_replace(monkeypatch):
    monkeypatch.setattr(comment_anchor, "_MAX_HUNK_CHAR_PRODUCT", 4)
    old = "stable\nthe quick brown fox\nstable2\n"
    new = "stable\nthe quick red fox\nstable2\n"
    s = old.index("quick")
    # Under the cap the whole changed line stays one coarse replace opcode, so a
    # span inside it collapses and orphans instead of fine-aligning.
    assert remap_range(old, new, s, s + len("quick brown fox")) is None
    # Anchors on unchanged lines are unaffected by the cap.
    assert remap_range(old, new, 0, len("stable")) == (0, len("stable"))


def test_over_cap_token_diff_skips_survival_guard(monkeypatch):
    monkeypatch.setattr(comment_anchor, "_MAX_TOKEN_PRODUCT", 1)
    old = "alpha\nthe quick brown fox\nomega\n"
    new = "alpha\nthe quick red fox\nomega\n"
    diff = comment_anchor.body_diff(old, new)
    assert diff.token_opcodes is None
    # Endpoint mapping still works; the guard is bypassed rather than orphaning.
    s = old.index("quick")
    result = remap_range(old, new, s, s + len("quick brown fox"), diff=diff)
    assert result is not None
    assert new[result[0] : result[1]] == "quick red fox"


def test_append_only_edit_maps_all_anchors_exactly():
    # Common-suffix/prefix trim: appending at the bottom must leave every
    # existing anchor at its exact old offsets, at any page size.
    old = "\n".join(f"- item {i} with some text" for i in range(500)) + "\n"
    new = old + "- appended row\n"
    diff = comment_anchor.body_diff(old, new)
    for i in (0, 250, 499):  # word-aligned spans so _snap_to_words is a no-op
        s = old.index(f"- item {i} with")
        e = s + len(f"- item {i} with some text")
        assert remap_range(old, new, s, e, diff=diff) == (s, e)


def test_anchor_after_edited_line_shifts_by_exact_delta():
    lines = [f"line {i} content here\n" for i in range(50)]
    old = "".join(lines)
    edited = lines.copy()
    edited[10] = "line 10 content here plus an insertion\n"
    new = "".join(edited)
    delta = len(edited[10]) - len(lines[10])
    s = old.index("line 40")
    e = s + len("line 40 content")
    assert remap_range(old, new, s, e) == (s + delta, e + delta)


def test_over_cap_line_diff_falls_back_to_one_hunk(monkeypatch):
    monkeypatch.setattr(comment_anchor, "_MAX_LINE_PRODUCT", 1)
    # Prefix/suffix lines are trimmed before the cap applies, so anchors there
    # stay exact even when the middle is treated as a single hunk.
    old = "stable head\nAAA one\nBBB two\nstable tail\n"
    new = "stable head\nCCC uno\nDDD dos\nstable tail\n"
    assert remap_range(old, new, 0, len("stable head")) == (0, len("stable head"))
    s = old.index("stable tail")
    s_new = new.index("stable tail")
    assert remap_range(old, new, s, s + 6) == (s_new, s_new + 6)
    # The middle collapses through the coarse hunk and orphans.
    assert remap_range(old, new, old.index("AAA"), old.index("AAA") + 7) is None


def test_huge_replaced_run_gets_no_partial_credit(monkeypatch):
    # An in-place-edited token run over the ratio cap must score zero credit
    # (orphan) instead of running a quadratic similarity pass.
    old = "prefix stays. weekly rotation. suffix stays."
    new = "prefix stays. biweekly rotation. suffix stays."
    s, e = old.index("weekly"), old.index("weekly") + len("weekly")
    # Control: under the normal cap the in-place edit earns partial credit.
    assert remap_range(old, new, s, e) is not None
    # The same edit with its run over the cap earns nothing and orphans.
    monkeypatch.setattr(comment_anchor, "_MAX_RUN_RATIO_PRODUCT", 10)
    assert remap_range(old, new, s, e) is None


def test_replace_run_similarity_computed_once_per_body_pair(monkeypatch):
    # Spans sharing a BodyDiff must pay each edited run's ratio() once, not
    # once per span — the group remap on a many-span page depends on it.
    import difflib

    old = "alpha weekly rotation gamma\n"
    new = "alpha biweekly rotation gamma\n"
    diff = comment_anchor.body_diff(old, new)  # built before counting starts

    constructions: list[int] = []

    class _Counting(difflib.SequenceMatcher):
        def __init__(self, *args, **kwargs):
            constructions.append(1)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(comment_anchor, "SequenceMatcher", _Counting)
    spans = [
        (old.index("weekly"), old.index("weekly") + len("weekly")),
        (0, old.index("rotation") + len("rotation")),
    ]
    results = [remap_range(old, new, s, e, diff=diff) for s, e in spans]
    assert all(r is not None for r in results)
    assert len(constructions) == 1  # the shared run's ratio ran exactly once
