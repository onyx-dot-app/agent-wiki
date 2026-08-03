"""``run_extraction`` end to end: a real wiki repo, a real database, a stubbed LLM.

The orchestration is where the money is spent, so the properties pinned here are all about
which pages get a call and which do not. Every one of them is silent when wrong — an
over-eager guard just costs money, and a lax one leaves needs labelled against a taxonomy
that no longer defines their types.
"""

from __future__ import annotations

import json

import pytest

from app.db import entity_type_taxonomy, page_needs
from app.ingest import needs
from app.llm.client import CompletionResult
from app.wiki import git as wiki_git


def _page(path: str, body: str = "# P\n\nStatus: open\n") -> None:
    wiki_git.commit_file(path, body, "seed", author=None)


def _taxonomy(name: str = "organization") -> int:
    return entity_type_taxonomy.record(
        {
            "corpus_fingerprint": "abc",
            "entity_types": [{"name": name, "definition": "A named company."}],
        }
    )


@pytest.fixture
def llm(monkeypatch):
    """Stub the LLM and count calls — the unit of cost this step is measured in."""
    calls: list[str] = []

    def fake_complete(messages, **kwargs):
        calls.append(messages[-1]["content"])
        return CompletionResult(
            text=json.dumps(
                {
                    "needs": [
                        {
                            "need_name": "deal status",
                            "need_kind": "entity_status",
                            "description": "status and blockers",
                            "current_content": "Status: open",
                            "entities": [
                                {
                                    "canonical_name": "Acme",
                                    "entity_type": "organization",
                                    "primary": True,
                                }
                            ],
                            "focus": "specific",
                        }
                    ]
                }
            )
        )

    monkeypatch.setattr(needs.client, "complete", fake_complete)
    return calls


class TestFirstRun:
    def test_extracts_every_page_and_stores_the_needs(self, tmp_repo, llm) -> None:
        _page("a.md")
        _page("b.md")

        counts = needs.run_extraction()

        assert counts["extracted"] == 2
        assert counts["needs"] == 2
        assert len(llm) == 2
        stored = page_needs.get("a.md")
        assert stored is not None
        assert stored.needs[0]["need_name"] == "deal status"
        assert stored.needs[0]["entities"][0]["entity_type"] == "organization"

    def test_records_the_model_by_name_not_as_a_blank(self, tmp_repo, llm, monkeypatch) -> None:
        """Both callers use the deployment default, so if the model were stored unresolved every
        row would hold "". The guard would then compare "" to "" after an admin switched models
        and conclude nothing changed, silently keeping needs written by the old one."""
        from app.llm import settings as llm_settings

        monkeypatch.setattr(
            needs, "get_llm_settings", lambda: llm_settings.LLMSettings(model="gpt-5.5")
        )
        _page("a.md")

        needs.run_extraction()

        stored = page_needs.get("a.md")
        assert stored is not None
        assert stored.model == "gpt-5.5"

    def test_switching_the_deployment_model_re_extracts(self, tmp_repo, llm, monkeypatch) -> None:
        """The case "let's change the model later" — it has to invalidate."""
        from app.llm import settings as llm_settings

        monkeypatch.setattr(
            needs, "get_llm_settings", lambda: llm_settings.LLMSettings(model="gpt-5.5")
        )
        _page("a.md")
        needs.run_extraction()
        llm.clear()

        monkeypatch.setattr(
            needs, "get_llm_settings", lambda: llm_settings.LLMSettings(model="claude-opus-4-7")
        )
        counts = needs.run_extraction()

        assert len(llm) == 1
        assert counts["extracted"] == 1
        stored = page_needs.get("a.md")
        assert stored is not None
        assert stored.model == "claude-opus-4-7"

    def test_records_the_active_taxonomy_on_every_page(self, tmp_repo, llm) -> None:
        """Need extraction is the first real consumer of the derived taxonomy, so the link
        back to it has to be written — it is what makes the type labels resolvable later."""
        entity_type_taxonomy_id = _taxonomy()
        _page("a.md")

        needs.run_extraction()

        stored = page_needs.get("a.md")
        assert stored is not None
        assert stored.entity_type_taxonomy_id == entity_type_taxonomy_id

    def test_works_with_no_taxonomy_at_all(self, tmp_repo, llm) -> None:
        """A deployment that has never derived still extracts, against the generic fallback
        types — the same way the relevance scorer degrades without its model file."""
        _page("a.md")

        counts = needs.run_extraction()

        assert counts["needs"] == 1
        stored = page_needs.get("a.md")
        assert stored is not None
        assert stored.entity_type_taxonomy_id is None

    def test_the_derived_types_reach_the_prompt(self, tmp_repo, llm, monkeypatch) -> None:
        prompts: list[str] = []

        def capture(messages, **kwargs):
            prompts.append(messages[0]["content"])
            return CompletionResult(text=json.dumps({"needs": []}))

        monkeypatch.setattr(needs.client, "complete", capture)
        _taxonomy("aircraft_model")
        _page("a.md")

        needs.run_extraction()

        assert "aircraft_model" in prompts[0]


class TestIncremental:
    def test_a_second_run_over_an_unchanged_wiki_costs_nothing(self, tmp_repo, llm) -> None:
        _page("a.md")
        _page("b.md")
        needs.run_extraction()
        llm.clear()

        counts = needs.run_extraction()

        assert llm == []
        assert counts["extracted"] == 0
        assert counts["skipped"] == 2

    def test_one_edit_costs_one_call(self, tmp_repo, llm) -> None:
        """The reason needs are stored rather than recomputed."""
        _page("a.md")
        _page("b.md")
        needs.run_extraction()
        llm.clear()
        _page("b.md", "# P\n\nStatus: closed\n")

        counts = needs.run_extraction()

        assert len(llm) == 1
        assert counts["extracted"] == 1
        assert counts["skipped"] == 1

    def test_a_new_page_is_extracted_and_the_rest_left_alone(self, tmp_repo, llm) -> None:
        _page("a.md")
        needs.run_extraction()
        llm.clear()
        _page("new.md")

        needs.run_extraction()

        assert len(llm) == 1

    def test_a_re_derived_taxonomy_re_extracts_everything(self, tmp_repo, llm) -> None:
        """A page skipped here would keep entity types the current taxonomy no longer
        defines, and nothing downstream could detect it."""
        _taxonomy("software_product_or_service")
        _page("a.md")
        needs.run_extraction()
        llm.clear()
        second = _taxonomy("software_product")

        counts = needs.run_extraction()

        assert len(llm) == 1
        stored = page_needs.get("a.md")
        assert stored is not None
        assert stored.entity_type_taxonomy_id == second
        assert counts["skipped"] == 0

    def test_a_rename_costs_nothing(self, tmp_repo, llm) -> None:
        """The reason needs key on the doc id: a reorganization changed no content, so it must
        not be billed. Path-keyed this would be one call per moved page plus a prune."""
        from app.wiki import doc_ids

        _page("old.md")
        needs.run_extraction()
        llm.clear()

        _sha, moves = wiki_git.move_path("old.md", "sub/new.md", "rename")
        doc_ids.on_path_moved(moves)

        counts = needs.run_extraction()

        assert llm == []
        assert counts["skipped"] == 1
        assert [row.path for row in page_needs.load_all()] == ["sub/new.md"]

    def test_force_re_extracts_an_unchanged_wiki(self, tmp_repo, llm) -> None:
        """What a prompt change requires: stored needs are only comparable to each other when
        they came from the same prompt, and the prompt is not part of the guard."""
        _page("a.md")
        needs.run_extraction()
        llm.clear()

        counts = needs.run_extraction(force=True)

        assert len(llm) == 1
        assert counts["extracted"] == 1


class TestBookkeeping:
    def test_a_page_that_yields_nothing_is_recorded_and_not_retried(
        self, tmp_repo, monkeypatch
    ) -> None:
        """Storing the empty answer is what stops a page being paid for on every run."""
        calls: list[str] = []

        def empty(messages, **kwargs):
            calls.append("x")
            return CompletionResult(text=json.dumps({"needs": []}))

        monkeypatch.setattr(needs.client, "complete", empty)
        _page("a.md")

        counts = needs.run_extraction()
        assert counts["empty"] == 1
        assert counts["needs"] == 0

        needs.run_extraction()
        assert len(calls) == 1

    def test_one_unparseable_page_does_not_stop_the_others(self, tmp_repo, monkeypatch) -> None:
        def flaky(messages, **kwargs):
            if "bad.md" in messages[-1]["content"]:
                return CompletionResult(text="not json")
            return CompletionResult(
                text=json.dumps(
                    {
                        "needs": [
                            {
                                "need_name": "a",
                                "need_kind": "reference",
                                "description": "d",
                            }
                        ]
                    }
                )
            )

        monkeypatch.setattr(needs.client, "complete", flaky)
        _page("bad.md")
        _page("good.md")

        counts = needs.run_extraction()

        assert counts["needs"] == 1
        assert counts["empty"] == 1
        assert page_needs.get("good.md") is not None

    def test_deleted_pages_are_pruned(self, tmp_repo, llm) -> None:
        """Needs of a page that no longer exists would still cluster downstream, so a fact
        could be reconciled onto a page that is gone."""
        _page("a.md")
        _page("gone.md")
        needs.run_extraction()

        wiki_git.delete_path("gone.md", "remove")
        needs.run_extraction()

        assert [row.path for row in page_needs.load_all()] == ["a.md"]

    def test_prefix_limits_the_pass(self, tmp_repo, llm) -> None:
        _page("keep/a.md")
        _page("other/b.md")

        counts = needs.run_extraction(prefix="keep")

        assert counts["pages"] == 1
        assert [row.path for row in page_needs.load_all()] == ["keep/a.md"]

    def test_a_prefixed_run_does_not_prune_outside_its_scope(self, tmp_repo, llm) -> None:
        """A scoped run only knows about its own scope, so everything else must be left alone.
        Pruning it would discard needs that cost an LLM call each, and silently: nothing
        downstream can tell "never extracted" from "wrongly pruned"."""
        _page("keep/a.md")
        _page("other/b.md")
        needs.run_extraction()
        assert len(page_needs.load_all()) == 2

        needs.run_extraction(prefix="keep")

        assert sorted(row.path for row in page_needs.load_all()) == ["keep/a.md", "other/b.md"]

    def test_a_prefixed_run_still_prunes_inside_its_scope(self, tmp_repo, llm) -> None:
        _page("keep/a.md")
        _page("keep/gone.md")
        needs.run_extraction(prefix="keep")

        wiki_git.delete_path("keep/gone.md", "remove")
        needs.run_extraction(prefix="keep")

        assert [row.path for row in page_needs.load_all()] == ["keep/a.md"]

    def test_scoping_is_a_path_boundary_not_a_string_prefix(self, tmp_repo, llm) -> None:
        """Scoping to "team" must not sweep "teamwork.md"."""
        _page("team/a.md")
        _page("teamwork.md")
        needs.run_extraction()

        needs.run_extraction(prefix="team")

        assert sorted(row.path for row in page_needs.load_all()) == ["team/a.md", "teamwork.md"]

    def test_an_empty_read_does_not_discard_stored_needs(
        self, tmp_repo, llm, caplog, monkeypatch
    ) -> None:
        """``read_corpus`` skips a page it cannot read, so a filesystem fault is indistinguishable
        from a wiki that lost every page. Pruning on that discards needs costing one LLM call each
        and unrecoverable without re-paying; keeping them costs nothing, because ``load_all``
        already hides pages whose doc-id row is not live."""
        _page("a.md")
        _page("b.md")
        needs.run_extraction()
        assert len(page_needs.load_all()) == 2

        monkeypatch.setattr(needs.entity_types, "read_corpus", lambda prefix="": [])
        with caplog.at_level("WARNING"):
            counts = needs.run_extraction()

        assert counts["pages"] == 0
        assert sorted(row.path for row in page_needs.load_all()) == ["a.md", "b.md"]
        assert any("skipping prune" in r.getMessage() for r in caplog.records)

    def test_an_empty_wiki_is_not_an_error(self, tmp_repo, llm) -> None:
        counts = needs.run_extraction()

        assert counts == {"pages": 0, "extracted": 0, "skipped": 0, "needs": 0, "empty": 0}
        assert llm == []


class TestParallelAndModel:
    """Extraction runs concurrently, and each page is durable as soon as it finishes."""

    def test_pages_extract_concurrently(self, tmp_repo, monkeypatch) -> None:
        import threading
        import time

        in_flight = 0
        peak = 0
        lock = threading.Lock()

        def slow(messages, **kwargs):
            nonlocal in_flight, peak
            with lock:
                in_flight += 1
                peak = max(peak, in_flight)
            time.sleep(0.05)
            with lock:
                in_flight -= 1
            return CompletionResult(text=json.dumps({"needs": []}))

        monkeypatch.setattr(needs.client, "complete", slow)
        for i in range(12):
            _page(f"p{i}.md")

        needs.run_extraction()

        assert peak > 1
        assert peak <= needs._workers()

    def test_concurrency_is_bounded(self, tmp_repo, monkeypatch) -> None:
        from types import SimpleNamespace

        monkeypatch.setattr(needs, "CONFIG", SimpleNamespace(need_extract_workers=2))
        import threading
        import time

        in_flight = 0
        peak = 0
        lock = threading.Lock()

        def slow(messages, **kwargs):
            nonlocal in_flight, peak
            with lock:
                in_flight += 1
                peak = max(peak, in_flight)
            time.sleep(0.03)
            with lock:
                in_flight -= 1
            return CompletionResult(text=json.dumps({"needs": []}))

        monkeypatch.setattr(needs.client, "complete", slow)
        for i in range(10):
            _page(f"p{i}.md")

        needs.run_extraction()

        assert peak == 2

    def test_every_page_is_stored_not_just_counted(self, tmp_repo, llm) -> None:
        """Each page is written inside its own worker, so an interrupted run resumes instead of
        losing everything — the failure that cost the taxonomy derivation 105 pages."""
        for i in range(6):
            _page(f"p{i}.md")

        counts = needs.run_extraction()

        assert counts["extracted"] == 6
        assert len(page_needs.load_all()) == 6

    def test_one_unstorable_page_does_not_abort_the_rest(self, tmp_repo, llm, monkeypatch) -> None:
        real_store = page_needs.store

        def flaky(path, **kwargs):
            if path == "p2.md":
                raise RuntimeError("db hiccup")
            return real_store(path, **kwargs)

        monkeypatch.setattr(needs.page_needs, "store", flaky)
        for i in range(5):
            _page(f"p{i}.md")

        counts = needs.run_extraction()

        assert counts["extracted"] == 4
        assert sorted(r.path for r in page_needs.load_all()) == [
            "p0.md",
            "p1.md",
            "p3.md",
            "p4.md",
        ]

    def test_uses_the_ingestion_model(self, tmp_repo, llm, monkeypatch) -> None:
        from app.llm import settings as llm_settings

        monkeypatch.setattr(
            needs,
            "get_llm_settings",
            lambda: llm_settings.LLMSettings(model="main", ingest_selector_model="ingest-cheap"),
        )
        _page("a.md")

        needs.run_extraction()

        stored = page_needs.get("a.md")
        assert stored is not None
        assert stored.model == "ingest-cheap"

    def test_falls_back_to_the_main_model_by_name(self, tmp_repo, llm, monkeypatch) -> None:
        """Never "" — the staleness guard compares this column, so an empty value would make a
        later model switch invisible."""
        from app.llm import settings as llm_settings

        monkeypatch.setattr(
            needs,
            "get_llm_settings",
            lambda: llm_settings.LLMSettings(model="main", ingest_selector_model=""),
        )
        _page("a.md")

        needs.run_extraction()

        stored = page_needs.get("a.md")
        assert stored is not None
        assert stored.model == "main"
