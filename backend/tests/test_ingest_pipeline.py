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
from unittest.mock import patch

import pytest

from app.config import CONFIG
from app.db.fts import SearchHit
from app.ingest import search as ingest_search
from app.ingest.source_tiers import is_filtered
from app.llm.agents.common import IRRELEVANT_SENTINEL
from app.llm.settings import _EMPTY as _EMPTY_LLM_SETTINGS, LLMSettings
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
def _stub_ingest_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """The candidate loop calls update_policy.is_ingest_disabled, which opens a
    DB session. These unit tests mock git/search and run without a DB, so
    default to 'not disabled'. The policy-specific test overrides this."""
    monkeypatch.setattr(
        "app.tasks.wiki_update.update_policy.is_ingest_disabled",
        lambda path: False,
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


def test_candidates_empty_when_no_fts_results():
    with patch("app.ingest.search.fts_search", return_value=[]):
        result = ingest_search.candidates("some content", "Some Title")
    assert result == []


def test_candidates_drops_below_min_score(monkeypatch):
    monkeypatch.setattr(ingest_search, "CONFIG", CONFIG.model_copy(update={"ingest_bm25_min_score": 5.0}))
    hits = [_hit("a.md", "Alpha", 3.0), _hit("b.md", "Beta", 6.0)]
    with patch("app.ingest.search.fts_search", return_value=hits):
        result = ingest_search.candidates("content", None)
    assert len(result) == 1
    assert result[0].path == "b.md"


def test_candidates_sorted_descending_by_score(monkeypatch):
    monkeypatch.setattr(ingest_search, "CONFIG", CONFIG.model_copy(update={"ingest_bm25_min_score": 0.0}))
    hits = [_hit("low.md", None, 1.0), _hit("high.md", None, 5.0), _hit("mid.md", None, 3.0)]
    with patch("app.ingest.search.fts_search", return_value=hits):
        result = ingest_search.candidates("content", None)
    assert [h.path for h in result] == ["high.md", "mid.md", "low.md"]


def test_title_boost_raises_score(monkeypatch):
    monkeypatch.setattr(ingest_search, "CONFIG", CONFIG.model_copy(update={"ingest_bm25_min_score": 0.0, "ingest_bm25_title_boost": 4.0}))
    # "deploy guide" has perfect Jaccard overlap with incoming title
    hits = [_hit("deploy.md", "deploy guide", 2.0), _hit("other.md", "unrelated", 2.0)]
    with patch("app.ingest.search.fts_search", return_value=hits):
        result = ingest_search.candidates("content", "deploy guide")
    assert result[0].path == "deploy.md"
    assert result[0].score > result[1].score


def test_title_boost_zero_when_no_incoming_title(monkeypatch):
    monkeypatch.setattr(ingest_search, "CONFIG", CONFIG.model_copy(update={"ingest_bm25_min_score": 0.0, "ingest_bm25_title_boost": 4.0}))
    hits = [_hit("a.md", "deploy guide", 2.0), _hit("b.md", "other", 3.0)]
    with patch("app.ingest.search.fts_search", return_value=hits):
        result = ingest_search.candidates("content", None)
    # no boost applied — sorted purely by BM25 score
    assert result[0].path == "b.md"


def test_title_boost_threshold_interaction(monkeypatch):
    monkeypatch.setattr(ingest_search, "CONFIG", CONFIG.model_copy(update={"ingest_bm25_min_score": 5.0, "ingest_bm25_title_boost": 4.0}))
    # raw score 3.0 is below threshold, but title boost should push it over
    hits = [_hit("a.md", "deploy guide", 3.0)]
    with patch("app.ingest.search.fts_search", return_value=hits):
        result = ingest_search.candidates("content", "deploy guide")
    assert len(result) == 1


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


@patch("app.tasks.wiki_update.ingest_search.candidates", return_value=[])
def test_filtered_source_drops_silently(mock_search, monkeypatch):
    monkeypatch.setattr("app.ingest.source_tiers.FILTERED_SOURCES", frozenset({"git_commit"}))
    _run(_make_push(source="git_commit"))
    mock_search.assert_not_called()


@patch("app.tasks.wiki_update.ingest_search.candidates", return_value=[])
def test_no_candidates_returns_early(mock_search):
    _run(_make_push())
    mock_search.assert_called_once()


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
    mock_search.return_value = [_hit("page.md", "Page", 5.0)]
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
    mock_search.return_value = [_hit("page.md", "Page", 5.0)]
    mock_reconcile.return_value = (["new body"], 1)
    _run(_make_push())
    assert mock_commit.call_args.kwargs["author"] == "Onyx Ingest <onyx-ingest@local>"
    # And the binding is unwound after the run — no leak into later tasks.
    assert author_string() == "AI Wiki Helper <ai-wiki-helper@local>"


@patch("app.tasks.wiki_update.wiki_git.commit_file")
@patch("app.tasks.wiki_update.wiki_git.read_file", return_value="body")
@patch("app.tasks.wiki_update.ingest_batch_reconciler.batch_reconcile")
@patch("app.tasks.wiki_update.ingest_search.candidates")
def test_no_change_does_not_commit(mock_search, mock_reconcile, mock_read, mock_commit, monkeypatch):
    monkeypatch.setattr("app.tasks.wiki_update.get_llm_settings", lambda: _settings_with_model())
    mock_search.return_value = [_hit("page.md", "Page", 5.0)]
    mock_reconcile.return_value = ([None], 1)
    _run(_make_push())
    mock_commit.assert_not_called()


@patch("app.tasks.wiki_update.wiki_git.commit_file")
@patch("app.tasks.wiki_update.wiki_git.read_file", return_value="body")
@patch("app.tasks.wiki_update.ingest_batch_reconciler.batch_reconcile")
@patch("app.tasks.wiki_update.ingest_search.candidates")
def test_irrelevant_does_not_commit(mock_search, mock_reconcile, mock_read, mock_commit, monkeypatch):
    monkeypatch.setattr("app.tasks.wiki_update.get_llm_settings", lambda: _settings_with_model())
    mock_search.return_value = [_hit("page.md", "Page", 5.0)]
    mock_reconcile.return_value = ([IRRELEVANT_SENTINEL], 1)
    _run(_make_push())
    mock_commit.assert_not_called()


@patch("app.tasks.wiki_update.wiki_git.commit_file")
@patch("app.tasks.wiki_update.ingest_batch_reconciler.batch_reconcile")
@patch("app.tasks.wiki_update.ingest_search.candidates")
def test_ingestion_disabled_skips_candidate_before_llm(
    mock_search, mock_reconcile, mock_commit, monkeypatch
):
    # A page whose policy disables ingestion auto-update must be dropped before
    # any LLM call — the reconciler never sees it and nothing commits.
    monkeypatch.setattr("app.tasks.wiki_update.get_llm_settings", lambda: _settings_with_model())
    monkeypatch.setattr(
        "app.tasks.wiki_update.update_policy.is_ingest_disabled",
        lambda path: path == "page.md",
    )
    mock_search.return_value = [_hit("page.md", "Page", 5.0)]
    _run(_make_push())
    mock_reconcile.assert_not_called()
    mock_commit.assert_not_called()


@patch("app.wiki.utils.wiki_notify.after_doc_write")
@patch("app.tasks.wiki_update.wiki_git.commit_file", return_value="sha")
@patch("app.tasks.wiki_update.wiki_git.read_file", return_value="body")
@patch("app.tasks.wiki_update.ingest_batch_reconciler.batch_reconcile")
@patch("app.tasks.wiki_update.ingest_search.candidates")
def test_n_consecutive_irrelevant_stops_loop(mock_search, mock_reconcile, mock_read, mock_commit, mock_notify, monkeypatch):
    monkeypatch.setattr("app.tasks.wiki_update.CONFIG", CONFIG.model_copy(update={"ingest_irrelevant_stop_n": 2}))
    monkeypatch.setattr("app.tasks.wiki_update.get_llm_settings", lambda: _settings_with_model())
    # 5 candidates — first two are IRRELEVANT, rest would commit but never reached
    mock_search.return_value = [_hit(f"p{i}.md", f"P{i}", float(5 - i)) for i in range(5)]
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
    mock_search.return_value = [_hit(f"p{i}.md", None, float(5 - i)) for i in range(3)]
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
    mock_search.return_value = [_hit(f"p{i}.md", None, float(5 - i)) for i in range(3)]
    mock_reconcile.return_value = ([IRRELEVANT_SENTINEL, "new body", IRRELEVANT_SENTINEL], 1)
    _run(_make_push())
    mock_commit.assert_called_once()


@patch("app.tasks.wiki_update.wiki_git.commit_file")
@patch("app.tasks.wiki_update.wiki_git.read_file", side_effect=Exception("file not found"))
@patch("app.tasks.wiki_update.ingest_batch_reconciler.batch_reconcile")
@patch("app.tasks.wiki_update.ingest_search.candidates")
def test_missing_file_skipped(mock_search, mock_reconcile, mock_read, mock_commit, monkeypatch):
    monkeypatch.setattr("app.tasks.wiki_update.get_llm_settings", lambda: _settings_with_model())
    mock_search.return_value = [_hit("missing.md", None, 5.0)]
    _run(_make_push())
    mock_reconcile.assert_not_called()
    mock_commit.assert_not_called()


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
    mock_search.side_effect = [ingest_search.IngestSearchError("maxClauseCount is set to 1024"), [_hit("page.md", "Page", 5.0)]]
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
    mock_search.side_effect = [ingest_search.IngestSearchError("maxClauseCount is set to 1024"), []]
    _run(_make_push(content="x " * 5000))
    mock_bounded.assert_called_once()
    assert mock_search.call_count == 2
