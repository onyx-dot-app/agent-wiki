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
        assert normalize_surface("JIRA") == normalize_surface("Jira")
        assert normalize_surface("ROHDE&SCHWARZ") == normalize_surface("Rohde & Schwarz")

    def test_strips_legal_suffix_and_version(self) -> None:
        assert normalize_surface("Scania AB") == "scania"
        assert normalize_surface("Acme Corp") == "acme"
        assert normalize_surface("Onyx v4") == "onyx"

    def test_keeps_a_vendor_distinct_from_its_product(self) -> None:
        """The reason containment is not used to fold: a prefix relation is not identity."""
        assert normalize_surface("Microsoft") != normalize_surface("Microsoft Teams")

    def test_never_returns_empty(self) -> None:
        assert normalize_surface("!!!") == "!!!"


class TestCorpusArtifacts:
    def test_page_title_is_not_a_referent(self) -> None:
        assert is_corpus_artifact("Architecture", {"architecture"}) == "page_title"

    def test_code_identifier_is_not_a_referent(self) -> None:
        assert is_corpus_artifact("documents_queue", set()) == "code_identifier"

    def test_leaves_real_names_alone(self) -> None:
        """The snake_case rule is deliberately narrow — these are genuine referents."""
        for name in ("Next.js", "@scope/pkg", "react-native-mmkv", "Rohde & Schwarz"):
            assert is_corpus_artifact(name, set()) == ""


class TestFold:
    def test_variants_become_one_referent_with_pages_unioned(self) -> None:
        folded = fold(
            [
                Mention(surface="Jira", page="a.md"),
                Mention(surface="JIRA", page="b.md"),
                Mention(surface="Jira", page="c.md"),
            ]
        )
        assert len(folded) == 1
        assert folded[0].n_docs == 3
        assert folded[0].canonical == "Jira"  # most frequent spelling wins

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
            [Mention(surface="Zendesk", page="a.md"), Mention(surface="Freshdesk", page="a.md")]
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
