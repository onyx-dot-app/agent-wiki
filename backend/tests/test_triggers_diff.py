"""Tests for ``app/triggers/diff.py``.

Verifies the BEFORE/AFTER snippet builder: diff for edits, full body fallback
for high-density rewrites, and full body for creates.
"""
from __future__ import annotations

from app.triggers import diff as diff_helper


def test_create_returns_empty_before_and_full_after():
    before, after = diff_helper.build_payload("", "hello\n", change_kind="create")
    assert before == ""
    assert after == "hello\n"


def test_edit_returns_diff_when_density_is_low():
    body = "\n".join(f"line {i}" for i in range(50))
    after = body.replace("line 25", "line 25 — updated")
    before, after_snippet = diff_helper.build_payload(body, after, change_kind="edit")
    assert before == body
    assert "<unified diff>" in after_snippet
    assert "line 25 — updated" in after_snippet


def test_edit_falls_back_to_full_body_for_high_density_rewrite():
    before = "old\n"
    after = "completely different content\n"
    before_s, after_s = diff_helper.build_payload(before, after, change_kind="edit")
    assert before_s == before
    assert "<unified diff>" not in after_s
    assert after_s == after


def test_truncates_oversized_bodies():
    big = "x" * 20000
    before, after = diff_helper.build_payload("", big, change_kind="create")
    assert "[truncated" in after
    assert len(after) < len(big)
