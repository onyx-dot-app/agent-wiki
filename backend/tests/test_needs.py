"""Need extraction — the parsing layer and the prompt's type menu.

The extraction judgment itself is an LLM call and is not pinned here. What is left is the
contract it has to satisfy, and every rule below exists because violating it is silent:
an off-menu entity type looks like a real type to everything downstream, two primaries make
a status row key on whichever mention parsed last, and an absent ``focus`` read as "generic"
would let a page accumulate entities it never asked about.
"""

from __future__ import annotations

import json

import pytest

from app.ingest import needs
from app.llm.client import CompletionResult

TYPE_DEFS = {
    "organization": "A named company or institution.",
    "software_product": "A named software product or service.",
}


def _need(**overrides) -> dict:
    base = {
        "aspect_name": "deal status and blockers",
        "need_kind": "entity_status",
        "description": "current status, blockers, and contact",
        "current_content": "Status: negotiation. Blocker: security review.",
        "focus": "specific",
    }
    return base | overrides


class TestBuildPrompt:
    def test_splices_the_derived_types_into_the_prompt(self) -> None:
        prompt = needs.build_prompt(TYPE_DEFS)

        assert "ENTITY_TYPES" not in prompt
        assert "- organization: A named company or institution." in prompt
        assert "- software_product: A named software product or service." in prompt

    def test_the_menu_is_the_deployment_s_own_types(self) -> None:
        """The point of deriving a taxonomy: the extractor's menu is not a hardcoded guess.

        Including the worked example — it names no concrete type, so a deployment tracking
        aircraft is never shown "organization" as a plausible label."""
        prompt = needs.build_prompt({"aircraft_model": "A named aircraft type."})

        assert "- aircraft_model: A named aircraft type." in prompt
        assert "organization" not in prompt


class TestParseNeed:
    def test_parses_a_well_formed_need(self) -> None:
        need = needs.parse_need(_need(cadence="weekly"), "needs[0]", TYPE_DEFS)

        assert need.aspect_name == "deal status and blockers"
        assert need.need_kind == "entity_status"
        assert need.cadence == "weekly"
        assert need.focus == "specific"

    def test_rejects_a_need_kind_off_the_closed_list(self) -> None:
        """Upstream left this open-vocabulary, which let a model put an entity TYPE here."""
        with pytest.raises(needs.SchemaError, match="need_kind"):
            needs.parse_need(_need(need_kind="organization"), "needs[0]", TYPE_DEFS)

    def test_requires_an_aspect_name_and_a_description(self) -> None:
        with pytest.raises(needs.SchemaError, match="aspect_name"):
            needs.parse_need(_need(aspect_name="  "), "needs[0]", TYPE_DEFS)
        with pytest.raises(needs.SchemaError, match="description"):
            needs.parse_need(_need(description=""), "needs[0]", TYPE_DEFS)

    def test_an_unusable_focus_falls_back_to_specific(self) -> None:
        """The fail-safe direction: admitting an entity a page never asked for is worse than
        omitting one, so absence must not mean "open"."""
        assert needs.parse_need(_need(focus="broad"), "n", TYPE_DEFS).focus == "specific"
        assert needs.parse_need(_need(focus=None), "n", TYPE_DEFS).focus == "specific"

    def test_generic_focus_survives(self) -> None:
        assert needs.parse_need(_need(focus="generic"), "n", TYPE_DEFS).focus == "generic"

    def test_empty_cadence_is_none_not_empty_string(self) -> None:
        """A timeline need's cadence is read as "is there one?", so "" must not answer yes."""
        assert needs.parse_need(_need(cadence=""), "n", TYPE_DEFS).cadence is None


class TestParseEntities:
    def test_types_entities_from_the_menu(self) -> None:
        need = needs.parse_need(
            _need(
                entities=[
                    {"canonical_name": "Acme", "entity_type": "organization", "primary": True},
                    {"canonical_name": "Acme Teams", "entity_type": "software_product"},
                ]
            ),
            "n",
            TYPE_DEFS,
        )

        assert [e.canonical_name for e in need.entities] == ["Acme", "Acme Teams"]
        assert need.primary_entity is not None
        assert need.primary_entity.canonical_name == "Acme"

    def test_an_off_menu_type_is_blanked_not_dropped(self) -> None:
        """The mention is still evidence the need is about something; losing the whole need
        over a bad label costs more than the label is worth."""
        need = needs.parse_need(
            _need(entities=[{"canonical_name": "Acme", "entity_type": "conglomerate"}]),
            "n",
            TYPE_DEFS,
        )

        assert len(need.entities) == 1
        assert need.entities[0].entity_type == ""

    def test_a_second_primary_is_demoted(self) -> None:
        """Otherwise a status row keys on whichever mention happened to parse last."""
        need = needs.parse_need(
            _need(
                entities=[
                    {"canonical_name": "Acme", "primary": True},
                    {"canonical_name": "Globex", "primary": True},
                ]
            ),
            "n",
            TYPE_DEFS,
        )

        assert [e.primary for e in need.entities] == [True, False]

    def test_no_primary_is_valid(self) -> None:
        """A need about no single subject — the prompt asks for none rather than a guess."""
        need = needs.parse_need(
            _need(entities=[{"canonical_name": "Acme"}, {"canonical_name": "Globex"}]),
            "n",
            TYPE_DEFS,
        )

        assert need.primary_entity is None

    def test_unnamed_and_malformed_entries_are_skipped(self) -> None:
        need = needs.parse_need(
            _need(entities=[{"canonical_name": "  "}, "Acme", {"entity_type": "organization"}]),
            "n",
            TYPE_DEFS,
        )

        assert need.entities == []

    def test_a_non_list_entities_field_is_not_fatal(self) -> None:
        assert needs.parse_need(_need(entities="Acme"), "n", TYPE_DEFS).entities == []


class TestExtractPage:
    """``extract_page`` owns the LLM boundary: everything it returns has been validated, and
    a page it cannot parse costs that page and nothing else."""

    def _stub(self, monkeypatch, *responses: str) -> list[list[dict]]:
        seen: list[list[dict]] = []

        def fake_complete(messages, **kwargs):
            seen.append(messages)
            return CompletionResult(text=responses[min(len(seen) - 1, len(responses) - 1)])

        monkeypatch.setattr(needs.client, "complete", fake_complete)
        return seen

    def test_extracts_needs_from_a_clean_response(self, monkeypatch) -> None:
        self._stub(monkeypatch, json.dumps({"needs": [_need(), _need(need_kind="timeline")]}))

        out = needs.extract_page("a.md", "body", type_defs=TYPE_DEFS)

        assert [n.need_kind for n in out] == ["entity_status", "timeline"]

    def test_tolerates_prose_around_the_json(self, monkeypatch) -> None:
        self._stub(monkeypatch, "Here you go:\n" + json.dumps({"needs": [_need()]}) + "\nDone.")

        assert len(needs.extract_page("a.md", "body", type_defs=TYPE_DEFS)) == 1

    def test_retries_once_with_the_rejection_reason(self, monkeypatch) -> None:
        """The model is told what was wrong — a bare retry would reproduce the same error."""
        seen = self._stub(
            monkeypatch,
            json.dumps({"needs": [_need(need_kind="nonsense")]}),
            json.dumps({"needs": [_need()]}),
        )

        out = needs.extract_page("a.md", "body", type_defs=TYPE_DEFS)

        assert len(out) == 1
        assert len(seen) == 2
        assert "REJECTED" in seen[1][-1]["content"]
        assert "need_kind" in seen[1][-1]["content"]

    def test_gives_up_after_the_retry_and_returns_nothing(self, monkeypatch) -> None:
        seen = self._stub(monkeypatch, "not json at all")

        assert needs.extract_page("a.md", "body", type_defs=TYPE_DEFS) == []
        assert len(seen) == 2

    def test_a_partial_response_yields_no_half_page(self, monkeypatch) -> None:
        """A page's needs are returned atomically, so a retry cannot append to a half-built
        list — one good need plus one bad one must not become one stored need."""
        self._stub(monkeypatch, json.dumps({"needs": [_need(), _need(aspect_name="")]}))

        assert needs.extract_page("a.md", "body", type_defs=TYPE_DEFS) == []

    def test_a_completion_failure_costs_only_that_page(self, monkeypatch) -> None:
        def boom(messages, **kwargs):
            raise RuntimeError("provider down")

        monkeypatch.setattr(needs.client, "complete", boom)

        assert needs.extract_page("a.md", "body", type_defs=TYPE_DEFS) == []

    def test_sends_the_page_whole_and_unchunked(self, monkeypatch) -> None:
        """A page seen in slices cannot be partitioned into the things it tracks, which is
        exactly what this step asks for."""
        body = "x" * 400_000
        seen = self._stub(monkeypatch, json.dumps({"needs": []}))

        needs.extract_page("a.md", body, type_defs=TYPE_DEFS)

        assert body in seen[0][-1]["content"]

    def test_an_empty_needs_list_is_a_valid_answer(self, monkeypatch) -> None:
        self._stub(monkeypatch, json.dumps({"needs": []}))

        assert needs.extract_page("a.md", "body", type_defs=TYPE_DEFS) == []


class TestOutputCap:
    """``current_content`` enumerates a page's entries rather than summarizing them, so the
    response scales with the page. The client's 4096 default truncates the densest pages, and
    truncation arrives as invalid JSON — indistinguishable from a page that tracks nothing."""

    def test_asks_for_more_than_the_client_default(self) -> None:
        from app.llm.client import DEFAULT_MAX_TOKENS

        # Measured over a 137-page production wiki: worst page ~8.8k output tokens, ~12.2k
        # under a weaker model. A cap at or below either would silently drop those pages.
        assert needs.MAX_OUTPUT_TOKENS >= 12_500
        assert needs.MAX_OUTPUT_TOKENS > DEFAULT_MAX_TOKENS

    def test_passes_the_cap_on_every_call(self, monkeypatch) -> None:
        seen: list[int] = []

        def fake_complete(messages, **kwargs):
            seen.append(kwargs.get("max_tokens", 0))
            return CompletionResult(text=json.dumps({"needs": [_need()]}))

        monkeypatch.setattr(needs.client, "complete", fake_complete)
        needs.extract_page("a.md", "body", type_defs=TYPE_DEFS)

        assert seen == [needs.MAX_OUTPUT_TOKENS]

    def test_truncation_is_logged_as_truncation(self, monkeypatch, caplog) -> None:
        """The fix for a truncated response is a bigger cap, not another prompt — so it must
        not be logged as a parse failure."""

        def fake_complete(messages, **kwargs):
            return CompletionResult(text='{"needs": [{"aspect', stop_reason="max_tokens")

        monkeypatch.setattr(needs.client, "complete", fake_complete)
        with caplog.at_level("WARNING"):
            assert needs.extract_page("dense.md", "body", type_defs=TYPE_DEFS) == []

        assert any("output cap" in r.getMessage() for r in caplog.records)

    def test_a_normal_stop_reason_logs_nothing_about_the_cap(self, monkeypatch, caplog) -> None:
        def fake_complete(messages, **kwargs):
            return CompletionResult(text=json.dumps({"needs": []}), stop_reason="stop")

        monkeypatch.setattr(needs.client, "complete", fake_complete)
        with caplog.at_level("WARNING"):
            needs.extract_page("a.md", "body", type_defs=TYPE_DEFS)

        assert not any("output cap" in r.getMessage() for r in caplog.records)
