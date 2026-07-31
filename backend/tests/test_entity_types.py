"""The deterministic half of entity-type derivation.

Stages 5 and 6 are LLM calls and are not pinned here. Everything else is a pure function,
and those are exactly the parts that must not drift: they decide which referents count as
one thing, which carry signal, and which categories have enough evidence to exist. A change
in any of them silently changes the taxonomy, and the taxonomy keys facts by entity.
"""

from __future__ import annotations

from app.ingest.entity_types import (
    MIN_TYPE_REFERENTS,
    OTHER_TYPE,
    EntityType,
    Mention,
    apply_floor,
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


class TestApplyFloor:
    def _type(self, name: str, refs: int, docs: int = 5) -> EntityType:
        return EntityType(name=name, definition="d", examples=[name], n_referents=refs, n_docs=docs)

    def test_well_supported_types_survive(self) -> None:
        kept = apply_floor([self._type("organization", 120), self._type("person", 9)])
        assert [t.name for t in kept] == ["organization", "person"]

    def test_thin_types_fold_into_other(self) -> None:
        """One instance is a classification guess, not evidence that a category exists."""
        kept = apply_floor(
            [self._type("organization", 120), self._type("facility", 1), self._type("font", 2)]
        )
        assert [t.name for t in kept] == ["organization", OTHER_TYPE]
        other = kept[-1]
        assert other.n_referents == 3  # 1 + 2, nothing lost
        assert set(other.examples) == {"facility", "font"}

    def test_a_type_needs_spread_across_pages_too(self) -> None:
        """Three referents from a single page is one author's vocabulary, not a kind."""
        kept = apply_floor([self._type("local", MIN_TYPE_REFERENTS, docs=1)])
        assert [t.name for t in kept] == [OTHER_TYPE]

    def test_other_sorts_last(self) -> None:
        kept = apply_floor([self._type("thin", 1), self._type("organization", 50)])
        assert kept[-1].name == OTHER_TYPE
