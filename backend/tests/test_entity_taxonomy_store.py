"""Storage for derived entity-type taxonomies.

What matters here is that a re-derivation cannot quietly invalidate what came before.
Entity types are keys: anything recording facts per entity records them under a type name,
so a rename must leave the old taxonomy readable and must not leave two rows claiming to be
in force. Both are properties of the store, not of the derivation, so they are pinned here.
"""

from __future__ import annotations

import pytest

from app.db import entity_taxonomy


def _artifact(*, types: list[dict[str, object]] | None = None, fingerprint: str = "abc123") -> dict:
    return {
        "corpus_fingerprint": fingerprint,
        "provenance": {"model": "test-model", "group_similarity": 0.45},
        "stats": {"n_pages": 3, "n_types": 1},
        "entity_types": types
        if types is not None
        else [
            {
                "name": "organization",
                "definition": "A named company or institution.",
                "examples": ["Acme"],
                "n_referents": 12,
                "n_docs": 3,
            }
        ],
    }


class TestRecord:
    def test_first_taxonomy_is_id_one_and_active(self) -> None:
        assert entity_taxonomy.active() is None

        taxonomy_id = entity_taxonomy.record(_artifact())

        assert taxonomy_id == 1
        row = entity_taxonomy.active()
        assert row is not None
        assert row.id == 1
        assert row.corpus_fingerprint == "abc123"
        assert row.types[0]["name"] == "organization"
        assert row.provenance["group_similarity"] == 0.45

    def test_ids_increment_and_only_the_newest_is_active(self) -> None:
        entity_taxonomy.record(_artifact(fingerprint="first"))
        second = entity_taxonomy.record(_artifact(fingerprint="second"))

        assert second == 2
        active = entity_taxonomy.active()
        assert active is not None
        assert (active.id, active.corpus_fingerprint) == (2, "second")

    def test_a_superseded_taxonomy_stays_resolvable(self) -> None:
        """The whole point of append-only: facts keyed under the first taxonomy's type names
        must still resolve after a later one renames them."""
        entity_taxonomy.record(
            _artifact(types=[{"name": "software_product_or_service", "definition": "d"}])
        )
        entity_taxonomy.record(_artifact(types=[{"name": "software_product", "definition": "d"}]))

        old = entity_taxonomy.get(1)
        assert old is not None
        assert old.types[0]["name"] == "software_product_or_service"
        assert old.active is False

    def test_refuses_a_taxonomy_with_no_types(self) -> None:
        """An empty derivation is a failure, and recording it would deactivate a good one."""
        with pytest.raises(ValueError):
            entity_taxonomy.record(_artifact(types=[]))
        assert entity_taxonomy.active() is None

    def test_a_failed_record_leaves_the_previous_one_in_force(self) -> None:
        entity_taxonomy.record(_artifact(fingerprint="good"))
        with pytest.raises(ValueError):
            entity_taxonomy.record(_artifact(types=[]))

        active = entity_taxonomy.active()
        assert active is not None
        assert active.corpus_fingerprint == "good"


class TestRead:
    def test_get_unknown_id_is_none(self) -> None:
        assert entity_taxonomy.get(99) is None

    def test_history_is_newest_first(self) -> None:
        for n in range(3):
            entity_taxonomy.record(_artifact(fingerprint=f"c{n}"))
        assert [row.id for row in entity_taxonomy.history()] == [3, 2, 1]

    def test_history_respects_the_limit(self) -> None:
        for n in range(4):
            entity_taxonomy.record(_artifact(fingerprint=f"c{n}"))
        assert [row.id for row in entity_taxonomy.history(limit=2)] == [4, 3]


class TestLoadTaxonomy:
    """``load_taxonomy`` is what the extraction prompt reads, so its fallback matters as
    much as its happy path — a deployment that has never derived must still work."""

    def test_falls_back_to_generic_types_when_nothing_is_derived(self) -> None:
        from app.ingest.entity_types import DEFAULT_TYPES, load_taxonomy

        defs, home = load_taxonomy()

        assert defs == dict(DEFAULT_TYPES)
        assert home == frozenset()

    def test_reads_the_active_taxonomy(self) -> None:
        from app.ingest.entity_types import load_taxonomy

        entity_taxonomy.record(
            _artifact(types=[{"name": "person", "definition": "A named individual."}])
        )

        defs, _ = load_taxonomy()

        assert defs == {"person": "A named individual."}

    def test_reads_a_specific_taxonomy(self) -> None:
        """How a consumer resolves the types it keyed facts under, rather than assuming the
        active taxonomy still means the same thing."""
        from app.ingest.entity_types import load_taxonomy

        entity_taxonomy.record(_artifact(types=[{"name": "old_name", "definition": "d"}]))
        entity_taxonomy.record(_artifact(types=[{"name": "new_name", "definition": "d"}]))

        assert set(load_taxonomy(taxonomy_id=1)[0]) == {"old_name"}
        assert set(load_taxonomy()[0]) == {"new_name"}

    def test_names_to_skip_come_from_the_configured_organisation(self) -> None:
        """Types are derived and versioned; the organisation is configured. An admin knows it
        on day one, and a re-derivation over a thin corpus must not overwrite it."""
        from app.ingest import settings as ingest_settings
        from app.ingest.entity_types import load_taxonomy

        entity_taxonomy.record(_artifact())
        assert load_taxonomy()[1] == frozenset()

        ingest_settings.upsert(max_doc_chars=1000, onyx_base_url=None, organization_name="Acme")

        assert load_taxonomy()[1] == frozenset({"Acme"})

    def test_a_blank_organisation_name_is_not_a_skip_name(self) -> None:
        """Whitespace must not become a name the extractor is told to ignore."""
        from app.ingest import settings as ingest_settings
        from app.ingest.entity_types import load_taxonomy

        ingest_settings.upsert(max_doc_chars=1000, onyx_base_url=None, organization_name="   ")

        assert load_taxonomy()[1] == frozenset()

    def test_a_taxonomy_of_unusable_entries_falls_back(self) -> None:
        """Malformed rows should degrade to the generic list, not to an empty menu that
        would make the extractor unable to type anything."""
        from app.ingest.entity_types import DEFAULT_TYPES, load_taxonomy

        entity_taxonomy.record(_artifact(types=[{"name": "", "definition": ""}, {"nope": 1}]))

        assert load_taxonomy()[0] == dict(DEFAULT_TYPES)


class TestOrganizationNameSource:
    """The source column, not the value, gates inference.

    "Do not overwrite a non-null value" would freeze a bad guess forever; and an admin who
    deliberately CLEARS the name has decided there is none, which must not look like "unset"
    and re-trigger detection.
    """

    def _set(self, name: str | None, source: str) -> None:
        from app.ingest import settings as ingest_settings

        ingest_settings.set_organization_name(name, source=source)

    def test_inference_may_claim_an_unset_name(self) -> None:
        from app.ingest import settings as ingest_settings

        self._set("Acme", "inferred")

        assert ingest_settings.get_organization_name() == "Acme"
        assert ingest_settings.organization_name_is_admin_set() is False

    def test_inference_may_correct_its_own_earlier_guess(self) -> None:
        """A later derivation sees far more corpus; it should be allowed to improve on itself."""
        from app.ingest import settings as ingest_settings

        self._set("Acme", "inferred")
        self._set("Acme Industries", "inferred")

        assert ingest_settings.get_organization_name() == "Acme Industries"

    def test_inference_never_overwrites_an_admin(self) -> None:
        from app.ingest import settings as ingest_settings

        self._set("CBRE", "admin")
        self._set("Cbre", "inferred")

        assert ingest_settings.get_organization_name() == "CBRE"

    def test_an_admin_clearing_the_name_stops_inference(self) -> None:
        """("admin", NULL) means "there is no name" — not "nobody has said"."""
        from app.ingest import settings as ingest_settings

        self._set("Acme", "admin")
        self._set(None, "admin")

        assert ingest_settings.get_organization_name() is None
        assert ingest_settings.organization_name_is_admin_set() is True

        self._set("Guessed", "inferred")
        assert ingest_settings.get_organization_name() is None

    def test_an_admin_may_override_an_inferred_value(self) -> None:
        from app.ingest import settings as ingest_settings

        self._set("Acme", "inferred")
        self._set("Acme Industries GmbH", "admin")

        assert ingest_settings.get_organization_name() == "Acme Industries GmbH"
        assert ingest_settings.organization_name_is_admin_set() is True

    def test_upsert_stamps_an_admin_source(self) -> None:
        """A name arriving through the admin settings save is a human decision, so inference
        must not later overwrite it."""
        from app.ingest import settings as ingest_settings

        ingest_settings.upsert(max_doc_chars=1000, onyx_base_url=None, organization_name="Acme")

        assert ingest_settings.organization_name_is_admin_set() is True

    def test_whitespace_is_not_a_name(self) -> None:
        from app.ingest import settings as ingest_settings

        self._set("   ", "inferred")
        assert ingest_settings.get_organization_name() is None

    def test_an_unknown_source_is_rejected(self) -> None:
        from app.ingest import settings as ingest_settings

        with pytest.raises(ValueError):
            ingest_settings.set_organization_name("Acme", source="guessed")
