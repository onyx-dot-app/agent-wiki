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
        from app.ingest import entity_types

        seen: list[dict] = []

        def fake_complete(messages, **kwargs):
            seen.append({"messages": messages, "kwargs": kwargs})
            return results[min(len(seen) - 1, len(results) - 1)]

        monkeypatch.setattr(entity_types.client, "complete", fake_complete)
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
    """The task can pin a model. This job does not need the deployment's strongest one — the
    validated taxonomy for the reference wiki came from gpt-5.4-mini — and a less verbose model
    is safer here, since truncation costs a page's referents outright."""

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

    def test_the_task_does_not_read_llm_settings(self) -> None:
        """The model is passed in, never inferred from settings. Specifically it must not read
        ``ingest_selector_model``: that field is EMPTY when the ingest pre-filter is off, so
        deriving the taxonomy model from it would let an unrelated switch silently change which
        model builds the taxonomy. Checked against imports rather than text, so a docstring that
        explains the reasoning cannot satisfy it."""
        import ast
        import pathlib

        tree = ast.parse(pathlib.Path("app/tasks/entity_types.py").read_text())
        imported = {
            alias.name if isinstance(node, ast.Import) else f"{node.module}.{alias.name}"
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        assert not any("llm" in name for name in imported), imported
