"""Unit tests for app.llm.agents.ingest_batch_reconciler."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.db.fts import SearchHit
from app.ingest.models import WikiUpdateCandidate
from app.llm.agents.ingest_batch_reconciler import (
    _batch_by_chars,
    _parse,
    batch_reconcile,
)
from app.llm.agents.common import IRRELEVANT_SENTINEL


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
# _parse                                                                       #
# --------------------------------------------------------------------------- #


def test_parse_irrelevant():
    text = "===RESULT [1]===\nIRRELEVANT\n===RESULT [2]===\nIRRELEVANT"
    results = _parse(text, 2)
    assert results == [IRRELEVANT_SENTINEL, IRRELEVANT_SENTINEL]


def test_parse_no_change():
    text = "===RESULT [1]===\nNO_CHANGE"
    results = _parse(text, 1)
    assert results == [None]


def test_parse_new_body():
    text = "===RESULT [1]===\n# Updated\n\nNew content here."
    results = _parse(text, 1)
    assert results == ["# Updated\n\nNew content here."]


def test_parse_mixed():
    text = (
        "===RESULT [1]===\nIRRELEVANT\n"
        "===RESULT [2]===\nNO_CHANGE\n"
        "===RESULT [3]===\n# New body\n\nContent."
    )
    results = _parse(text, 3)
    assert results[0] == IRRELEVANT_SENTINEL
    assert results[1] is None
    assert results[2] == "# New body\n\nContent."


def test_parse_missing_section_defaults_to_irrelevant():
    text = "===RESULT [1]===\nNO_CHANGE"
    results = _parse(text, 2)
    assert results[0] is None
    assert results[1] == IRRELEVANT_SENTINEL


def test_parse_no_sections_raises():
    with pytest.raises(ValueError, match="no ===RESULT"):
        _parse("no sections at all", 1)


# --------------------------------------------------------------------------- #
# batch_reconcile                                                              #
# --------------------------------------------------------------------------- #


@patch("app.llm.agents.ingest_batch_reconciler.load_prompt", return_value="p")
@patch("app.llm.agents.ingest_batch_reconciler.client")
def test_reconcile_returns_results(mock_client, _mock_prompt):
    candidates = [_candidate("a"), _candidate("b"), _candidate("c")]
    mock_client.complete.return_value = _llm_response(
        "===RESULT [1]===\nIRRELEVANT\n"
        "===RESULT [2]===\nNO_CHANGE\n"
        "===RESULT [3]===\n# New\n\nBody."
    )

    results, llm_calls = batch_reconcile(
        title="T", url="", content="doc", source="s", candidates=candidates, model="m"
    )

    assert results[0] == IRRELEVANT_SENTINEL
    assert results[1] is None
    assert results[2] == "# New\n\nBody."
    assert llm_calls == 1


@patch("app.llm.agents.ingest_batch_reconciler.load_prompt", return_value="p")
@patch("app.llm.agents.ingest_batch_reconciler.client")
def test_reconcile_empty_returns_empty(mock_client, _mock_prompt):
    results, llm_calls = batch_reconcile(
        title=None, url="", content="doc", source="s", candidates=[], model="m"
    )
    assert results == []
    assert llm_calls == 0
    mock_client.complete.assert_not_called()


@patch("app.llm.agents.ingest_batch_reconciler.load_prompt", return_value="p")
@patch("app.llm.agents.ingest_batch_reconciler.client")
def test_reconcile_fails_open_on_llm_exception(mock_client, _mock_prompt):
    candidates = [_candidate("a"), _candidate("b")]
    mock_client.complete.side_effect = RuntimeError("timeout")

    results, llm_calls = batch_reconcile(
        title=None, url="", content="doc", source="s", candidates=candidates, model="m"
    )

    assert results == [IRRELEVANT_SENTINEL, IRRELEVANT_SENTINEL]
    assert llm_calls == 1


@patch("app.llm.agents.ingest_batch_reconciler.load_prompt", return_value="p")
@patch("app.llm.agents.ingest_batch_reconciler.client")
def test_reconcile_fails_open_on_parse_error(mock_client, _mock_prompt):
    candidates = [_candidate("a"), _candidate("b")]
    mock_client.complete.return_value = _llm_response("no result sections here")

    results, llm_calls = batch_reconcile(
        title=None, url="", content="doc", source="s", candidates=candidates, model="m"
    )

    assert results == [IRRELEVANT_SENTINEL, IRRELEVANT_SENTINEL]
    assert llm_calls == 1


@patch("app.llm.agents.ingest_batch_reconciler.load_prompt", return_value="p")
@patch("app.llm.agents.ingest_batch_reconciler.client")
def test_reconcile_merges_multiple_batches(mock_client, _mock_prompt):
    a = _candidate("a", "x" * 110_000)
    b = _candidate("b", "y" * 110_000)
    mock_client.complete.side_effect = [
        _llm_response("===RESULT [1]===\n# Updated a\n\nBody."),
        _llm_response("===RESULT [1]===\nNO_CHANGE"),
    ]

    results, llm_calls = batch_reconcile(
        title=None, url="", content="", source="s", candidates=[a, b], model="m"
    )

    assert results[0] == "# Updated a\n\nBody."
    assert results[1] is None
    assert llm_calls == 2


@patch("app.llm.agents.ingest_batch_reconciler.load_prompt", return_value="p")
@patch("app.llm.agents.ingest_batch_reconciler.client")
def test_reconcile_one_batch_fails_open_other_succeeds(mock_client, _mock_prompt):
    a = _candidate("a", "x" * 110_000)
    b = _candidate("b", "y" * 110_000)
    mock_client.complete.side_effect = [
        RuntimeError("timeout"),
        _llm_response("===RESULT [1]===\nNO_CHANGE"),
    ]

    results, llm_calls = batch_reconcile(
        title=None, url="", content="", source="s", candidates=[a, b], model="m"
    )

    assert results == [IRRELEVANT_SENTINEL, None]
    assert llm_calls == 2
