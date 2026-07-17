"""Tests for the document ingest pipeline.

Covers:
  - app.ingest.source_tiers  — is_filtered()
  - app.tasks.wiki_update.process_pushed_document
      - filtered source drop
      - relevance-filter candidate stage (all enabled pages -> kept)
      - document-embedding-unavailable drop
      - IRRELEVANT / NO_CHANGE / new body outputs
      - N-consecutive IRRELEVANT stop rule
      - observability counters
"""
from __future__ import annotations

import dataclasses
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.config import CONFIG
from app.db.page_embeddings import PageVector
from app.ingest.relevance.filter import RelevanceFilter
from app.ingest.relevance.service import RelevanceService
from app.ingest.settings import IngestSettings
from app.ingest.source_tiers import is_filtered
from app.ingest.types import CandidatePage, IngestionDocument
from app.llm import embeddings as llm_embeddings
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
    """The candidate stage calls update_policy.resolve_for_paths, which opens a DB
    session. These unit tests mock git/embeddings and run without a DB, so default
    to "no policy" (enabled, no instruction). Policy-specific tests override this."""
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
        updated_at=None,
        updated_by_user_id=None,
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
# process_pushed_document                                                      #
# --------------------------------------------------------------------------- #


_VEC = [0.1, 0.2, 0.3]


class _KeepFilter(RelevanceFilter):
    """Fake filter: keeps every page, or only ``keep`` when given; exposes
    ``scores`` (path -> score) when given."""

    def __init__(
        self, keep: set[str] | None = None, scores: dict[str, float] | None = None
    ) -> None:
        self._keep = keep
        self._scores = scores

    def is_relevant(self, doc: IngestionDocument, page: CandidatePage) -> bool:
        return self._keep is None or page.path in self._keep

    def score_pages(
        self, doc: IngestionDocument, pages: list[CandidatePage]
    ) -> list[float | None] | None:
        if self._scores is None:
            return None
        return [self._scores.get(p.path) for p in pages]


def _stub_candidates(
    monkeypatch: pytest.MonkeyPatch,
    paths: list[str],
    keep: set[str] | None = None,
    scores: dict[str, float] | None = None,
) -> None:
    """Point the candidate stage at ``paths``: the embedding store holds one
    vector per path, the document embeds locally (no API), and the relevance
    filter keeps ``keep`` (all when None). Order is preserved end to end."""
    vectors = [PageVector(p, llm_embeddings.pack(_VEC)) for p in paths]
    monkeypatch.setattr(
        "app.db.page_embeddings.load_all", lambda model: vectors
    )
    monkeypatch.setattr(
        "app.ingest.enrich.with_document_embedding",
        lambda d: dataclasses.replace(d, embedding=_VEC),
    )
    monkeypatch.setattr(
        "app.tasks.wiki_update.get_relevance_service",
        lambda: RelevanceService(_KeepFilter(keep, scores)),
    )


def _outcome_count(outcome: str, wiki_path: str) -> float:
    from prometheus_client import REGISTRY
    return REGISTRY.get_sample_value(
        "ingest_outcomes_total", {"outcome": outcome, "wiki_path": wiki_path}
    ) or 0.0


def _doc_result_count(result: str) -> float:
    from prometheus_client import REGISTRY
    return REGISTRY.get_sample_value(
        "ingest_document_results_total", {"result": result}
    ) or 0.0


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


def test_filtered_source_drops_silently(monkeypatch):
    monkeypatch.setattr("app.ingest.source_tiers.FILTERED_SOURCES", frozenset({"git_commit"}))
    load_all = MagicMock()
    monkeypatch.setattr("app.db.page_embeddings.load_all", load_all)
    _run(_make_push(source="git_commit"))
    load_all.assert_not_called()


def test_doc_embedding_unavailable_drops_doc(monkeypatch):
    # No document vector -> nothing to score against. The doc is dropped (with a
    # terminal result) rather than fail-opening into reconciling every page.
    monkeypatch.setattr(
        "app.ingest.enrich.with_document_embedding", lambda d: d
    )
    load_all = MagicMock()
    monkeypatch.setattr("app.db.page_embeddings.load_all", load_all)
    before = _doc_result_count("no_candidates")
    _run(_make_push())
    load_all.assert_not_called()
    assert _doc_result_count("no_candidates") - before == 1.0


def test_empty_embedding_store_returns_early(monkeypatch):
    # No stored page vectors -> no candidates -> per-document terminal result.
    _stub_candidates(monkeypatch, [])
    before = _doc_result_count("no_candidates")
    _run(_make_push())
    assert _doc_result_count("no_candidates") - before == 1.0


def test_relevance_filter_drops_recorded(monkeypatch):
    # Pages the filter drops get the filtered_by_relevance outcome and never
    # reach the LLM; only kept pages become candidates.
    _stub_candidates(monkeypatch, ["kept.md", "dropped.md"], keep={"kept.md"})
    monkeypatch.setattr("app.tasks.wiki_update.get_llm_settings", lambda: _settings_with_model())
    before = _outcome_count("filtered_by_relevance", "dropped.md")
    with patch("app.tasks.wiki_update.wiki_git.read_file", return_value="body"), \
         patch("app.tasks.wiki_update.ingest_batch_reconciler.batch_reconcile") as mock_reconcile:
        mock_reconcile.return_value = ([None], 1)
        _run(_make_push())
    assert _outcome_count("filtered_by_relevance", "dropped.md") - before == 1.0
    candidates = mock_reconcile.call_args.kwargs["candidates"]
    assert [c.path for c in candidates] == ["kept.md"]


@patch("app.tasks.wiki_update.wiki_git.read_file", return_value="body")
@patch("app.tasks.wiki_update.ingest_batch_reconciler.batch_reconcile")
def test_candidates_ordered_most_relevant_first(mock_reconcile, mock_read, monkeypatch):
    # The reconciler (and its N-consecutive-IRRELEVANT early stop) sees kept
    # candidates most-relevant-first, each hit carrying its relevance score.
    monkeypatch.setattr("app.tasks.wiki_update.get_llm_settings", lambda: _settings_with_model())
    _stub_candidates(
        monkeypatch,
        ["lo.md", "hi.md", "mid.md"],
        scores={"lo.md": 0.2, "hi.md": 0.9, "mid.md": 0.5},
    )
    mock_reconcile.return_value = ([None, None, None], 1)
    _run(_make_push())
    candidates = mock_reconcile.call_args.kwargs["candidates"]
    assert [c.path for c in candidates] == ["hi.md", "mid.md", "lo.md"]
    assert [c.score for c in candidates] == [0.9, 0.5, 0.2]


def test_all_pages_dropped_records_no_candidates(monkeypatch):
    _stub_candidates(monkeypatch, ["a.md", "b.md"], keep=set())
    before = _doc_result_count("no_candidates")
    _run(_make_push())
    assert _doc_result_count("no_candidates") - before == 1.0


def test_no_readable_candidates_records_no_candidates(monkeypatch):
    # Kept pages exist but none are readable -> also recorded as no_candidates.
    _stub_candidates(monkeypatch, ["page.md"])
    before = _doc_result_count("no_candidates")
    with patch("app.tasks.wiki_update.wiki_git.read_file", side_effect=OSError("unreadable")):
        _run(_make_push())
    assert _doc_result_count("no_candidates") - before == 1.0


@patch("app.tasks.wiki_update.wiki_git.head_sha_for_path", return_value="headsha")
@patch("app.wiki.utils.wiki_notify.after_doc_write")
@patch("app.tasks.wiki_update.wiki_git.commit_file", return_value="sha123")
@patch("app.tasks.wiki_update.wiki_git.read_file", return_value="old body")
@patch("app.tasks.wiki_update.ingest_batch_reconciler.batch_reconcile")
def test_new_body_commits(mock_reconcile, mock_read, mock_commit, mock_notify, mock_head, monkeypatch):
    monkeypatch.setattr("app.tasks.wiki_update.get_llm_settings", lambda: _settings_with_model())
    _stub_candidates(monkeypatch, ["page.md"])
    mock_reconcile.return_value = (["new body"], 1)
    _run(_make_push())
    mock_commit.assert_called_once()
    mock_notify.assert_called_once()


@patch("app.tasks.wiki_update.wiki_git.read_file", return_value="old body")
@patch("app.tasks.wiki_update.ingest_batch_reconciler.batch_reconcile")
def test_push_metadata_passed_to_reconciler(mock_reconcile, mock_read, monkeypatch):
    monkeypatch.setattr("app.tasks.wiki_update.get_llm_settings", lambda: _settings_with_model())
    _stub_candidates(monkeypatch, ["page.md"])
    mock_reconcile.return_value = ([None], 1)
    meta = {"object_type": ["PullRequest"], "merged": ["True"]}
    _run(_make_push(source="github", metadata=meta))
    assert mock_reconcile.call_args.kwargs["metadata"] == meta


@patch("app.tasks.wiki_update.wiki_git.head_sha_for_path", return_value="headsha")
@patch("app.wiki.utils.wiki_notify.after_doc_write")
@patch("app.tasks.wiki_update.wiki_git.commit_file", return_value="sha123")
@patch("app.tasks.wiki_update.wiki_git.read_file", return_value="old body")
@patch("app.tasks.wiki_update.ingest_batch_reconciler.batch_reconcile")
def test_commit_attributed_to_onyx_ingest(
    mock_reconcile, mock_read, mock_commit, mock_notify, mock_head, monkeypatch
):
    # No human user is bound on the ingest path, so the commit author would
    # otherwise fall back to "AI Wiki Helper". process_pushed_document binds the
    # Onyx Ingest identity for the run instead.
    monkeypatch.setattr("app.tasks.wiki_update.get_llm_settings", lambda: _settings_with_model())
    _stub_candidates(monkeypatch, ["page.md"])
    mock_reconcile.return_value = (["new body"], 1)
    _run(_make_push())
    assert mock_commit.call_args.kwargs["author"] == wiki_constants.INGEST_AUTHOR
    # And the binding is unwound after the run — no leak into later tasks.
    assert author_string() == "AI Wiki Helper <ai-wiki-helper@local>"


@patch("app.tasks.wiki_update.wiki_git.commit_file")
@patch("app.tasks.wiki_update.wiki_git.read_file", return_value="body")
@patch("app.tasks.wiki_update.ingest_batch_reconciler.batch_reconcile")
def test_no_change_does_not_commit(mock_reconcile, mock_read, mock_commit, monkeypatch):
    monkeypatch.setattr("app.tasks.wiki_update.get_llm_settings", lambda: _settings_with_model())
    _stub_candidates(monkeypatch, ["page.md"])
    mock_reconcile.return_value = ([None], 1)
    _run(_make_push())
    mock_commit.assert_not_called()


@patch("app.tasks.wiki_update.wiki_git.commit_file")
@patch("app.tasks.wiki_update.wiki_git.read_file", return_value="body")
@patch("app.tasks.wiki_update.ingest_batch_reconciler.batch_reconcile")
def test_irrelevant_does_not_commit(mock_reconcile, mock_read, mock_commit, monkeypatch):
    monkeypatch.setattr("app.tasks.wiki_update.get_llm_settings", lambda: _settings_with_model())
    _stub_candidates(monkeypatch, ["page.md"])
    mock_reconcile.return_value = ([IRRELEVANT_SENTINEL], 1)
    _run(_make_push())
    mock_commit.assert_not_called()


@patch("app.tasks.wiki_update.ingest_eval_sample.log_sample")
@patch("app.llm.client.complete")
@patch("app.tasks.wiki_update.wiki_git.read_file", return_value="body")
def test_omitted_candidate_recorded_as_irrelevant(
    mock_read, mock_complete, mock_log, monkeypatch
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

    _stub_candidates(monkeypatch, ["kept.md", "omitted.md"])
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
def test_ingestion_disabled_skips_candidate_before_llm(
    mock_reconcile, mock_commit, monkeypatch
):
    # A page whose policy disables ingestion auto-update is never a candidate —
    # the filter and reconciler never see it and nothing commits.
    monkeypatch.setattr("app.tasks.wiki_update.get_llm_settings", lambda: _settings_with_model())
    from app.wiki.update_policy import ResolvedPolicy

    _stub_candidates(monkeypatch, ["page.md"])
    monkeypatch.setattr(
        "app.tasks.wiki_update.update_policy.resolve_for_paths",
        lambda paths: {"page.md": ResolvedPolicy(ingestion_auto_update_disabled=True)},
    )
    before = _outcome_count("ingestion_auto_update_disabled", "page.md")
    _run(_make_push())
    mock_reconcile.assert_not_called()
    mock_commit.assert_not_called()
    # The policy exclusion is visible: the blocked would-be candidate is recorded.
    assert _outcome_count("ingestion_auto_update_disabled", "page.md") - before == 1.0


@patch("app.tasks.wiki_update.update_frequency.record_auto_update_capped")
@patch("app.tasks.wiki_update.wiki_git.commit_file")
@patch("app.tasks.wiki_update.ingest_batch_reconciler.batch_reconcile")
def test_over_cap_skips_candidate_before_llm(
    mock_reconcile, mock_commit, mock_capped, monkeypatch
):
    # A page that already hit the admin cap in the trailing 24h is dropped before
    # any LLM call — the reconciler never sees it and nothing commits (no tokens).
    # The exclusion logs the (deduped) capped activity event.
    monkeypatch.setattr(
        "app.tasks.wiki_update.ingest_settings.get",
        lambda: _ingest_settings(auto_update_cap=3),
    )
    monkeypatch.setattr(
        "app.tasks.wiki_update.wiki_git.ingest_update_times_24h",
        lambda _: [1, 2, 3],  # 3 updates in window >= cap 3
    )
    _stub_candidates(monkeypatch, ["page.md"])
    _run(_make_push())
    mock_reconcile.assert_not_called()
    mock_commit.assert_not_called()
    mock_capped.assert_called_once_with("page.md", 3, 3)


@patch("app.tasks.wiki_update.wiki_git.head_sha_for_path", return_value="headsha")
@patch("app.wiki.utils.wiki_notify.after_doc_write")
@patch("app.tasks.wiki_update.wiki_git.commit_file", return_value="sha123")
@patch("app.tasks.wiki_update.wiki_git.read_file", return_value="old body")
@patch("app.tasks.wiki_update.ingest_batch_reconciler.batch_reconcile")
def test_update_instruction_passed_to_reconciler(
    mock_reconcile, mock_read, mock_commit, mock_notify, mock_head, monkeypatch
):
    # The resolved per-page instruction rides onto the candidate the reconciler sees.
    monkeypatch.setattr("app.tasks.wiki_update.get_llm_settings", lambda: _settings_with_model())
    from app.wiki.update_policy import ResolvedPolicy

    _stub_candidates(monkeypatch, ["page.md"])
    monkeypatch.setattr(
        "app.tasks.wiki_update.update_policy.resolve_for_paths",
        lambda paths: {"page.md": ResolvedPolicy(update_instruction="Keep it terse.")},
    )
    mock_reconcile.return_value = (["new body"], 1)
    _run(_make_push())
    candidates = mock_reconcile.call_args.kwargs["candidates"]
    assert candidates[0].update_instruction == "Keep it terse."


@patch("app.wiki.utils.wiki_notify.after_doc_write")
@patch("app.tasks.wiki_update.wiki_git.commit_file", return_value="sha")
@patch("app.tasks.wiki_update.wiki_git.read_file", return_value="body")
@patch("app.tasks.wiki_update.ingest_batch_reconciler.batch_reconcile")
def test_n_consecutive_irrelevant_stops_loop(mock_reconcile, mock_read, mock_commit, mock_notify, monkeypatch):
    monkeypatch.setattr("app.tasks.wiki_update.CONFIG", CONFIG.model_copy(update={"ingest_irrelevant_stop_n": 2}))
    monkeypatch.setattr("app.tasks.wiki_update.get_llm_settings", lambda: _settings_with_model())
    # 5 candidates — first two are IRRELEVANT, rest would commit but never reached
    _stub_candidates(monkeypatch, [f"p{i}.md" for i in range(5)])
    mock_reconcile.return_value = ([IRRELEVANT_SENTINEL, IRRELEVANT_SENTINEL, "new", "new", "new"], 1)
    _run(_make_push())
    mock_commit.assert_not_called()


@patch("app.wiki.utils.wiki_notify.after_doc_write")
@patch("app.tasks.wiki_update.wiki_git.commit_file", return_value="sha")
@patch("app.tasks.wiki_update.wiki_git.read_file", return_value="body")
@patch("app.tasks.wiki_update.ingest_batch_reconciler.batch_reconcile")
def test_no_change_resets_irrelevant_counter(mock_reconcile, mock_read, mock_commit, mock_notify, monkeypatch):
    monkeypatch.setattr("app.tasks.wiki_update.CONFIG", CONFIG.model_copy(update={"ingest_irrelevant_stop_n": 2}))
    monkeypatch.setattr("app.tasks.wiki_update.get_llm_settings", lambda: _settings_with_model())
    # IRRELEVANT, NO_CHANGE (resets counter), IRRELEVANT — should NOT stop early
    _stub_candidates(monkeypatch, [f"p{i}.md" for i in range(3)])
    mock_reconcile.return_value = ([IRRELEVANT_SENTINEL, None, IRRELEVANT_SENTINEL], 1)
    _run(_make_push())
    mock_commit.assert_not_called()


@patch("app.tasks.wiki_update.wiki_git.head_sha_for_path", return_value="headsha")
@patch("app.wiki.utils.wiki_notify.after_doc_write")
@patch("app.tasks.wiki_update.wiki_git.commit_file", return_value="sha")
@patch("app.tasks.wiki_update.wiki_git.read_file", return_value="body")
@patch("app.tasks.wiki_update.ingest_batch_reconciler.batch_reconcile")
def test_commit_resets_irrelevant_counter(mock_reconcile, mock_read, mock_commit, mock_notify, mock_head, monkeypatch):
    monkeypatch.setattr("app.tasks.wiki_update.CONFIG", CONFIG.model_copy(update={"ingest_irrelevant_stop_n": 2}))
    monkeypatch.setattr("app.tasks.wiki_update.get_llm_settings", lambda: _settings_with_model())
    # IRRELEVANT, new body (resets counter), IRRELEVANT — should NOT stop early
    _stub_candidates(monkeypatch, [f"p{i}.md" for i in range(3)])
    mock_reconcile.return_value = ([IRRELEVANT_SENTINEL, "new body", IRRELEVANT_SENTINEL], 1)
    _run(_make_push())
    mock_commit.assert_called_once()


@patch("app.tasks.wiki_update.wiki_git.commit_file")
@patch("app.tasks.wiki_update.wiki_git.read_file", side_effect=Exception("file not found"))
@patch("app.tasks.wiki_update.ingest_batch_reconciler.batch_reconcile")
def test_missing_file_skipped(mock_reconcile, mock_read, mock_commit, monkeypatch):
    monkeypatch.setattr("app.tasks.wiki_update.get_llm_settings", lambda: _settings_with_model())
    _stub_candidates(monkeypatch, ["missing.md"])
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


@patch("app.tasks.wiki_update.ingest_batch_reconciler.batch_reconcile", return_value=([], 0))
@patch("app.tasks.wiki_update.ingest_selector.select_candidates", return_value=[])
def test_selector_drop_eval_logged(
    mock_select, mock_reconcile, tmp_repo, monkeypatch
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
    _stub_candidates(monkeypatch, ["page.md"])
    _run(_make_push())
    rows = _eval_rows("filtered_by_selector")
    assert len(rows) == 1
    assert rows[0]["wiki_path"] == "page.md"
