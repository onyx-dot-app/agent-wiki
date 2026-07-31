"""Storage for derived entity-type taxonomies.

What matters here is that a re-derivation cannot quietly invalidate what came before.
Entity types are keys: anything recording facts per entity records them under a type name,
so a rename must leave the old taxonomy readable and must not leave two rows claiming to be
in force. Both are properties of the store, not of the derivation, so they are pinned here.
"""

from __future__ import annotations

import pytest

from app.db import entity_type_taxonomy

# Every test here takes ``tmp_db`` — the conftest fixture that declares "this test needs a
# migrated database". Without it a test runs against the app's own configured connection,
# which in CI is a docker-compose hostname that does not resolve from the test job.


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
    def test_first_taxonomy_is_id_one_and_active(self, tmp_db) -> None:
        assert entity_type_taxonomy.active() is None

        entity_type_taxonomy_id = entity_type_taxonomy.record(_artifact())

        assert entity_type_taxonomy_id == 1
        row = entity_type_taxonomy.active()
        assert row is not None
        assert row.id == 1
        assert row.corpus_fingerprint == "abc123"
        assert row.types[0]["name"] == "organization"
        assert row.provenance["group_similarity"] == 0.45

    def test_ids_increment_and_only_the_newest_is_active(self, tmp_db) -> None:
        entity_type_taxonomy.record(_artifact(fingerprint="first"))
        second = entity_type_taxonomy.record(_artifact(fingerprint="second"))

        assert second == 2
        active = entity_type_taxonomy.active()
        assert active is not None
        assert (active.id, active.corpus_fingerprint) == (2, "second")

    def test_a_superseded_taxonomy_stays_resolvable(self, tmp_db) -> None:
        """The whole point of append-only: facts keyed under the first taxonomy's type names
        must still resolve after a later one renames them."""
        entity_type_taxonomy.record(
            _artifact(types=[{"name": "software_product_or_service", "definition": "d"}])
        )
        entity_type_taxonomy.record(_artifact(types=[{"name": "software_product", "definition": "d"}]))

        old = entity_type_taxonomy.get(1)
        assert old is not None
        assert old.types[0]["name"] == "software_product_or_service"
        assert old.active is False

    def test_refuses_a_taxonomy_with_no_types(self, tmp_db) -> None:
        """An empty derivation is a failure, and recording it would deactivate a good one."""
        with pytest.raises(ValueError):
            entity_type_taxonomy.record(_artifact(types=[]))
        assert entity_type_taxonomy.active() is None

    def test_a_failed_record_leaves_the_previous_one_in_force(self, tmp_db) -> None:
        entity_type_taxonomy.record(_artifact(fingerprint="good"))
        with pytest.raises(ValueError):
            entity_type_taxonomy.record(_artifact(types=[]))

        active = entity_type_taxonomy.active()
        assert active is not None
        assert active.corpus_fingerprint == "good"


class TestRead:
    def test_get_unknown_id_is_none(self, tmp_db) -> None:
        assert entity_type_taxonomy.get(99) is None

    def test_history_is_newest_first(self, tmp_db) -> None:
        for n in range(3):
            entity_type_taxonomy.record(_artifact(fingerprint=f"c{n}"))
        assert [row.id for row in entity_type_taxonomy.history()] == [3, 2, 1]

    def test_history_respects_the_limit(self, tmp_db) -> None:
        for n in range(4):
            entity_type_taxonomy.record(_artifact(fingerprint=f"c{n}"))
        assert [row.id for row in entity_type_taxonomy.history(limit=2)] == [4, 3]


class TestLoadTaxonomy:
    """``load_taxonomy`` is what the extractor's type menu comes from, so its fallback
    matters as much as its happy path — a deployment that has never derived must still
    work."""

    def test_falls_back_to_generic_types_when_nothing_is_derived(self, tmp_db) -> None:
        from app.ingest.entity_types import DEFAULT_TYPES, load_taxonomy

        assert load_taxonomy() == dict(DEFAULT_TYPES)

    def test_reads_the_active_taxonomy(self, tmp_db) -> None:
        from app.ingest.entity_types import load_taxonomy

        entity_type_taxonomy.record(
            _artifact(types=[{"name": "person", "definition": "A named individual."}])
        )

        assert load_taxonomy() == {"person": "A named individual."}

    def test_reads_a_specific_taxonomy(self, tmp_db) -> None:
        """How a consumer resolves the types it keyed facts under, rather than assuming the
        active taxonomy still means the same thing."""
        from app.ingest.entity_types import load_taxonomy

        entity_type_taxonomy.record(_artifact(types=[{"name": "old_name", "definition": "d"}]))
        entity_type_taxonomy.record(_artifact(types=[{"name": "new_name", "definition": "d"}]))

        assert set(load_taxonomy(entity_type_taxonomy_id=1)) == {"old_name"}
        assert set(load_taxonomy()) == {"new_name"}

    def test_a_taxonomy_of_unusable_entries_falls_back(self, tmp_db) -> None:
        """Malformed rows should degrade to the generic list, not to an empty menu that
        would make the extractor unable to type anything."""
        from app.ingest.entity_types import DEFAULT_TYPES, load_taxonomy

        entity_type_taxonomy.record(_artifact(types=[{"name": "", "definition": ""}, {"nope": 1}]))

        assert load_taxonomy() == dict(DEFAULT_TYPES)
