"""Tests for the document ingest pipeline.

Covers:
  - app.ingest.source_tiers  — is_filtered()
  - app.ingest.search        — title boost, score threshold, ranking
  - app.tasks.wiki_update.process_pushed_document
      - filtered source drop
      - no BM25 candidates
      - IRRELEVANT / NO_CHANGE / new body outputs
      - N-consecutive IRRELEVANT stop rule
      - observability counters
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.config import CONFIG
from app.db.fts import SearchHit
from app.ingest import search as ingest_search
from app.ingest.settings import IngestSettings
from app.ingest.source_tiers import is_filtered
from app.llm.agents.common import IRRELEVANT_SENTINEL
from app.llm.settings import _EMPTY as _EMPTY_LLM_SETTINGS, LLMSettings
from app.wiki import constants as wiki_constants
from app.wiki import git as wiki_git
from app.wiki.utils import author_string


@pytest.fixture(autouse=True)
def _stub_llm_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """process_pushed_document calls get_llm_settings() which hits the DB.
    Return empty settings so the selector and batch-reconciler stages are
    skipped in unit tests that don't need them."""
    monkeypatch.setattr(
        "app.tasks.wiki_update.get_llm_settings",
        lambda: _EMPTY_LLM_SETTINGS,
    )


@pytest.fixture(autouse=True)
def _stub_resolve_policies(monkeypatch: pytest.MonkeyPatch) -> None:
    """The candidate loop calls update_policy.resolve_for_paths, which opens a DB
    session. These unit tests mock git/search and run without a DB, so default to
    "no policy" (enabled, no instruction). Policy-specific tests override this."""
    monkeypatch.setattr(
        "app.tasks.wiki_update.update_policy.resolve_for_paths",
        lambda paths: {},
    )


def _ingest_settings(*, auto_update_cap: int) -> IngestSettings:
    return IngestSettings(
        max_doc_chars=100_000,
        api_key=None,
        onyx_base_url=None,
        warn_update_threshold_default=10,
        auto_update_cap=auto_update_cap,
    )


@pytest.fixture(autouse=True)
def _stub_ingest_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """The candidate loop reads the admin cap via ingest_settings.get() (a DB
    read). Default to cap=0 (no cap) so the no-DB unit tests are unaffected;
    cap-specific tests override."""
    monkeypatch.setattr(
        "app.tasks.wiki_update.ingest_settings.get",
        lambda: _ingest_settings(auto_update_cap=0),
    )


def _settings_with_model(model: str = "test-model") -> LLMSettings:
    return _EMPTY_LLM_SETTINGS.model_copy(update={"model": model})


# --------------------------------------------------------------------------- #
# source_tiers                                                                 #
# --------------------------------------------------------------------------- #


def test_is_filtered_none_source():
    assert is_filtered(None) is False


def test_is_filtered_unknown_source():
    assert is_filtered("confluence") is False


def test_is_filtered_known_filtered_source(monkeypatch):
    monkeypatch.setattr("app.ingest.source_tiers.FILTERED_SOURCES", frozenset({"git_commit"}))
    assert is_filtered("git_commit") is True


def test_is_filtered_case_sensitive(monkeypatch):
    monkeypatch.setattr("app.ingest.source_tiers.FILTERED_SOURCES", frozenset({"git_commit"}))
    assert is_filtered("Git_Commit") is False


# --------------------------------------------------------------------------- #
# ingest.search — title boost + threshold                                      #
# --------------------------------------------------------------------------- #


def _hit(path: str, title: str | None, score: float) -> SearchHit:
    return SearchHit(doc_id=path, path=path, title=title, snippet="", score=score)


def _cs(
    passed: list[SearchHit],
    dropped: list[SearchHit] | None = None,
    rank_dropped: list[SearchHit] | None = None,
) -> ingest_search.CandidateSearch:
    """Build a CandidateSearch for mocking ingest_search.candidates()."""
    return ingest_search.CandidateSearch(
        passed=passed, dropped=dropped or [], rank_dropped=rank_dropped or []
    )


def test_candidates_empty_when_no_fts_results():
    with patch("app.ingest.search.fts_search", return_value=[]):
        result = ingest_search.candidates("some content", "Some Title")
    assert result.passed == [] and result.dropped == []


def _outcome_count(outcome: str, wiki_path: str) -> float:
    from prometheus_client import REGISTRY
    return REGISTRY.get_sample_value(
        "ingest_outcomes_total", {"outcome": outcome, "wiki_path": wiki_path}
    ) or 0.0


def test_candidates_partitions_on_min_score(monkeypatch):
    monkeypatch.setattr(ingest_search, "CONFIG", CONFIG.model_copy(update={"ingest_bm25_min_score": 5.0}))
    hits = [_hit("a.md", "Alpha", 3.0), _hit("b.md", "Beta", 6.0)]
    with patch("app.ingest.search.fts_search", return_value=hits):
        result = ingest_search.candidates("content", None)
    # candidates() is pure: it partitions passed/dropped and emits no drop
    # outcome metric — that's the caller's post-process.
    assert [h.path for h in result.passed] == ["b.md"]
    assert [h.path for h in result.dropped] == ["a.md"]


def test_candidates_sorted_descending_by_score(monkeypatch):
    monkeypatch.setattr(ingest_search, "CONFIG", CONFIG.model_copy(update={"ingest_bm25_min_score": 0.0}))
    hits = [_hit("low.md", None, 1.0), _hit("high.md", None, 5.0), _hit("mid.md", None, 3.0)]
    with patch("app.ingest.search.fts_search", return_value=hits):
        result = ingest_search.candidates("content", None)
    assert [h.path for h in result.passed] == ["high.md", "mid.md", "low.md"]


def test_title_boost_raises_score(monkeypatch):
    monkeypatch.setattr(ingest_search, "CONFIG", CONFIG.model_copy(update={"ingest_bm25_min_score": 0.0, "ingest_bm25_title_boost": 4.0}))
    # "deploy guide" has perfect Jaccard overlap with incoming title
    hits = [_hit("deploy.md", "deploy guide", 2.0), _hit("other.md", "unrelated", 2.0)]
    with patch("app.ingest.search.fts_search", return_value=hits):
        result = ingest_search.candidates("content", "deploy guide")
    assert result.passed[0].path == "deploy.md"
    assert result.passed[0].score > result.passed[1].score


def test_title_boost_zero_when_no_incoming_title(monkeypatch):
    monkeypatch.setattr(ingest_search, "CONFIG", CONFIG.model_copy(update={"ingest_bm25_min_score": 0.0, "ingest_bm25_title_boost": 4.0}))
    hits = [_hit("a.md", "deploy guide", 2.0), _hit("b.md", "other", 3.0)]
    with patch("app.ingest.search.fts_search", return_value=hits):
        result = ingest_search.candidates("content", None)
    # no boost applied — sorted purely by BM25 score
    assert result.passed[0].path == "b.md"


def test_title_boost_threshold_interaction(monkeypatch):
    monkeypatch.setattr(ingest_search, "CONFIG", CONFIG.model_copy(update={"ingest_bm25_min_score": 5.0, "ingest_bm25_title_boost": 4.0}))
    # raw score 3.0 is below threshold, but title boost should push it over
    hits = [_hit("a.md", "deploy guide", 3.0)]
    with patch("app.ingest.search.fts_search", return_value=hits):
        result = ingest_search.candidates("content", "deploy guide")
    assert len(result.passed) == 1


def test_candidates_no_rank_dropped_without_fetch_limit(monkeypatch):
    # Default fetch: fts_search is asked for exactly the top-N cap, so there's
    # no tail beyond it — rank_dropped is empty (production behavior unchanged).
    monkeypatch.setattr(ingest_search, "CONFIG", CONFIG.model_copy(update={"ingest_bm25_min_score": 0.0, "ingest_bm25_limit": 2}))
    hits = [_hit("a.md", None, 9.0), _hit("b.md", None, 8.0)]
    with patch("app.ingest.search.fts_search", return_value=hits) as m:
        result = ingest_search.candidates("content", None)
    assert m.call_args.kwargs["limit"] == 2
    assert result.rank_dropped == []


def test_candidates_rank_dropped_beyond_cap(monkeypatch):
    # A wider fetch_limit pulls hits beyond the top-N cap; the top-N still go to
    # the pipeline, the rest land in rank_dropped (most relevant first).
    monkeypatch.setattr(ingest_search, "CONFIG", CONFIG.model_copy(update={"ingest_bm25_min_score": 0.0, "ingest_bm25_limit": 2}))
    hits = [_hit("a.md", None, 9.0), _hit("b.md", None, 8.0), _hit("c.md", None, 7.0), _hit("d.md", None, 6.0)]
    with patch("app.ingest.search.fts_search", return_value=hits) as m:
        result = ingest_search.candidates("content", None, fetch_limit=10)
    # fetch_limit is passed through to the search backend.
    assert m.call_args.kwargs["limit"] == 10
    assert [h.path for h in result.passed] == ["a.md", "b.md"]
    assert [h.path for h in result.rank_dropped] == ["c.md", "d.md"]


# --------------------------------------------------------------------------- #
# process_pushed_document                                                      #
# --------------------------------------------------------------------------- #


def _make_push(**kwargs: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "content": "some content",
        "title": "Test Doc",
        "source": "confluence",
        "source_document_id": "doc-abc",
        "metadata": {},
    }
    base.update(kwargs)
    return base


def _run(push: dict[str, Any]) -> None:
    from app.tasks.wiki_update import process_pushed_document
    # Run synchronously via immediate_mode
    from app.tasks.queues import documents_queue
    with documents_queue.immediate_mode():
        process_pushed_document(push)


@patch("app.tasks.wiki_update.ingest_search.candidates", return_value=_cs([]))
def test_filtered_source_drops_silently(mock_search, monkeypatch):
    monkeypatch.setattr("app.ingest.source_tiers.FILTERED_SOURCES", frozenset({"git_commit"}))
    _run(_make_push(source="git_commit"))
    mock_search.assert_not_called()


def _doc_result_count(result: str) -> float:
    from prometheus_client import REGISTRY
    return REGISTRY.get_sample_value(
        "ingest_document_results_total", {"result": result}
    ) or 0.0


@patch("app.tasks.wiki_update.ingest_search.candidates", return_value=_cs([]))
def test_no_candidates_returns_early(mock_search, monkeypatch):
    # No BM25 hit -> the doc still gets a per-document terminal result so the
    # "search filtered out" tile accounts for it (panel reconciles to ingested).
    monkeypatch.setattr(
        "app.tasks.wiki_update.CONFIG",
        CONFIG.model_copy(update={"ingest_eval_logging": False}),
    )
    before = _doc_result_count("no_candidates")
    _run(_make_push())
    mock_search.assert_called_once()
    # Eval logging off: the candidate fetch is not widened.
    assert mock_search.call_args.kwargs["fetch_limit"] is None
    assert _doc_result_count("no_candidates") - before == 1.0


@patch("app.tasks.wiki_update.wiki_git.read_file", side_effect=OSError("unreadable"))
@patch("app.tasks.wiki_update.ingest_search.candidates")
def test_no_readable_candidates_records_search_filtered_out(mock_search, mock_read):
    # Hits exist but none are readable -> also recorded as no_candidates.
    mock_search.return_value = _cs([_hit("page.md", "Page", 5.0)])
    before = _doc_result_count("no_candidates")
    _run(_make_push())
    assert _doc_result_count("no_candidates") - before == 1.0


def test_candidates_raises_on_search_backend_error():
    # A backend failure (e.g. OpenSearch rejecting an oversized query) must
    # surface as IngestSearchError, not be swallowed into an empty result.
    boom = RuntimeError("maxClauseCount is set to 1024")
    with patch("app.ingest.search.fts_search", side_effect=boom):
        with pytest.raises(ingest_search.IngestSearchError):
            ingest_search.candidates("a very long transcript", "Big Meeting")


@patch("app.tasks.wiki_update.wiki_git.commit_file")
@patch("app.tasks.wiki_update.ingest_search.candidates",
       side_effect=ingest_search.IngestSearchError("maxClauseCount is set to 1024"))
def test_search_error_is_caught_and_does_not_commit(mock_search, mock_commit):
    # A search failure is logged and the document dropped — it must be caught
    # (not raised out of the task) and must never commit.
    _run(_make_push())
    mock_commit.assert_not_called()


@patch("app.tasks.wiki_update.wiki_git.head_sha_for_path", return_value="headsha")
@patch("app.wiki.utils.wiki_notify.after_doc_write")
@patch("app.tasks.wiki_update.wiki_git.commit_file", return_value="sha123")
@patch("app.tasks.wiki_update.wiki_git.read_file", return_value="old body")
@patch("app.tasks.wiki_update.ingest_batch_reconciler.batch_reconcile")
@patch("app.tasks.wiki_update.ingest_search.candidates")
def test_new_body_commits(mock_search, mock_reconcile, mock_read, mock_commit, mock_notify, mock_head, monkeypatch):
    monkeypatch.setattr("app.tasks.wiki_update.get_llm_settings", lambda: _settings_with_model())
    mock_search.return_value = _cs([_hit("page.md", "Page", 5.0)])
    mock_reconcile.return_value = (["new body"], 1)
    _run(_make_push())
    mock_commit.assert_called_once()
    mock_notify.assert_called_once()


@patch("app.tasks.wiki_update.wiki_git.head_sha_for_path", return_value="headsha")
@patch("app.wiki.utils.wiki_notify.after_doc_write")
@patch("app.tasks.wiki_update.wiki_git.commit_file", return_value="sha123")
@patch("app.tasks.wiki_update.wiki_git.read_file", return_value="old body")
@patch("app.tasks.wiki_update.ingest_batch_reconciler.batch_reconcile")
@patch("app.tasks.wiki_update.ingest_search.candidates")
def test_commit_attributed_to_onyx_ingest(
    mock_search, mock_reconcile, mock_read, mock_commit, mock_notify, mock_head, monkeypatch
):
    # No human user is bound on the ingest path, so the commit author would
    # otherwise fall back to "AI Wiki Helper". process_pushed_document binds the
    # Onyx Ingest identity for the run instead.
    monkeypatch.setattr("app.tasks.wiki_update.get_llm_settings", lambda: _settings_with_model())
    mock_search.return_value = _cs([_hit("page.md", "Page", 5.0)])
    mock_reconcile.return_value = (["new body"], 1)
    _run(_make_push())
    assert mock_commit.call_args.kwargs["author"] == wiki_constants.INGEST_AUTHOR
    # And the binding is unwound after the run — no leak into later tasks.
    assert author_string() == "AI Wiki Helper <ai-wiki-helper@local>"


@patch("app.tasks.wiki_update.wiki_git.commit_file")
@patch("app.tasks.wiki_update.wiki_git.read_file", return_value="body")
@patch("app.tasks.wiki_update.ingest_batch_reconciler.batch_reconcile")
@patch("app.tasks.wiki_update.ingest_search.candidates")
def test_no_change_does_not_commit(mock_search, mock_reconcile, mock_read, mock_commit, monkeypatch):
    monkeypatch.setattr("app.tasks.wiki_update.get_llm_settings", lambda: _settings_with_model())
    mock_search.return_value = _cs([_hit("page.md", "Page", 5.0)])
    mock_reconcile.return_value = ([None], 1)
    _run(_make_push())
    mock_commit.assert_not_called()


@patch("app.tasks.wiki_update.wiki_git.commit_file")
@patch("app.tasks.wiki_update.wiki_git.read_file", return_value="body")
@patch("app.tasks.wiki_update.ingest_batch_reconciler.batch_reconcile")
@patch("app.tasks.wiki_update.ingest_search.candidates")
def test_irrelevant_does_not_commit(mock_search, mock_reconcile, mock_read, mock_commit, monkeypatch):
    monkeypatch.setattr("app.tasks.wiki_update.get_llm_settings", lambda: _settings_with_model())
    mock_search.return_value = _cs([_hit("page.md", "Page", 5.0)])
    mock_reconcile.return_value = ([IRRELEVANT_SENTINEL], 1)
    _run(_make_push())
    mock_commit.assert_not_called()


@patch("app.tasks.wiki_update.ingest_eval_sample.log_sample")
@patch("app.llm.client.complete")
@patch("app.tasks.wiki_update.wiki_git.read_file", return_value="body")
@patch("app.tasks.wiki_update.ingest_search.candidates")
def test_omitted_candidate_recorded_as_irrelevant(
    mock_search, mock_read, mock_complete, mock_log, monkeypatch
):
    # The reconciler now omits irrelevant candidates from its tool call (only
    # edit/no_change are emitted). This drives the real _parse_tool_results +
    # post-process: an omitted candidate must still land as an `irrelevant`
    # outcome — same metric + eval row as an explicit irrelevant — so DB logging
    # and accounting stay correct.
    monkeypatch.setattr("app.tasks.wiki_update.get_llm_settings", lambda: _settings_with_model())
    monkeypatch.setattr(
        "app.tasks.wiki_update.CONFIG",
        CONFIG.model_copy(update={"ingest_eval_logging": True}),
    )
    from app.llm.client import ToolCall

    mock_search.return_value = _cs([_hit("kept.md", "Kept", 6.0), _hit("omitted.md", "Omitted", 5.0)])
    # Model reports only candidate 1 (no_change); candidate 2 is omitted entirely.
    resp = MagicMock()
    resp.text = ""
    resp.tool_calls = [
        ToolCall(
            id="c1",
            name="submit_results",
            arguments={"results": [{"candidate_index": 1, "action": "no_change"}]},
        )
    ]
    resp.usage.input_tokens = 100
    resp.usage.output_tokens = 10
    resp.usage.cached_input_tokens = 0
    resp.usage.uncached_input_tokens = 100
    mock_complete.return_value = resp

    before = _outcome_count("irrelevant", "omitted.md")
    _run(_make_push())

    assert _outcome_count("irrelevant", "omitted.md") - before == 1.0
    logged = {(c.kwargs["wiki_path"], c.kwargs["outcome"]) for c in mock_log.call_args_list}
    assert ("omitted.md", "irrelevant") in logged
    assert ("kept.md", "no_change") in logged


@patch("app.tasks.wiki_update.wiki_git.commit_file")
@patch("app.tasks.wiki_update.ingest_batch_reconciler.batch_reconcile")
@patch("app.tasks.wiki_update.ingest_search.candidates")
def test_ingestion_disabled_skips_candidate_before_llm(
    mock_search, mock_reconcile, mock_commit, monkeypatch
):
    # A page whose policy disables ingestion auto-update must be dropped before
    # any LLM call — the reconciler never sees it and nothing commits.
    monkeypatch.setattr("app.tasks.wiki_update.get_llm_settings", lambda: _settings_with_model())
    from app.wiki.update_policy import ResolvedPolicy

    monkeypatch.setattr(
        "app.tasks.wiki_update.update_policy.resolve_for_paths",
        lambda paths: {"page.md": ResolvedPolicy(ingestion_auto_update_disabled=True)},
    )
    mock_search.return_value = _cs([_hit("page.md", "Page", 5.0)])
    _run(_make_push())
    mock_reconcile.assert_not_called()
    mock_commit.assert_not_called()


@patch("app.tasks.wiki_update.wiki_git.commit_file")
@patch("app.tasks.wiki_update.ingest_batch_reconciler.batch_reconcile")
@patch("app.tasks.wiki_update.ingest_search.candidates")
def test_over_cap_skips_candidate_before_llm(
    mock_search, mock_reconcile, mock_commit, monkeypatch
):
    # A page that already hit the admin cap in the trailing 24h is dropped before
    # any LLM call — the reconciler never sees it and nothing commits (no tokens).
    monkeypatch.setattr(
        "app.tasks.wiki_update.ingest_settings.get",
        lambda: _ingest_settings(auto_update_cap=3),
    )
    monkeypatch.setattr(
        "app.tasks.wiki_update.wiki_git.ingest_update_times_24h",
        lambda _: [1, 2, 3],  # 3 updates in window >= cap 3
    )
    mock_search.return_value = _cs([_hit("page.md", "Page", 5.0)])
    _run(_make_push())
    mock_reconcile.assert_not_called()
    mock_commit.assert_not_called()


@patch("app.tasks.wiki_update.wiki_git.head_sha_for_path", return_value="headsha")
@patch("app.wiki.utils.wiki_notify.after_doc_write")
@patch("app.tasks.wiki_update.wiki_git.commit_file", return_value="sha123")
@patch("app.tasks.wiki_update.wiki_git.read_file", return_value="old body")
@patch("app.tasks.wiki_update.ingest_batch_reconciler.batch_reconcile")
@patch("app.tasks.wiki_update.ingest_search.candidates")
def test_update_instruction_passed_to_reconciler(
    mock_search, mock_reconcile, mock_read, mock_commit, mock_notify, mock_head, monkeypatch
):
    # The resolved per-page instruction rides onto the candidate the reconciler sees.
    monkeypatch.setattr("app.tasks.wiki_update.get_llm_settings", lambda: _settings_with_model())
    from app.wiki.update_policy import ResolvedPolicy

    monkeypatch.setattr(
        "app.tasks.wiki_update.update_policy.resolve_for_paths",
        lambda paths: {"page.md": ResolvedPolicy(update_instruction="Keep it terse.")},
    )
    mock_search.return_value = _cs([_hit("page.md", "Page", 5.0)])
    mock_reconcile.return_value = (["new body"], 1)
    _run(_make_push())
    candidates = mock_reconcile.call_args.kwargs["candidates"]
    assert candidates[0].update_instruction == "Keep it terse."


@patch("app.wiki.utils.wiki_notify.after_doc_write")
@patch("app.tasks.wiki_update.wiki_git.commit_file", return_value="sha")
@patch("app.tasks.wiki_update.wiki_git.read_file", return_value="body")
@patch("app.tasks.wiki_update.ingest_batch_reconciler.batch_reconcile")
@patch("app.tasks.wiki_update.ingest_search.candidates")
def test_n_consecutive_irrelevant_stops_loop(mock_search, mock_reconcile, mock_read, mock_commit, mock_notify, monkeypatch):
    monkeypatch.setattr("app.tasks.wiki_update.CONFIG", CONFIG.model_copy(update={"ingest_irrelevant_stop_n": 2}))
    monkeypatch.setattr("app.tasks.wiki_update.get_llm_settings", lambda: _settings_with_model())
    # 5 candidates — first two are IRRELEVANT, rest would commit but never reached
    mock_search.return_value = _cs([_hit(f"p{i}.md", f"P{i}", float(5 - i)) for i in range(5)])
    mock_reconcile.return_value = ([IRRELEVANT_SENTINEL, IRRELEVANT_SENTINEL, "new", "new", "new"], 1)
    _run(_make_push())
    mock_commit.assert_not_called()


@patch("app.wiki.utils.wiki_notify.after_doc_write")
@patch("app.tasks.wiki_update.wiki_git.commit_file", return_value="sha")
@patch("app.tasks.wiki_update.wiki_git.read_file", return_value="body")
@patch("app.tasks.wiki_update.ingest_batch_reconciler.batch_reconcile")
@patch("app.tasks.wiki_update.ingest_search.candidates")
def test_no_change_resets_irrelevant_counter(mock_search, mock_reconcile, mock_read, mock_commit, mock_notify, monkeypatch):
    monkeypatch.setattr("app.tasks.wiki_update.CONFIG", CONFIG.model_copy(update={"ingest_irrelevant_stop_n": 2}))
    monkeypatch.setattr("app.tasks.wiki_update.get_llm_settings", lambda: _settings_with_model())
    # IRRELEVANT, NO_CHANGE (resets counter), IRRELEVANT — should NOT stop early
    mock_search.return_value = _cs([_hit(f"p{i}.md", None, float(5 - i)) for i in range(3)])
    mock_reconcile.return_value = ([IRRELEVANT_SENTINEL, None, IRRELEVANT_SENTINEL], 1)
    _run(_make_push())
    mock_commit.assert_not_called()


@patch("app.tasks.wiki_update.wiki_git.head_sha_for_path", return_value="headsha")
@patch("app.wiki.utils.wiki_notify.after_doc_write")
@patch("app.tasks.wiki_update.wiki_git.commit_file", return_value="sha")
@patch("app.tasks.wiki_update.wiki_git.read_file", return_value="body")
@patch("app.tasks.wiki_update.ingest_batch_reconciler.batch_reconcile")
@patch("app.tasks.wiki_update.ingest_search.candidates")
def test_commit_resets_irrelevant_counter(mock_search, mock_reconcile, mock_read, mock_commit, mock_notify, mock_head, monkeypatch):
    monkeypatch.setattr("app.tasks.wiki_update.CONFIG", CONFIG.model_copy(update={"ingest_irrelevant_stop_n": 2}))
    monkeypatch.setattr("app.tasks.wiki_update.get_llm_settings", lambda: _settings_with_model())
    # IRRELEVANT, new body (resets counter), IRRELEVANT — should NOT stop early
    mock_search.return_value = _cs([_hit(f"p{i}.md", None, float(5 - i)) for i in range(3)])
    mock_reconcile.return_value = ([IRRELEVANT_SENTINEL, "new body", IRRELEVANT_SENTINEL], 1)
    _run(_make_push())
    mock_commit.assert_called_once()


@patch("app.tasks.wiki_update.wiki_git.commit_file")
@patch("app.tasks.wiki_update.wiki_git.read_file", side_effect=Exception("file not found"))
@patch("app.tasks.wiki_update.ingest_batch_reconciler.batch_reconcile")
@patch("app.tasks.wiki_update.ingest_search.candidates")
def test_missing_file_skipped(mock_search, mock_reconcile, mock_read, mock_commit, monkeypatch):
    monkeypatch.setattr("app.tasks.wiki_update.get_llm_settings", lambda: _settings_with_model())
    mock_search.return_value = _cs([_hit("missing.md", None, 5.0)])
    _run(_make_push())
    mock_reconcile.assert_not_called()
    mock_commit.assert_not_called()


def _eval_rows(outcome: str) -> list[dict[str, Any]]:
    from sqlalchemy import select as sa_select

    from app.db.models import IngestEvalSample
    from app.db.session import session

    with session() as s:
        return [
            {
                "wiki_path": r.wiki_path,
                "source_document_id": r.source_document_id,
                "bm25_score": r.bm25_score,
            }
            for r in s.execute(
                sa_select(IngestEvalSample).where(IngestEvalSample.outcome == outcome)
            ).scalars().all()
        ]


@patch("app.tasks.wiki_update.ingest_search.candidates")
def test_bm25_drop_recorded_and_eval_logged(mock_search, tmp_repo, monkeypatch):
    # The post-process records a below-threshold candidate: the
    # filtered_by_bm25_score outcome metric + an eval row (body read here).
    wiki_git.commit_file("low.md", "# Low\nbody\n", "seed", author=None)
    monkeypatch.setattr(
        "app.tasks.wiki_update.CONFIG",
        CONFIG.model_copy(update={"ingest_eval_logging": True}),
    )
    mock_search.return_value = _cs([], [_hit("low.md", "Low", 2.0)])
    before = _outcome_count("filtered_by_bm25_score", "low.md")
    _run(_make_push())
    assert _outcome_count("filtered_by_bm25_score", "low.md") - before == 1.0
    rows = _eval_rows("filtered_by_bm25_score")
    assert len(rows) == 1
    assert rows[0]["wiki_path"] == "low.md"
    assert rows[0]["source_document_id"] == "doc-abc"
    assert rows[0]["bm25_score"] == 2.0


@patch("app.tasks.wiki_update.ingest_search.candidates")
def test_rank_drop_recorded_and_eval_logged(mock_search, tmp_repo, monkeypatch):
    # A real search hit beyond the top-N cap is recorded as
    # filtered_by_search_rank, with its BM25 score, when eval logging widens the
    # fetch. The fetch is widened via fetch_limit=EVAL_WIDE_FETCH_LIMIT.
    wiki_git.commit_file("tail.md", "# Tail\nbody\n", "seed", author=None)
    monkeypatch.setattr(
        "app.tasks.wiki_update.CONFIG",
        CONFIG.model_copy(update={"ingest_eval_logging": True}),
    )
    mock_search.return_value = _cs([], rank_dropped=[_hit("tail.md", "Tail", 1.5)])
    before = _outcome_count("filtered_by_search_rank", "tail.md")
    _run(_make_push())
    assert mock_search.call_args.kwargs["fetch_limit"] == ingest_search.EVAL_WIDE_FETCH_LIMIT
    assert _outcome_count("filtered_by_search_rank", "tail.md") - before == 1.0
    rows = _eval_rows("filtered_by_search_rank")
    assert len(rows) == 1
    assert rows[0]["wiki_path"] == "tail.md"
    assert rows[0]["bm25_score"] == 1.5


@patch("app.tasks.wiki_update.ingest_batch_reconciler.batch_reconcile", return_value=([], 0))
@patch("app.tasks.wiki_update.ingest_selector.select_candidates", return_value=[])
@patch("app.tasks.wiki_update.ingest_search.candidates")
def test_selector_drop_eval_logged(
    mock_search, mock_select, mock_reconcile, tmp_repo, monkeypatch
):
    # The selector drops the only candidate; that (doc, page) pair is eval-logged.
    wiki_git.commit_file("page.md", "# Page\nbody\n", "seed", author=None)
    monkeypatch.setattr(
        "app.tasks.wiki_update.get_llm_settings",
        lambda: _EMPTY_LLM_SETTINGS.model_copy(
            update={"model": "main", "ingest_selector_model": "sel"}
        ),
    )
    monkeypatch.setattr(
        "app.tasks.wiki_update.CONFIG",
        CONFIG.model_copy(update={"ingest_eval_logging": True}),
    )
    mock_search.return_value = _cs([_hit("page.md", "Page", 6.0)])
    _run(_make_push())
    rows = _eval_rows("filtered_by_selector")
    assert len(rows) == 1
    assert rows[0]["wiki_path"] == "page.md"


@patch("app.tasks.wiki_update.ingest_search.bounded_query", return_value="term")
@patch("app.tasks.wiki_update.ingest_search.candidates")
def test_fallback_bm25_drops_not_recorded(mock_search, mock_bounded, tmp_repo, monkeypatch):
    # The oversized-query fallback scores against a lossy summary, so its drops
    # must NOT be recorded — no filtered_by_bm25_score metric and no eval row.
    wiki_git.commit_file("low.md", "# Low\nbody\n", "seed", author=None)
    monkeypatch.setattr(
        "app.tasks.wiki_update.CONFIG",
        CONFIG.model_copy(update={"ingest_eval_logging": True}),
    )
    mock_search.side_effect = [
        ingest_search.IngestSearchError("maxClauseCount is set to 1024"),
        _cs([], [_hit("low.md", "Low", 2.0)]),
    ]
    before = _outcome_count("filtered_by_bm25_score", "low.md")
    _run(_make_push(content="x " * 5000))
    assert _outcome_count("filtered_by_bm25_score", "low.md") - before == 0.0
    assert _eval_rows("filtered_by_bm25_score") == []


# --------------------------------------------------------------------------- #
# oversized-query handling (LLM intent + bounded fallback)                    #
# --------------------------------------------------------------------------- #


def test_bounded_query_caps_terms_and_drops_short_tokens():
    content = ("alpha beta gamma " * 50) + "to is a " + ("delta " * 5)
    q = ingest_search.bounded_query(content, max_terms=3)
    terms = q.split()
    assert len(terms) <= 3
    # short tokens (<=2 chars) are dropped
    assert "to" not in terms and "is" not in terms and "a" not in terms
    # most frequent content term is included
    assert "alpha" in terms


def test_generate_search_query_builds_from_intent_fields():
    from types import SimpleNamespace
    from app.ingest import intent as ingest_intent
    payload = '{"summary": "Acme renewal call", "candidate_updates": ["renewed for $24k"], "entities": ["Acme", "$24k"]}'
    with patch("app.ingest.intent.client.complete", return_value=SimpleNamespace(text=payload)):
        q = ingest_intent.generate_search_query(title="Acme", content="...transcript...", model="cheap")
    assert q is not None
    assert "Acme renewal call" in q and "renewed for $24k" in q and "$24k" in q


def test_generate_search_query_returns_none_on_error():
    from app.ingest import intent as ingest_intent
    with patch("app.ingest.intent.client.complete", side_effect=RuntimeError("model down")):
        assert ingest_intent.generate_search_query(title="t", content="c", model="cheap") is None


def test_generate_search_query_strips_markdown_fence():
    from types import SimpleNamespace
    from app.ingest import intent as ingest_intent
    # Many models wrap JSON in a ```json fence — must still parse.
    payload = '```json\n{"summary": "Acme call", "candidate_updates": [], "entities": ["Acme"]}\n```'
    with patch("app.ingest.intent.client.complete", return_value=SimpleNamespace(text=payload)):
        q = ingest_intent.generate_search_query(title="Acme", content="...", model="cheap")
    assert q is not None and "Acme call" in q and "Acme" in q


def test_generate_search_query_caps_term_count():
    import json as _json
    from types import SimpleNamespace
    from app.ingest import intent as ingest_intent
    # Model echoes a huge entities list — the query must stay bounded so it can't
    # re-trip the clause limit on retry.
    payload = _json.dumps({"summary": "many ids", "candidate_updates": [], "entities": [f"id{i:05d}" for i in range(1000)]})
    with patch("app.ingest.intent.client.complete", return_value=SimpleNamespace(text=payload)):
        q = ingest_intent.generate_search_query(title="t", content="c", model="cheap")
    assert q is not None
    assert len(q.split()) <= 200


def _settings_with_selector(model: str = "test-model") -> LLMSettings:
    return _EMPTY_LLM_SETTINGS.model_copy(update={"model": model, "ingest_selector_model": model})


@patch("app.tasks.wiki_update.wiki_git.head_sha_for_path", return_value="headsha")
@patch("app.wiki.utils.wiki_notify.after_doc_write")
@patch("app.tasks.wiki_update.wiki_git.commit_file", return_value="sha")
@patch("app.tasks.wiki_update.wiki_git.read_file", return_value="body")
@patch("app.tasks.wiki_update.ingest_batch_reconciler.batch_reconcile")
@patch("app.tasks.wiki_update.ingest_intent.generate_search_query", return_value="distilled query")
@patch("app.tasks.wiki_update.ingest_search.candidates")
def test_oversized_query_retries_with_llm_intent(
    mock_search, mock_intent, mock_reconcile, mock_read, mock_commit, mock_notify, mock_head, monkeypatch
):
    monkeypatch.setattr("app.tasks.wiki_update.get_llm_settings", lambda: _settings_with_selector())
    # First candidate search blows the clause limit; retry with the distilled
    # query succeeds and the doc commits.
    mock_search.side_effect = [ingest_search.IngestSearchError("maxClauseCount is set to 1024"), _cs([_hit("page.md", "Page", 5.0)])]
    mock_reconcile.return_value = (["new body"], 1)
    _run(_make_push(content="x " * 5000))
    mock_intent.assert_called_once()
    assert mock_search.call_count == 2
    mock_commit.assert_called_once()


@patch("app.tasks.wiki_update.ingest_search.bounded_query", return_value="term1 term2")
@patch("app.tasks.wiki_update.ingest_search.candidates")
def test_oversized_query_falls_back_to_bounded_terms_when_no_model(mock_search, mock_bounded):
    # No selector model configured (autouse _EMPTY settings) → no LLM call,
    # fall back to bounded-terms query and retry.
    mock_search.side_effect = [ingest_search.IngestSearchError("maxClauseCount is set to 1024"), _cs([])]
    _run(_make_push(content="x " * 5000))
    mock_bounded.assert_called_once()
    assert mock_search.call_count == 2
