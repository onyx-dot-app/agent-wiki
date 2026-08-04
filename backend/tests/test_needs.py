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
from app.ingest.needs import Focus, NeedKind
from app.llm.client import CompletionResult

TYPE_DEFS = {
    "organization": "A named company or institution.",
    "software_product": "A named software product or service.",
}


def _need(**overrides) -> dict:
    base = {
        "need_name": "deal status and blockers",
        "need_kind": "entity_status",
        "description": "current status, blockers, and contact",
        "detail_level": "one status line + a short blockers list",
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

    def test_asks_for_a_verbatim_update_instruction(self) -> None:
        prompt = needs.build_prompt(TYPE_DEFS)

        assert "update_instruction" in prompt
        assert "VERBATIM" in prompt
        # The guard that matters: an invented instruction would be obeyed as a human directive.
        assert "do NOT invent" in prompt

    def test_does_not_cap_how_many_entities_a_need_may_have(self) -> None:
        """The prompt used to hint "usually 0-3", which suppresses entities on exactly the pages
        that name many — a tracker with a row per customer is about every one of them."""
        prompt = needs.build_prompt(TYPE_DEFS)

        assert "0-3" not in prompt
        assert "EVERY one the need is about" in prompt


class TestParseNeed:
    def test_parses_a_well_formed_need(self) -> None:
        need = needs.parse_need(_need(), "needs[0]", TYPE_DEFS)

        assert need.need_name == "deal status and blockers"
        assert need.need_kind is NeedKind.ENTITY_STATUS
        assert need.detail_level == "one status line + a short blockers list"
        assert need.focus is Focus.SPECIFIC

    def test_rejects_a_need_kind_off_the_closed_list(self) -> None:
        """Upstream left this open-vocabulary, which let a model put an entity TYPE here."""
        with pytest.raises(needs.SchemaError, match="need_kind"):
            needs.parse_need(_need(need_kind="organization"), "needs[0]", TYPE_DEFS)

    def test_requires_a_need_name_and_a_description(self) -> None:
        with pytest.raises(needs.SchemaError, match="need_name"):
            needs.parse_need(_need(need_name="  "), "needs[0]", TYPE_DEFS)
        with pytest.raises(needs.SchemaError, match="description"):
            needs.parse_need(_need(description=""), "needs[0]", TYPE_DEFS)

    def test_an_unusable_focus_falls_back_to_specific(self) -> None:
        """The fail-safe direction: admitting an entity a page never asked for is worse than
        omitting one, so absence must not mean "open"."""
        assert needs.parse_need(_need(focus="broad"), "n", TYPE_DEFS).focus is Focus.SPECIFIC
        assert needs.parse_need(_need(focus=None), "n", TYPE_DEFS).focus is Focus.SPECIFIC

    def test_generic_focus_survives(self) -> None:
        assert needs.parse_need(_need(focus="generic"), "n", TYPE_DEFS).focus is Focus.GENERIC

    def test_captures_an_update_instruction(self) -> None:
        need = needs.parse_need(
            _need(update_instruction="Add each Friday's notes as a new dated section, newest first."),
            "n",
            TYPE_DEFS,
        )

        assert need.update_instruction == "Add each Friday's notes as a new dated section, newest first."

    def test_no_instruction_is_empty_not_invented(self) -> None:
        """Most pages state no maintenance rule, and a fabricated one would be obeyed as though a
        human wrote it — worse than having none. Absence must survive parsing as absence."""
        assert needs.parse_need(_need(), "n", TYPE_DEFS).update_instruction == ""
        assert needs.parse_need(_need(update_instruction=None), "n", TYPE_DEFS).update_instruction == ""
        assert needs.parse_need(_need(update_instruction="   "), "n", TYPE_DEFS).update_instruction == ""

    def test_an_instruction_is_independent_of_detail_level(self) -> None:
        """They answer different questions: detail_level is inferred from the entries already
        there, an instruction is the author's directive and can constrain placement or admissible
        sources, which no amount of reading the content reveals."""
        need = needs.parse_need(
            _need(
                detail_level="one line per report",
                update_instruction="Each line should include the source, the customer, and the date.",
            ),
            "n",
            TYPE_DEFS,
        )

        assert need.detail_level == "one line per report"
        assert need.update_instruction.startswith("Each line should include")

    def test_the_instruction_reaches_stored_json(self) -> None:
        need = needs.parse_need(_need(update_instruction="Newest first."), "n", TYPE_DEFS)

        assert need.model_dump(mode="json")["update_instruction"] == "Newest first."

    @pytest.mark.parametrize("bad", [42, True, ["a", "b"], {"k": "v"}, 3.5])
    def test_a_non_string_instruction_is_dropped_not_stringified(self, bad) -> None:
        """str(42) would store "42" and str(["a"]) "['a']" as though the page had written it. This
        field is kept verbatim so it can be treated as the author's own directive, which makes a
        coerced value worse than no value."""
        need = needs.parse_need(_need(update_instruction=bad), "n", TYPE_DEFS)

        assert need.update_instruction == ""

    @pytest.mark.parametrize("field", ["detail_level", "current_content"])
    def test_a_non_string_optional_field_is_dropped(self, field) -> None:
        """Dropped rather than raised: losing one advisory value beats discarding a whole page's
        needs."""
        need = needs.parse_need(_need(**{field: ["x"]}), "n", TYPE_DEFS)

        assert getattr(need, field) == ""

    @pytest.mark.parametrize("field", ["need_name", "description"])
    def test_a_non_string_required_field_reaches_the_retry_path(self, field) -> None:
        """The required fields already reject emptiness, so a dropped non-string lands there — and
        the message says "non-empty string" rather than "required", which would send the model
        looking for a field it did send."""
        with pytest.raises(needs.SchemaError, match="non-empty string"):
            needs.parse_need(_need(**{field: 42}), "n", TYPE_DEFS)

    def test_a_non_string_entity_name_is_skipped(self) -> None:
        """Same coercion in the entity list would have minted an entity literally named "42"."""
        need = needs.parse_need(
            _need(entities=[{"canonical_name": 42}, {"canonical_name": "Acme"}]),
            "n",
            TYPE_DEFS,
        )

        assert [e.canonical_name for e in need.entities] == ["Acme"]

    def test_ignores_fields_the_schema_no_longer_carries(self) -> None:
        """``cadence`` was dropped: it was populated on 9-18% of needs, missed 30/43 timelines
        on a weaker model, leaked onto kinds where it means nothing, and restated
        ``detail_level`` in prose. A stale key in a response must not break parsing."""
        need = needs.parse_need(_need(cadence="weekly"), "n", TYPE_DEFS)

        assert need.need_name == "deal status and blockers"
        assert not hasattr(need, "cadence")

    def test_the_model_itself_refuses_an_invalid_kind(self) -> None:
        """The enum is the guarantee, not just the parser: nothing can construct a need whose
        kind nothing downstream knows how to apply."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            needs.InformationNeed(need_name="a", need_kind="organization", description="d")

    def test_serializes_to_plain_json_strings(self) -> None:
        """What lands in JSONB. A str-subclass enum would round-trip anyway, but the stored shape
        should not depend on that."""
        need = needs.parse_need(_need(focus="generic"), "n", TYPE_DEFS)

        dumped = need.model_dump(mode="json")
        assert dumped["need_kind"] == "entity_status"
        assert dumped["focus"] == "generic"
        assert json.loads(json.dumps(dumped))["need_kind"] == "entity_status"


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

        assert [n.need_kind for n in out] == [NeedKind.ENTITY_STATUS, NeedKind.TIMELINE]

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
        self._stub(monkeypatch, json.dumps({"needs": [_need(), _need(need_name="")]}))

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

    @pytest.mark.parametrize(
        "stop_reason",
        [
            "max_tokens",  # anthropic, bedrock, gemini (MAX_TOKENS)
            "length",  # ollama, custom (OpenAI-compatible finish_reason)
            "incomplete",  # openai Responses API reports status, not a stop_reason
        ],
    )
    def test_truncation_is_logged_as_truncation(self, monkeypatch, caplog, stop_reason) -> None:
        """The fix for a truncated response is a bigger cap, not another prompt — so it must not
        be logged as a parse failure.

        Every provider's own vocabulary counts, and "incomplete" is the one that matters most:
        it is what the OpenAI Responses API reports, and OpenAI is the provider a truncated
        response was actually observed on. Matching only the other two made this silent exactly
        where it was needed."""

        def fake_complete(messages, **kwargs):
            return CompletionResult(text='{"needs": [{"need_', stop_reason=stop_reason)

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
