"""Unit tests for app.llm.agents.ingest_batch_classifier."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.db.fts import SearchHit
from app.ingest.models import WikiUpdateCandidate
from app.llm.agents.ingest_batch_classifier import (
    Verdict,
    _batch_by_chars,
    classify_candidates,
)


def _hit(path: str, score: float = 1.0) -> SearchHit:
    return SearchHit(doc_id=path, path=path, title=None, snippet="", score=score)


def _candidate(path: str, body: str = "body") -> WikiUpdateCandidate:
    return WikiUpdateCandidate(hit=_hit(path), body=body)


def _llm_response(text: str) -> MagicMock:
    return MagicMock(text=text)


# --------------------------------------------------------------------------- #
# _batch_by_chars                                                              #
# --------------------------------------------------------------------------- #


def test_batch_single_when_all_fit():
    candidates = [_candidate("a", "x" * 100), _candidate("b", "y" * 100)]
    assert _batch_by_chars(candidates, budget=1000) == [candidates]


def test_batch_splits_when_budget_exceeded():
    a = _candidate("a", "x" * 600)
    b = _candidate("b", "y" * 600)
    batches = _batch_by_chars([a, b], budget=1000)
    assert batches == [[a], [b]]


def test_batch_empty():
    assert _batch_by_chars([], budget=1000) == [[]]


# --------------------------------------------------------------------------- #
# classify_candidates                                                          #
# --------------------------------------------------------------------------- #


@patch("app.llm.agents.ingest_batch_classifier.load_prompt", return_value="p")
@patch("app.llm.agents.ingest_batch_classifier.client")
def test_classify_returns_verdicts(mock_client, _mock_prompt):
    candidates = [_candidate("a"), _candidate("b"), _candidate("c")]
    mock_client.complete.return_value = _llm_response('["IRRELEVANT", "NO_CHANGE", "NEEDS_UPDATE"]')

    result = classify_candidates(
        title="T", url="", content="doc", source="s", candidates=candidates, model="m"
    )

    assert result == [Verdict.IRRELEVANT, Verdict.NO_CHANGE, Verdict.NEEDS_UPDATE]


@patch("app.llm.agents.ingest_batch_classifier.load_prompt", return_value="p")
@patch("app.llm.agents.ingest_batch_classifier.client")
def test_classify_empty_returns_empty(mock_client, _mock_prompt):
    result = classify_candidates(
        title=None, url="", content="doc", source="s", candidates=[], model="m"
    )
    assert result == []
    mock_client.complete.assert_not_called()


@patch("app.llm.agents.ingest_batch_classifier.load_prompt", return_value="p")
@patch("app.llm.agents.ingest_batch_classifier.client")
def test_classify_fails_open_on_invalid_json(mock_client, _mock_prompt):
    candidates = [_candidate("a"), _candidate("b")]
    mock_client.complete.return_value = _llm_response("not json")

    result = classify_candidates(
        title=None, url="", content="doc", source="s", candidates=candidates, model="m"
    )

    assert result == [Verdict.NEEDS_UPDATE, Verdict.NEEDS_UPDATE]


@patch("app.llm.agents.ingest_batch_classifier.load_prompt", return_value="p")
@patch("app.llm.agents.ingest_batch_classifier.client")
def test_classify_fails_open_on_llm_exception(mock_client, _mock_prompt):
    candidates = [_candidate("a"), _candidate("b")]
    mock_client.complete.side_effect = RuntimeError("timeout")

    result = classify_candidates(
        title=None, url="", content="doc", source="s", candidates=candidates, model="m"
    )

    assert result == [Verdict.NEEDS_UPDATE, Verdict.NEEDS_UPDATE]


@patch("app.llm.agents.ingest_batch_classifier.load_prompt", return_value="p")
@patch("app.llm.agents.ingest_batch_classifier.client")
def test_classify_wrong_length_fails_open(mock_client, _mock_prompt):
    candidates = [_candidate("a"), _candidate("b")]
    # LLM returns only one verdict for two candidates
    mock_client.complete.return_value = _llm_response('["IRRELEVANT"]')

    result = classify_candidates(
        title=None, url="", content="doc", source="s", candidates=candidates, model="m"
    )

    assert result == [Verdict.NEEDS_UPDATE, Verdict.NEEDS_UPDATE]


@patch("app.llm.agents.ingest_batch_classifier.load_prompt", return_value="p")
@patch("app.llm.agents.ingest_batch_classifier.client")
def test_classify_unknown_verdict_falls_back_to_needs_update(mock_client, _mock_prompt):
    candidates = [_candidate("a"), _candidate("b")]
    mock_client.complete.return_value = _llm_response('["IRRELEVANT", "UNKNOWN_VERDICT"]')

    result = classify_candidates(
        title=None, url="", content="doc", source="s", candidates=candidates, model="m"
    )

    assert result == [Verdict.IRRELEVANT, Verdict.NEEDS_UPDATE]


@patch("app.llm.agents.ingest_batch_classifier.load_prompt", return_value="p")
@patch("app.llm.agents.ingest_batch_classifier.client")
def test_classify_merges_multiple_batches(mock_client, _mock_prompt):
    a = _candidate("a", "x" * 110_000)
    b = _candidate("b", "y" * 110_000)
    mock_client.complete.side_effect = [
        _llm_response('["NEEDS_UPDATE"]'),
        _llm_response('["NO_CHANGE"]'),
    ]

    result = classify_candidates(
        title=None, url="", content="", source="s", candidates=[a, b], model="m"
    )

    assert result == [Verdict.NEEDS_UPDATE, Verdict.NO_CHANGE]
    assert mock_client.complete.call_count == 2


@patch("app.llm.agents.ingest_batch_classifier.load_prompt", return_value="p")
@patch("app.llm.agents.ingest_batch_classifier.client")
def test_classify_one_batch_fails_open_other_succeeds(mock_client, _mock_prompt):
    a = _candidate("a", "x" * 110_000)
    b = _candidate("b", "y" * 110_000)
    mock_client.complete.side_effect = [
        RuntimeError("timeout"),
        _llm_response('["NO_CHANGE"]'),
    ]

    result = classify_candidates(
        title=None, url="", content="", source="s", candidates=[a, b], model="m"
    )

    assert result == [Verdict.NEEDS_UPDATE, Verdict.NO_CHANGE]
