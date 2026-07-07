"""Unit tests for app.llm.agents.ingest_batch_reconciler."""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

from app.db.fts import SearchHit
from app.ingest.models import WikiUpdateCandidate
from app.llm.agents.common import IRRELEVANT_SENTINEL, TextEdit, batch_by_chars, today_str
from app.llm.agents.ingest_batch_reconciler import (
    _METADATA_MAX_CHARS,
    _format_metadata,
    _parse_tool_results,
    batch_reconcile,
)
from app.llm.client import ToolCall


def _hit(path: str, score: float = 1.0) -> SearchHit:
    return SearchHit(doc_id=path, path=path, title=None, snippet="", score=score)


def _candidate(path: str, body: str = "body") -> WikiUpdateCandidate:
    return WikiUpdateCandidate(hit=_hit(path), body=body)


def _tool_call(arguments: dict) -> ToolCall:
    return ToolCall(id="call_1", name="submit_results", arguments=arguments)


def _llm_response(arguments: dict) -> MagicMock:
    m = MagicMock()
    m.text = ""
    m.tool_calls = [_tool_call(arguments)]
    m.usage.input_tokens = 100
    m.usage.output_tokens = 50
    return m


def _llm_no_tool() -> MagicMock:
    m = MagicMock()
    m.text = "some text the model returned instead of a tool call"
    m.tool_calls = []
    m.usage.input_tokens = 100
    m.usage.output_tokens = 50
    return m


# --------------------------------------------------------------------------- #
# batch_by_chars                                                               #
# --------------------------------------------------------------------------- #


def test_batch_single_when_all_fit():
    candidates = [_candidate("a", "x" * 100), _candidate("b", "y" * 100)]
    assert batch_by_chars(candidates, budget=1000) == [candidates]


def test_batch_splits_when_budget_exceeded():
    a = _candidate("a", "x" * 600)
    b = _candidate("b", "y" * 600)
    batches = batch_by_chars([a, b], budget=1000)
    assert batches == [[a], [b]]


def test_batch_empty():
    assert batch_by_chars([], budget=1000) == [[]]


# --------------------------------------------------------------------------- #
# _parse_tool_results                                                          #
# --------------------------------------------------------------------------- #


def test_parse_tool_results_irrelevant():
    batch = [_candidate("a"), _candidate("b")]
    tc = _tool_call({
        "results": [
            {"candidate_index": 1, "action": "irrelevant"},
            {"candidate_index": 2, "action": "irrelevant"},
        ]
    })
    results = _parse_tool_results(tc, batch)
    assert results == [IRRELEVANT_SENTINEL, IRRELEVANT_SENTINEL]


def test_parse_tool_results_no_change():
    batch = [_candidate("a")]
    tc = _tool_call({"results": [{"candidate_index": 1, "action": "no_change"}]})
    results = _parse_tool_results(tc, batch)
    assert results == [None]


def test_parse_tool_results_edit():
    batch = [_candidate("a")]
    tc = _tool_call({
        "results": [{
            "candidate_index": 1,
            "action": "edit",
            "edits": [{"find": "old text", "replace": "new text"}],
        }]
    })
    results = _parse_tool_results(tc, batch)
    assert results == [[TextEdit(find="old text", replace="new text")]]


def test_parse_tool_results_mixed():
    batch = [_candidate("a"), _candidate("b"), _candidate("c")]
    tc = _tool_call({
        "results": [
            {"candidate_index": 1, "action": "irrelevant"},
            {"candidate_index": 2, "action": "no_change"},
            {"candidate_index": 3, "action": "edit", "edits": [{"find": "old", "replace": "new"}]},
        ]
    })
    results = _parse_tool_results(tc, batch)
    assert results[0] == IRRELEVANT_SENTINEL
    assert results[1] is None
    assert results[2] == [TextEdit(find="old", replace="new")]


def test_parse_tool_results_missing_index_defaults_to_irrelevant():
    batch = [_candidate("a"), _candidate("b")]
    tc = _tool_call({"results": [{"candidate_index": 1, "action": "no_change"}]})
    results = _parse_tool_results(tc, batch)
    assert results[0] is None
    assert results[1] == IRRELEVANT_SENTINEL


def test_parse_tool_results_empty_results_all_irrelevant():
    batch = [_candidate("a"), _candidate("b")]
    tc = _tool_call({"results": []})
    results = _parse_tool_results(tc, batch)
    assert results == [IRRELEVANT_SENTINEL, IRRELEVANT_SENTINEL]


def test_parse_tool_results_edit_with_no_edits_warns(caplog):
    batch = [_candidate("a")]
    tc = _tool_call({"results": [{"candidate_index": 1, "action": "edit", "edits": []}]})
    with caplog.at_level(logging.WARNING, logger="app.llm.agents.ingest_batch_reconciler"):
        results = _parse_tool_results(tc, batch)
    assert results == [[]]
    assert "no valid edits" in caplog.text


def test_parse_tool_results_wrong_tool_name_returns_all_irrelevant(caplog):
    batch = [_candidate("a"), _candidate("b")]
    tc = ToolCall(id="call_1", name="wrong_tool", arguments={"results": []})
    with caplog.at_level(logging.WARNING, logger="app.llm.agents.ingest_batch_reconciler"):
        results = _parse_tool_results(tc, batch)
    assert results == [IRRELEVANT_SENTINEL, IRRELEVANT_SENTINEL]
    assert "unexpected tool call" in caplog.text


def test_parse_tool_results_unknown_action_defaults_to_irrelevant(caplog):
    batch = [_candidate("a")]
    tc = _tool_call({"results": [{"candidate_index": 1, "action": "rewrite"}]})
    with caplog.at_level(logging.WARNING, logger="app.llm.agents.ingest_batch_reconciler"):
        results = _parse_tool_results(tc, batch)
    assert results == [IRRELEVANT_SENTINEL]
    assert "unknown action" in caplog.text


def test_parse_tool_results_multiple_edits():
    batch = [_candidate("a")]
    tc = _tool_call({
        "results": [{
            "candidate_index": 1,
            "action": "edit",
            "edits": [
                {"find": "first", "replace": "FIRST"},
                {"find": "second", "replace": "SECOND"},
            ],
        }]
    })
    results = _parse_tool_results(tc, batch)
    assert results == [[TextEdit("first", "FIRST"), TextEdit("second", "SECOND")]]


# --------------------------------------------------------------------------- #
# batch_reconcile                                                              #
# --------------------------------------------------------------------------- #


@patch("app.llm.agents.ingest_batch_reconciler.load_prompt", return_value="p")
@patch("app.llm.agents.ingest_batch_reconciler.client")
def test_reconcile_returns_results(mock_client, _mock_prompt):
    a = _candidate("a")
    b = _candidate("b")
    c = _candidate("c", "current content")
    mock_client.complete.return_value = _llm_response({
        "results": [
            {"candidate_index": 1, "action": "irrelevant"},
            {"candidate_index": 2, "action": "no_change"},
            {"candidate_index": 3, "action": "edit", "edits": [
                {"find": "current content", "replace": "updated content"}
            ]},
        ]
    })

    results, llm_calls = batch_reconcile(
        title="T", url="", content="doc", source="s", candidates=[a, b, c], model="m"
    )

    assert results[0] == IRRELEVANT_SENTINEL
    assert results[1] is None
    assert results[2] == "updated content"
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
def test_reconcile_fails_open_when_no_tool_call(mock_client, _mock_prompt):
    candidates = [_candidate("a"), _candidate("b")]
    mock_client.complete.return_value = _llm_no_tool()

    results, llm_calls = batch_reconcile(
        title=None, url="", content="doc", source="s", candidates=candidates, model="m"
    )

    assert results == [IRRELEVANT_SENTINEL, IRRELEVANT_SENTINEL]
    assert llm_calls == 1


@patch("app.llm.agents.ingest_batch_reconciler.load_prompt", return_value="p")
@patch("app.llm.agents.ingest_batch_reconciler.client")
def test_reconcile_find_not_in_body_returns_none(mock_client, _mock_prompt):
    candidates = [_candidate("a", "actual body")]
    mock_client.complete.return_value = _llm_response({
        "results": [{
            "candidate_index": 1,
            "action": "edit",
            "edits": [{"find": "text not in body", "replace": "replacement"}],
        }]
    })

    results, _ = batch_reconcile(
        title=None, url="", content="doc", source="s", candidates=candidates, model="m"
    )

    assert results[0] is None


@patch("app.llm.agents.ingest_batch_reconciler.load_prompt", return_value="p")
@patch("app.llm.agents.ingest_batch_reconciler.client")
def test_reconcile_empty_edit_list_warns_and_returns_none(mock_client, _mock_prompt):
    candidates = [_candidate("a", "body text")]
    mock_client.complete.return_value = _llm_response({
        "results": [{"candidate_index": 1, "action": "edit", "edits": []}]
    })

    with patch("app.llm.agents.ingest_batch_reconciler.log") as mock_log:
        results, _ = batch_reconcile(
            title=None, url="", content="doc", source="s", candidates=candidates, model="m"
        )

    assert results[0] is None
    mock_log.warning.assert_called()


@patch("app.llm.agents.ingest_batch_reconciler.load_prompt", return_value="p")
@patch("app.llm.agents.ingest_batch_reconciler.client")
def test_reconcile_merges_multiple_batches(mock_client, _mock_prompt):
    a = _candidate("a", "start " + "x" * 110_000)
    b = _candidate("b", "y" * 110_000)
    mock_client.complete.side_effect = [
        _llm_response({"results": [{"candidate_index": 1, "action": "edit", "edits": [
            {"find": "start", "replace": "updated"}
        ]}]}),
        _llm_response({"results": [{"candidate_index": 1, "action": "no_change"}]}),
    ]

    results, llm_calls = batch_reconcile(
        title=None, url="", content="", source="s", candidates=[a, b], model="m"
    )

    assert results[0] == "updated " + "x" * 110_000
    assert results[1] is None
    assert llm_calls == 2


@patch("app.llm.agents.ingest_batch_reconciler.load_prompt", return_value="p")
@patch("app.llm.agents.ingest_batch_reconciler.client")
def test_reconcile_one_batch_fails_open_other_succeeds(mock_client, _mock_prompt):
    a = _candidate("a", "x" * 110_000)
    b = _candidate("b", "y" * 110_000)
    mock_client.complete.side_effect = [
        RuntimeError("timeout"),
        _llm_response({"results": [{"candidate_index": 1, "action": "no_change"}]}),
    ]

    results, llm_calls = batch_reconcile(
        title=None, url="", content="", source="s", candidates=[a, b], model="m"
    )

    assert results == [IRRELEVANT_SENTINEL, None]
    assert llm_calls == 2


@patch("app.llm.agents.ingest_batch_reconciler.load_prompt", return_value="p")
@patch("app.llm.agents.ingest_batch_reconciler.client")
def test_reconcile_passes_tool_to_client(mock_client, _mock_prompt):
    mock_client.complete.return_value = _llm_response({
        "results": [{"candidate_index": 1, "action": "irrelevant"}]
    })

    batch_reconcile(
        title=None, url="", content="doc", source="s", candidates=[_candidate("a")], model="m"
    )

    call_kwargs = mock_client.complete.call_args.kwargs
    assert call_kwargs.get("tools") is not None
    assert call_kwargs["tools"][0]["name"] == "submit_results"


# --------------------------------------------------------------------------- #
# Per-page update instruction rendering (real prompt)                          #
# --------------------------------------------------------------------------- #


@patch("app.llm.agents.ingest_batch_reconciler.client")
def test_update_instruction_rendered_in_prompt(mock_client):
    # Real load_prompt so the {candidates} block is actually rendered.
    mock_client.complete.return_value = _llm_response(
        {"results": [{"candidate_index": 1, "action": "no_change"}]}
    )
    cand = WikiUpdateCandidate(
        hit=_hit("page.md"), body="body", update_instruction="Keep it terse."
    )
    batch_reconcile(
        title="T", url="", content="doc", source="s", candidates=[cand], model="m"
    )
    user_msg = mock_client.complete.call_args.kwargs["messages"][1]["content"]
    assert "Update instruction for this page: Keep it terse." in user_msg


@patch("app.llm.agents.ingest_batch_reconciler.client")
def test_today_date_rendered_in_prompt(mock_client):
    # Real load_prompt so the doc header block is actually rendered.
    mock_client.complete.return_value = _llm_response(
        {"results": [{"candidate_index": 1, "action": "no_change"}]}
    )
    before = today_str()
    batch_reconcile(
        title="T", url="", content="doc", source="s",
        candidates=[_candidate("page.md")], model="m",
    )
    after = today_str()
    user_msg = mock_client.complete.call_args.kwargs["messages"][1]["content"]
    assert f"Today's date: {before}" in user_msg or f"Today's date: {after}" in user_msg


@patch("app.llm.agents.ingest_batch_reconciler.client")
def test_no_instruction_line_when_absent(mock_client):
    mock_client.complete.return_value = _llm_response(
        {"results": [{"candidate_index": 1, "action": "no_change"}]}
    )
    batch_reconcile(
        title="T", url="", content="doc", source="s",
        candidates=[_candidate("page.md")], model="m",
    )
    user_msg = mock_client.complete.call_args.kwargs["messages"][1]["content"]
    assert "Update instruction for this page" not in user_msg


# --------------------------------------------------------------------------- #
# Source metadata rendering                                                    #
# --------------------------------------------------------------------------- #


def test_format_metadata_empty_and_none():
    assert _format_metadata(None) == ""
    assert _format_metadata({}) == ""
    assert _format_metadata({"k": []}) == ""


def test_format_metadata_joins_lists_and_collapses_whitespace():
    block = _format_metadata({
        "merged": ["True"],
        "labels": ["bug", "p0"],
        "notes": "line one\nline two",
    })
    assert block == (
        "Document metadata:\n"
        "merged: True\n"
        "labels: bug, p0\n"
        "notes: line one line two"
    )


def test_format_metadata_drops_sensitive_keys():
    block = _format_metadata({
        "state": "open",
        "access_token": "ghp_abc123",
        "Webhook-Secret": "shh",
        "API_KEY": "k",
        "session_id": "s",
    })
    assert block == "Document metadata:\nstate: open"


def test_format_metadata_strips_url_query_strings():
    block = _format_metadata({
        "download": "https://s3.example.com/f.pdf?X-Amz-Signature=abc&X-Amz-Credential=xyz",
    })
    assert block == "Document metadata:\ndownload: https://s3.example.com/f.pdf"


def test_format_metadata_collapses_whitespace_in_keys():
    # A key with embedded newlines must not split the block into extra
    # header-like lines the model would read as separate fields.
    block = _format_metadata({"merged: True\nstate": "open"})
    assert block == "Document metadata:\nmerged: True state: open"


def test_format_metadata_truncates_oversized_block():
    block = _format_metadata({"k": "x" * (2 * _METADATA_MAX_CHARS)})
    assert block.endswith("… (truncated)")
    assert len(block) <= _METADATA_MAX_CHARS + len("… (truncated)")


@patch("app.llm.agents.ingest_batch_reconciler.client")
def test_metadata_rendered_in_prompt(mock_client):
    # Real load_prompt so the doc header block is actually rendered.
    mock_client.complete.return_value = _llm_response(
        {"results": [{"candidate_index": 1, "action": "no_change"}]}
    )
    batch_reconcile(
        title="123: Fix flaky test", url="https://github.com/o/r/pull/123",
        content="PR body", source="github",
        candidates=[_candidate("page.md")], model="m",
        metadata={
            "object_type": ["PullRequest"],
            "merged": ["True"],
            "user": ["{'login': 'roshan'}"],
        },
    )
    user_msg = mock_client.complete.call_args.kwargs["messages"][1]["content"]
    assert "Document metadata:" in user_msg
    assert "merged: True" in user_msg
    assert "user: {'login': 'roshan'}" in user_msg


@patch("app.llm.agents.ingest_batch_reconciler.client")
def test_no_metadata_block_when_absent(mock_client):
    mock_client.complete.return_value = _llm_response(
        {"results": [{"candidate_index": 1, "action": "no_change"}]}
    )
    batch_reconcile(
        title="T", url="", content="doc", source="s",
        candidates=[_candidate("page.md")], model="m",
    )
    user_msg = mock_client.complete.call_args.kwargs["messages"][1]["content"]
    assert "Document metadata:" not in user_msg
