"""The deterministic half of entity-type derivation.

Naming and merging are LLM calls and are not pinned here. What is left is pure: deciding
which spellings are one referent, and which extracted strings are things the wiki tracks
rather than things it is made of. Both change the taxonomy silently if they drift, and the
taxonomy is what keys facts by entity.
"""

from __future__ import annotations

from app.ingest.entity_types import (
    Mention,
    _member_indices,
    fold,
    is_corpus_artifact,
    normalize_surface,
)


class TestNormalizeSurface:
    def test_case_and_punctuation_collapse(self) -> None:
        assert normalize_surface("ACME") == normalize_surface("Acme")
        assert normalize_surface("ROHDE&SCHWARZ") == normalize_surface("Rohde & Schwarz")

    def test_strips_legal_suffix_and_version(self) -> None:
        assert normalize_surface("Acme AB") == "acme"
        assert normalize_surface("Acme Corp") == "acme"
        assert normalize_surface("Acme v4") == "acme"

    def test_keeps_a_vendor_distinct_from_its_product(self) -> None:
        """The reason containment is not used to fold: a prefix relation is not identity."""
        assert normalize_surface("Acme") != normalize_surface("Acme Teams")

    def test_never_returns_empty(self) -> None:
        assert normalize_surface("!!!") == "!!!"


class TestCorpusArtifacts:
    def test_page_title_is_not_a_referent(self) -> None:
        assert is_corpus_artifact("Architecture", {"architecture"}) == "page_title"

    def test_code_identifier_is_not_a_referent(self) -> None:
        assert is_corpus_artifact("documents_queue", set()) == "code_identifier"

    def test_leaves_real_names_alone(self) -> None:
        """The snake_case rule is deliberately narrow — these are genuine referents."""
        for name in ("Next.js", "@scope/pkg", "some-hyphenated-pkg", "Acme & Partners"):
            assert is_corpus_artifact(name, set()) == ""


class TestFold:
    def test_variants_become_one_referent_with_pages_unioned(self) -> None:
        folded = fold(
            [
                Mention(surface="Acme", page="a.md"),
                Mention(surface="ACME", page="b.md"),
                Mention(surface="Acme", page="c.md"),
            ]
        )
        assert len(folded) == 1
        assert folded[0].n_docs == 3
        assert folded[0].canonical == "Acme"  # most frequent spelling wins

    def test_fold_precedes_counting(self) -> None:
        """Folding is what makes document frequency mean anything: unfolded, this entity
        looks like three referents on one page each rather than one on three."""
        mentions = [
            Mention(surface=s, page=p)
            for s, p in (("Acme", "a.md"), ("ACME", "b.md"), ("Acme Inc", "c.md"))
        ]
        assert fold(mentions)[0].n_docs == 3
        assert len({m.surface for m in mentions}) == 3

    def test_distinct_things_stay_distinct(self) -> None:
        folded = fold(
            [Mention(surface="Acme", page="a.md"), Mention(surface="Globex", page="a.md")]
        )
        assert len(folded) == 2


class TestMemberIndices:
    """The LLM contract at the naming/merge boundary.

    Both prompts require a partition — every input index in exactly one output type. The
    caller has to enforce that: an omitted index silently drops a referent, and a repeated
    one inflates the support a type is judged on.
    """

    def test_converts_to_zero_based(self) -> None:
        assert _member_indices({"member_indices": [1, 3]}, 5) == [0, 2]

    def test_drops_out_of_range(self) -> None:
        assert _member_indices({"member_indices": [1, 99, 0, -2]}, 3) == [0]

    def test_ignores_non_numeric_and_booleans(self) -> None:
        """True is an int in Python; it is not an index."""
        assert _member_indices({"member_indices": ["2", None, True, 2]}, 4) == [1]

    def test_missing_or_malformed_is_empty(self) -> None:
        assert _member_indices({}, 3) == []
        assert _member_indices({"member_indices": "1,2"}, 3) == []


class TestCompleteJson:
    """The LLM boundary of the derivation. Every rule here exists because a large wiki page
    broke it in production: extraction overflowed the client's 4096-token default, the response
    came back cut off mid-list, and the truncation was reported as "unparseable JSON" naming
    neither the page nor the reason. Eight of eight of the largest pages failed that way,
    contributing zero referents each."""

    def _stub(self, monkeypatch, *results):
        # Patched where the call is MADE. ``_complete_json`` delegates to the shared
        # ``json_completion`` helper, so patching ``entity_types.client`` would stub nothing and
        # these tests would silently pass against the real client.
        from app.ingest import json_completion

        seen: list[dict] = []

        def fake_complete(messages, **kwargs):
            seen.append({"messages": messages, "kwargs": kwargs})
            return results[min(len(seen) - 1, len(results) - 1)]

        monkeypatch.setattr(json_completion.client, "complete", fake_complete)
        return seen

    def test_asks_for_more_than_the_client_default(self) -> None:
        from app.ingest.entity_types import MAX_OUTPUT_TOKENS
        from app.llm.client import DEFAULT_MAX_TOKENS

        assert MAX_OUTPUT_TOKENS > DEFAULT_MAX_TOKENS

    def test_passes_the_cap_on_every_call(self, monkeypatch) -> None:
        from app.ingest import entity_types
        from app.llm.client import CompletionResult

        seen = self._stub(
            monkeypatch, CompletionResult(text='{"referents":[]}', stop_reason="completed")
        )

        entity_types.extract_page("a.md", "body")

        assert seen[0]["kwargs"]["max_tokens"] == entity_types.MAX_OUTPUT_TOKENS

    def test_truncation_is_reported_as_truncation_and_names_the_page(
        self, monkeypatch, caplog
    ) -> None:
        from app.ingest import entity_types
        from app.llm.client import CompletionResult

        self._stub(
            monkeypatch,
            CompletionResult(text='{"referents":[{"name":"Acme"}', stop_reason="incomplete"),
        )

        with caplog.at_level("WARNING"):
            assert entity_types.extract_page("Customers/Big Page.md", "body") == []

        messages = [r.getMessage() for r in caplog.records]
        assert any("cut off" in m for m in messages)
        assert any("Customers/Big Page.md" in m for m in messages)

    def test_truncation_does_not_burn_the_retry(self, monkeypatch) -> None:
        """Truncation is deterministic — re-asking pays for the same overflowing response twice.
        What it needs is a bigger cap, not another prompt."""
        from app.ingest import entity_types
        from app.llm.client import CompletionResult

        seen = self._stub(
            monkeypatch, CompletionResult(text='{"referents":[{"name":"A"}', stop_reason="incomplete")
        )

        entity_types.extract_page("a.md", "body")

        assert len(seen) == 1

    def test_malformed_json_is_retried_with_the_reason(self, monkeypatch) -> None:
        from app.ingest import entity_types
        from app.llm.client import CompletionResult

        seen = self._stub(
            monkeypatch,
            CompletionResult(text="not json at all", stop_reason="completed"),
            CompletionResult(
                text='{"referents":[{"name":"Acme","what":"a customer"}]}',
                stop_reason="completed",
            ),
        )

        out = entity_types.extract_page("a.md", "body")

        assert [m.surface for m in out] == ["Acme"]
        assert len(seen) == 2
        assert "REJECTED" in seen[1]["messages"][-1]["content"]

    def test_gives_up_after_the_retry_and_names_the_page(self, monkeypatch, caplog) -> None:
        from app.ingest import entity_types
        from app.llm.client import CompletionResult

        seen = self._stub(monkeypatch, CompletionResult(text="never json", stop_reason="completed"))

        with caplog.at_level("WARNING"):
            assert entity_types.extract_page("Notes/x.md", "body") == []

        assert len(seen) == 2
        assert any("Notes/x.md" in r.getMessage() for r in caplog.records)


class TestParallelExtraction:
    """Extraction and naming run concurrently. The property that matters is that concurrency
    changes only the wall clock: ``_leader_cluster`` takes the FIRST centroid a referent matches
    and the naming collapse keeps first-seen examples, so a reordered result set is a different
    taxonomy from the same corpus."""

    def test_results_keep_input_order_regardless_of_completion_order(self, monkeypatch) -> None:
        """Completion order is deliberately the reverse of input order here, so a naive
        as-completed collection would fail this and a taxonomy would silently shift."""
        import time

        from app.ingest import entity_types

        pages = [(f"p{i}.md", "body") for i in range(8)]

        def slow_extract(path, body, *, model=None):
            # Earlier pages sleep longest, so they finish last.
            time.sleep(0.05 * (8 - int(path[1:-3])))
            return [entity_types.Mention(surface=path, page=path)]

        monkeypatch.setattr(entity_types, "extract_page", slow_extract)
        captured: list[list[entity_types.Mention]] = entity_types._map_ordered(
            lambda pb: slow_extract(pb[0], pb[1]), pages, stage="extract"
        )

        assert [m[0].surface for m in captured] == [p for p, _ in pages]

    def test_pages_really_run_concurrently(self, monkeypatch) -> None:
        """Otherwise this is a no-op refactor: the whole point is that 147 sequential calls at
        ~55s each took 1.5 hours."""
        import threading
        import time

        from app.ingest import entity_types

        in_flight = 0
        peak = 0
        lock = threading.Lock()

        def watched(item):
            nonlocal in_flight, peak
            with lock:
                in_flight += 1
                peak = max(peak, in_flight)
            time.sleep(0.05)
            with lock:
                in_flight -= 1
            return [item]

        entity_types._map_ordered(watched, list(range(16)), stage="extract")

        assert peak > 1
        assert peak <= entity_types._derive_workers()

    def test_concurrency_is_bounded(self, monkeypatch) -> None:
        """Unbounded fan-out over a large wiki would hit the provider's rate limit rather than
        finish faster."""
        import threading
        import time

        from app.ingest import entity_types

        in_flight = 0
        peak = 0
        lock = threading.Lock()

        def watched(item):
            nonlocal in_flight, peak
            with lock:
                in_flight += 1
                peak = max(peak, in_flight)
            time.sleep(0.02)
            with lock:
                in_flight -= 1
            return [item]

        entity_types._map_ordered(watched, list(range(200)), stage="extract")

        assert peak <= entity_types._derive_workers()

    def test_one_failing_item_does_not_abort_the_rest(self, monkeypatch) -> None:
        """The module's stated rule — a single failed page must not abort a corpus-wide
        derivation — has to survive the pool, which would otherwise propagate the exception and
        discard every other page's paid-for work."""
        from app.ingest import entity_types

        def sometimes_raises(item):
            if item == 3:
                raise RuntimeError("provider blew up")
            return [item]

        out = entity_types._map_ordered(sometimes_raises, list(range(6)), stage="extract")

        assert out == [[0], [1], [2], [], [4], [5]]

    def test_progress_reports_every_item(self, monkeypatch) -> None:
        from app.ingest import entity_types

        seen: list[tuple[str, int, int]] = []
        entity_types._map_ordered(
            lambda item: [item],
            list(range(5)),
            stage="extract",
            progress=lambda stage, n, total: seen.append((stage, n, total)),
        )

        assert seen == [("extract", n, 5) for n in range(1, 6)]

    def test_an_empty_corpus_starts_no_pool(self) -> None:
        from app.ingest import entity_types

        assert entity_types._map_ordered(lambda item: [item], [], stage="extract") == []


class TestDeriveWorkers:
    """The pool size is an operator knob, not a build-time constant: the governing constraint is
    the provider's rate limit, and this pool runs inside a queue task, so effective concurrency is
    the queue's worker count multiplied by this."""

    def test_defaults_to_eight(self) -> None:
        from app.ingest import entity_types

        assert entity_types._derive_workers() == entity_types.DEFAULT_DERIVE_WORKERS == 8

    def test_env_var_lowers_it(self, monkeypatch) -> None:
        """A lower-tier deployment has to be able to turn this down without rebuilding."""
        from app.config import load_config

        monkeypatch.setenv("ENTITY_TYPE_DERIVE_WORKERS", "2")
        assert load_config().entity_type_derive_workers == 2

    def test_a_lowered_knob_actually_bounds_the_pool(self, monkeypatch) -> None:
        import threading
        import time

        from app.ingest import entity_types

        monkeypatch.setattr(entity_types, "_derive_workers", lambda: 2)
        in_flight = 0
        peak = 0
        lock = threading.Lock()

        def watched(item):
            nonlocal in_flight, peak
            with lock:
                in_flight += 1
                peak = max(peak, in_flight)
            time.sleep(0.03)
            with lock:
                in_flight -= 1
            return [item]

        entity_types._map_ordered(watched, list(range(20)), stage="extract")

        assert peak == 2

    def test_a_zero_knob_does_not_stall_the_derivation(self, monkeypatch) -> None:
        """0 would otherwise mean a pool of no workers — a run that reports success having done
        nothing. Clamped to sequential instead."""
        from types import SimpleNamespace

        from app.ingest import entity_types

        # Config validates on assignment, so the knob cannot be mutated at runtime — only set at
        # boot. Patch the module's reference to stand in for a deployment booted with 0.
        monkeypatch.setattr(
            entity_types, "CONFIG", SimpleNamespace(entity_type_derive_workers=0)
        )
        assert entity_types._derive_workers() == 1
        assert entity_types._map_ordered(lambda i: [i], [1, 2, 3], stage="extract") == [
            [1],
            [2],
            [3],
        ]

    def test_a_bad_value_is_rejected_at_startup(self, monkeypatch) -> None:
        """Fail loudly at boot rather than silently falling back to a default an operator did not
        choose."""
        import pytest as _pytest

        from app.config import load_config

        monkeypatch.setenv("ENTITY_TYPE_DERIVE_WORKERS", "not-a-number")
        with _pytest.raises(ValueError):
            load_config()


class TestDerivationTaskModel:
    """Which model the derivation runs on. It defaults to the ingestion-pipeline model rather
    than the main one: this job is ingest-side, and the validated 9-type taxonomy for the
    reference wiki came from gpt-5.4-mini."""

    def test_the_model_reaches_run_derivation(self, monkeypatch) -> None:
        from app.ingest import entity_types as ingest_entity_types
        from app.tasks import entity_types as task_module

        seen: dict[str, object] = {}

        def fake_run(**kwargs):
            seen.update(kwargs)
            return {}

        monkeypatch.setattr(ingest_entity_types, "run_derivation", fake_run)
        task_module.derive_entity_types.fn(triggered_by_user_id="usr_1", model="gpt-5.4-mini")

        assert seen == {"triggered_by_user_id": "usr_1", "model": "gpt-5.4-mini"}

    def test_no_model_keeps_the_deployment_default(self, monkeypatch) -> None:
        """None must reach run_derivation as None, so client.complete resolves llm_settings —
        not be turned into an empty string, which would read as a configured-but-blank model."""
        from app.ingest import entity_types as ingest_entity_types
        from app.tasks import entity_types as task_module

        seen: dict[str, object] = {}
        monkeypatch.setattr(
            ingest_entity_types, "run_derivation", lambda **kw: seen.update(kw) or {}
        )
        task_module.derive_entity_types.fn()

        assert seen["model"] is None

    @staticmethod
    def _stub(monkeypatch, *, ingest_model: str) -> dict:
        """Stub the derivation down to the one thing under test: which model it resolves."""
        from app.ingest import entity_types
        from app.llm import settings as llm_settings

        seen: dict = {}
        artifact = {
            "entity_types": [{"name": "t", "definition": "d"}],
            "stats": {"n_mentions": 1, "n_referents": 1, "n_typed": 1, "n_types": 1},
        }
        monkeypatch.setattr(
            entity_types,
            "get_llm_settings",
            lambda: llm_settings.LLMSettings(model="main", ingest_selector_model=ingest_model),
        )
        monkeypatch.setattr(entity_types, "read_corpus", lambda prefix="": [("a.md", "body")])
        monkeypatch.setattr(
            entity_types,
            "derive",
            lambda pages, model=None: (seen.update(model=model), artifact)[1],
        )
        monkeypatch.setattr(entity_types, "store_taxonomy", lambda artifact, triggered_by=None: 1)
        return seen

    def test_defaults_to_the_ingestion_model(self, monkeypatch) -> None:
        """The wiring that makes the option useful: with nothing passed, the admin's
        ingestion-pipeline model is what runs."""
        from app.ingest import entity_types

        seen = self._stub(monkeypatch, ingest_model="ingest-cheap")
        entity_types.run_derivation()

        assert seen["model"] == "ingest-cheap"

    def test_an_explicit_model_wins(self, monkeypatch) -> None:
        from app.ingest import entity_types

        seen = self._stub(monkeypatch, ingest_model="ingest-cheap")
        entity_types.run_derivation(model="pinned")

        assert seen["model"] == "pinned"

    def test_no_ingestion_model_falls_back_to_the_deployment_default(self, monkeypatch) -> None:
        """Unset means no cheaper model was nominated: pass None so client.complete resolves the
        main model, rather than "" which would read as configured-but-blank."""
        from app.ingest import entity_types

        seen = self._stub(monkeypatch, ingest_model="")
        entity_types.run_derivation()

        assert seen["model"] is None


class TestNamingPartialAcceptance:
    """A partition violation costs only what is invalid: the named types survive, and uncovered or
    double-claimed members are handled without discarding the response."""

    @staticmethod
    def _group(n: int):
        from app.ingest.entity_types import Referent

        return [
            Referent(canonical=f"r{i}", variants=[f"r{i}"], pages={f"p{i}.md"})
            for i in range(n)
        ]

    @staticmethod
    def _stub(monkeypatch, payload):
        from app.ingest import entity_types

        monkeypatch.setattr(entity_types, "_complete_json", lambda *a, **k: payload)

    def test_one_uncovered_member_does_not_discard_the_group(self, monkeypatch, caplog) -> None:
        from app.ingest import entity_types

        group = self._group(10)
        # Covers members 1-9 of 10. Indices are 1-based (see ``_member_indices``).
        self._stub(
            monkeypatch,
            {
                "types": [
                    {
                        "type_name": "organization",
                        "definition": "d",
                        "member_indices": list(range(1, 10)),
                    }
                ]
            },
        )

        with caplog.at_level("WARNING"):
            out = entity_types.name_group(group)

        assert out[0].name == "organization"
        assert out[0].n_referents == 9
        assert out[1].name.startswith("unnamed")
        assert out[1].n_referents == 1

    def test_a_full_partition_is_unchanged(self, monkeypatch) -> None:
        from app.ingest import entity_types

        self._stub(
            monkeypatch,
            {
                "types": [
                    {"type_name": "person", "definition": "d", "member_indices": [1, 2]},
                    {"type_name": "organization", "definition": "d", "member_indices": [3]},
                ]
            },
        )

        out = entity_types.name_group(self._group(3))

        assert [t.name for t in out] == ["person", "organization"]
        assert sum(t.n_referents for t in out) == 3

    def test_a_repeated_member_keeps_the_first_assignment(self, monkeypatch, caplog) -> None:
        """A double-claimed member would inflate the support counts a type is judged on, so the
        duplicate is dropped — not the entry, and not the response."""
        from app.ingest import entity_types

        self._stub(
            monkeypatch,
            {
                "types": [
                    {"type_name": "person", "definition": "d", "member_indices": [1, 2]},
                    {"type_name": "organization", "definition": "d", "member_indices": [2, 3]},
                ]
            },
        )

        with caplog.at_level("WARNING"):
            out = entity_types.name_group(self._group(3))

        assert [t.name for t in out] == ["person", "organization"]
        assert [t.n_referents for t in out] == [2, 1]
        assert sum(t.n_referents for t in out) == 3

    def test_an_unusable_response_still_falls_back_to_the_whole_group(self, monkeypatch) -> None:
        from app.ingest import entity_types

        self._stub(monkeypatch, {"types": []})

        out = entity_types.name_group(self._group(5))

        assert len(out) == 1
        assert out[0].name.startswith("unnamed")
        assert out[0].n_referents == 5

    def test_remainders_from_different_groups_get_different_names(self, monkeypatch) -> None:
        """``derive`` collapses types sharing a name, so identically-sized remainders from
        unrelated groups would otherwise pool their referents and examples into one type."""
        from app.ingest import entity_types

        names = []
        for offset in (0, 100):
            group = [
                entity_types.Referent(canonical=f"r{offset + i}", pages={f"p{i}.md"})
                for i in range(3)
            ]
            self._stub(
                monkeypatch,
                {"types": [{"type_name": "person", "definition": "d", "member_indices": [1, 2]}]},
            )
            out = entity_types.name_group(group)
            names.append([t.name for t in out if t.name.startswith("unnamed")])

        assert names[0] != names[1], names

    def test_names_survive_truncation_of_long_canonicals(self, monkeypatch) -> None:
        """A readable slug has to be cut somewhere, so two long names sharing a prefix would
        collide on length alone. The digest is what keeps them apart."""
        from app.ingest import entity_types

        self._stub(monkeypatch, {"types": []})
        shared = "x" * 80
        first = entity_types.name_group(
            [entity_types.Referent(canonical=shared + "alpha", pages={"a.md"})]
        )
        second = entity_types.name_group(
            [entity_types.Referent(canonical=shared + "beta", pages={"b.md"})]
        )

        assert first[0].name != second[0].name

    def test_names_distinguish_canonicals_that_differ_only_by_punctuation(
        self, monkeypatch
    ) -> None:
        """Folding already merges these, so they should never be two referents — but the name
        must not depend on that invariant holding."""
        from app.ingest import entity_types

        self._stub(monkeypatch, {"types": []})
        first = entity_types.name_group(
            [entity_types.Referent(canonical="Acme-Foo", pages={"a.md"})]
        )
        second = entity_types.name_group(
            [entity_types.Referent(canonical="Acme Foo", pages={"b.md"})]
        )

        assert first[0].name != second[0].name

    def test_the_same_member_set_names_deterministically(self, monkeypatch) -> None:
        from app.ingest import entity_types

        self._stub(monkeypatch, {"types": []})

        def once():
            return entity_types.name_group(
                [entity_types.Referent(canonical="alpha", pages={"a.md"})]
            )[0].name

        assert once() == once()

    def test_a_whole_group_fallback_is_also_named_per_group(self, monkeypatch) -> None:
        from app.ingest import entity_types

        self._stub(monkeypatch, {"types": []})
        first = entity_types.name_group([entity_types.Referent(canonical="alpha", pages={"a.md"})])
        second = entity_types.name_group([entity_types.Referent(canonical="beta", pages={"b.md"})])

        assert first[0].name != second[0].name

    def test_referent_counts_stay_exact(self, monkeypatch) -> None:
        """The counts are what a type's support is judged on downstream, so they must sum to the
        group with nothing double-counted and nothing lost."""
        from app.ingest import entity_types

        self._stub(
            monkeypatch,
            {
                "types": [
                    {"type_name": "a", "definition": "d", "member_indices": [1, 2, 3]},
                    {"type_name": "b", "definition": "d", "member_indices": [3, 4]},
                ]
            },
        )

        out = entity_types.name_group(self._group(6))

        assert sum(t.n_referents for t in out) == 6


class TestMergeConvergence:
    """The merge loop is the only stage that sees the whole taxonomy, so how far it runs decides
    whether the result is a stable answer or wherever the round cap fell."""

    @staticmethod
    def _types(n: int):
        from app.ingest.entity_types import EntityType

        return [
            EntityType(name=f"t{i}", definition="d", examples=[], n_referents=1, n_docs=1)
            for i in range(n)
        ]

    def test_keeps_merging_while_the_count_falls(self, monkeypatch) -> None:
        """Three rounds stopped mid-descent on a real corpus, leaving single-referent types the
        prompt asks to fold."""
        from app.ingest import entity_types

        sizes = iter([40, 20, 10, 6, 5, 5])
        monkeypatch.setattr(
            entity_types, "_merge_once", lambda types, model=None: self._types(next(sizes))
        )

        out = entity_types.merge_types(self._types(80))

        assert len(out) == 5

    def test_stops_as_soon_as_a_round_collapses_nothing(self, monkeypatch) -> None:
        from app.ingest import entity_types

        calls = {"n": 0}

        def once(types, model=None):
            calls["n"] += 1
            return self._types(len(types))

        monkeypatch.setattr(entity_types, "_merge_once", once)
        entity_types.merge_types(self._types(9))

        assert calls["n"] == 1

    def test_warns_when_the_cap_binds_instead_of_converging(self, monkeypatch, caplog) -> None:
        """A capped run is not a converged one, and the difference was invisible before."""
        from app.ingest import entity_types

        monkeypatch.setattr(
            entity_types, "_merge_once", lambda types, model=None: self._types(len(types) - 1)
        )

        with caplog.at_level("WARNING"):
            entity_types.merge_types(self._types(100))

        assert any("still collapsing" in r.getMessage() for r in caplog.records)

    def test_a_failed_round_does_not_report_convergence(self, monkeypatch, caplog) -> None:
        """_merge_once returns None on a provider error or unusable response. Treating that as a
        round that merged nothing would persist the partially merged taxonomy as a stable
        answer — it is only the point the failure happened."""
        from app.ingest import entity_types

        results = iter([self._types(30), None])
        monkeypatch.setattr(
            entity_types, "_merge_once", lambda types, model=None: next(results)
        )

        with caplog.at_level("INFO"):
            out = entity_types.merge_types(self._types(50))

        messages = " ".join(r.getMessage() for r in caplog.records)
        assert len(out) == 30  # keeps the progress the successful round made
        assert "NOT converged" in messages
        assert "converged after" not in messages

    def test_too_few_types_to_merge_is_not_a_failure(self, monkeypatch, caplog) -> None:
        """Fewer than three types cannot be consolidated further; that is a clean stop, not an
        error, and must not warn."""
        from app.ingest import entity_types

        with caplog.at_level("WARNING"):
            out = entity_types.merge_types(self._types(2))

        assert len(out) == 2
        assert not [r for r in caplog.records if "NOT converged" in r.getMessage()]

    def test_reports_each_round(self, monkeypatch, caplog) -> None:
        from app.ingest import entity_types

        sizes = iter([30, 12, 12])
        monkeypatch.setattr(
            entity_types, "_merge_once", lambda types, model=None: self._types(next(sizes))
        )

        with caplog.at_level("INFO"):
            entity_types.merge_types(self._types(50))

        messages = " ".join(r.getMessage() for r in caplog.records)
        assert "50 -> 30" in messages and "30 -> 12" in messages
        assert "converged" in messages
