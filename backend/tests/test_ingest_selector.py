"""Unit tests for app.llm.agents.ingest_selector."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.db.fts import SearchHit
from app.llm.agents.ingest_selector import Candidate, _batch_by_chars, select_candidates


def _hit(path: str, score: float = 1.0) -> SearchHit:
    return SearchHit(doc_id=path, path=path, title=None, snippet="", score=score)


def _candidate(path: str, body: str = "body") -> Candidate:
    return Candidate(hit=_hit(path), body=body)


def _llm_response(text: str) -> MagicMock:
    return MagicMock(text=text)


# --------------------------------------------------------------------------- #
# _batch_by_chars                                                              #
# --------------------------------------------------------------------------- #


def test_batch_single_when_all_fit():
    candidates = [_candidate("a", "x" * 100), _candidate("b", "y" * 100)]
    batches = _batch_by_chars(candidates, budget=1000)
    assert batches == [candidates]


def test_batch_splits_when_budget_exceeded():
    a = _candidate("a", "x" * 600)
    b = _candidate("b", "y" * 600)
    batches = _batch_by_chars([a, b], budget=1000)
    assert len(batches) == 2
    assert batches[0] == [a]
    assert batches[1] == [b]


def test_batch_packs_greedily():
    # a+b fit together; c needs its own batch
    a = _candidate("a", "x" * 300)
    b = _candidate("b", "y" * 300)
    c = _candidate("c", "z" * 600)
    batches = _batch_by_chars([a, b, c], budget=700)
    assert len(batches) == 2
    assert batches[0] == [a, b]
    assert batches[1] == [c]


def test_batch_zero_budget_each_candidate_own_batch():
    candidates = [_candidate("a"), _candidate("b"), _candidate("c")]
    batches = _batch_by_chars(candidates, budget=0)
    assert len(batches) == 3


def test_batch_empty_candidates():
    assert _batch_by_chars([], budget=1000) == [[]]


# --------------------------------------------------------------------------- #
# select_candidates                                                            #
# --------------------------------------------------------------------------- #


@patch("app.llm.agents.ingest_selector.load_prompt", return_value="prompt")
@patch("app.llm.agents.ingest_selector.client")
def test_select_returns_kept_candidates(mock_client, _mock_prompt):
    candidates = [_candidate("a"), _candidate("b"), _candidate("c")]
    mock_client.complete.return_value = _llm_response("[1, 3]")

    result = select_candidates(title="T", content="doc", candidates=candidates, model="m")

    assert [c.hit.path for c in result] == ["a", "c"]


@patch("app.llm.agents.ingest_selector.load_prompt", return_value="prompt")
@patch("app.llm.agents.ingest_selector.client")
def test_select_empty_array_drops_all(mock_client, _mock_prompt):
    candidates = [_candidate("a"), _candidate("b")]
    mock_client.complete.return_value = _llm_response("[]")

    result = select_candidates(title=None, content="doc", candidates=candidates, model="m")

    assert result == []


@patch("app.llm.agents.ingest_selector.load_prompt", return_value="prompt")
@patch("app.llm.agents.ingest_selector.client")
def test_select_fails_open_on_invalid_json(mock_client, _mock_prompt):
    candidates = [_candidate("a"), _candidate("b")]
    mock_client.complete.return_value = _llm_response("not json")

    result = select_candidates(title=None, content="doc", candidates=candidates, model="m")

    assert result == candidates


@patch("app.llm.agents.ingest_selector.load_prompt", return_value="prompt")
@patch("app.llm.agents.ingest_selector.client")
def test_select_fails_open_on_llm_exception(mock_client, _mock_prompt):
    candidates = [_candidate("a"), _candidate("b")]
    mock_client.complete.side_effect = RuntimeError("timeout")

    result = select_candidates(title=None, content="doc", candidates=candidates, model="m")

    assert result == candidates


@patch("app.llm.agents.ingest_selector.load_prompt", return_value="prompt")
@patch("app.llm.agents.ingest_selector.client")
def test_select_ignores_out_of_range_indices(mock_client, _mock_prompt):
    candidates = [_candidate("a"), _candidate("b")]
    # indices are 1-based; 0 and 99 are out of range, 1 and 2 are valid
    mock_client.complete.return_value = _llm_response("[0, 1, 99]")

    result = select_candidates(title=None, content="doc", candidates=candidates, model="m")

    assert [c.hit.path for c in result] == ["a"]


@patch("app.llm.agents.ingest_selector.load_prompt", return_value="prompt")
@patch("app.llm.agents.ingest_selector.client")
def test_select_empty_candidates_returns_immediately(mock_client, _mock_prompt):
    result = select_candidates(title=None, content="doc", candidates=[], model="m")

    assert result == []
    mock_client.complete.assert_not_called()


@patch("app.llm.agents.ingest_selector.load_prompt", return_value="prompt")
@patch("app.llm.agents.ingest_selector.client")
def test_select_merges_multiple_batches(mock_client, _mock_prompt):
    # Each body exceeds half the 200k budget so they must split into two batches
    a = _candidate("a", "x" * 110_000)
    b = _candidate("b", "y" * 110_000)
    # First batch keeps [1], second batch keeps [1]
    mock_client.complete.side_effect = [
        _llm_response("[1]"),
        _llm_response("[1]"),
    ]

    result = select_candidates(title=None, content="", candidates=[a, b], model="m")

    assert [c.hit.path for c in result] == ["a", "b"]
    assert mock_client.complete.call_count == 2


@patch("app.llm.agents.ingest_selector.load_prompt", return_value="prompt")
@patch("app.llm.agents.ingest_selector.client")
def test_select_one_batch_fails_open_other_succeeds(mock_client, _mock_prompt):
    a = _candidate("a", "x" * 110_000)
    b = _candidate("b", "y" * 110_000)
    mock_client.complete.side_effect = [
        RuntimeError("timeout"),
        _llm_response("[1]"),
    ]

    result = select_candidates(title=None, content="", candidates=[a, b], model="m")

    # first batch fails open (returns a), second batch keeps b
    assert {c.hit.path for c in result} == {"a", "b"}
