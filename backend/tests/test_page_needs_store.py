"""Storage for per-page information needs.

The property that matters is the re-extract guard. Extraction is one LLM call per page, so a
guard that is too eager wastes money on an unchanged wiki and a guard that is too lax leaves
needs labelled with entity types the current taxonomy no longer defines. Both are pinned here,
along with pruning — needs of a deleted page would still cluster downstream, so a fact could
be routed to a page that is gone.
"""

from __future__ import annotations

from app.db import entity_taxonomy, page_needs

# Every test here takes ``tmp_db`` — the conftest fixture that declares "this test needs a
# migrated database". Without it a test runs against the app's own configured connection,
# which in CI is a docker-compose hostname that does not resolve from the test job.

NEEDS = [
    {
        "aspect_name": "deal status",
        "need_kind": "entity_status",
        "description": "status and blockers",
        "entities": [{"canonical_name": "Acme", "entity_type": "organization", "primary": True}],
        "focus": "specific",
    }
]


def _taxonomy(name: str = "organization") -> int:
    return entity_taxonomy.record(
        {
            "corpus_fingerprint": "abc",
            "entity_types": [{"name": name, "definition": "A named company."}],
        }
    )


class TestStoreAndGet:
    def test_round_trips_a_page_s_needs(self, tmp_db) -> None:
        page_needs.store("a.md", body="body", needs=NEEDS, model="gpt-5", taxonomy_id=None)

        row = page_needs.get("a.md")
        assert row is not None
        assert row.needs[0]["aspect_name"] == "deal status"
        assert row.needs[0]["entities"][0]["primary"] is True
        assert row.model == "gpt-5"

    def test_re_extraction_replaces_rather_than_appends(self, tmp_db) -> None:
        """Current-valued, unlike the taxonomy store: a page's needs describe it as it is now,
        and nothing keys facts by a need, so there is nothing to orphan."""
        page_needs.store("a.md", body="v1", needs=NEEDS, model="m")
        page_needs.store("a.md", body="v2", needs=[], model="m")

        row = page_needs.get("a.md")
        assert row is not None
        assert row.needs == []

    def test_an_empty_need_list_is_stored(self, tmp_db) -> None:
        """"This page tracks nothing durable" is a real answer, and storing it is what stops
        the page being re-extracted on every run."""
        page_needs.store("a.md", body="body", needs=[])

        assert page_needs.get("a.md") is not None
        assert page_needs.stale_paths([("a.md", "body")]) == []

    def test_unextracted_page_is_none(self, tmp_db) -> None:
        assert page_needs.get("missing.md") is None

    def test_records_the_taxonomy_the_types_came_from(self, tmp_db) -> None:
        taxonomy_id = _taxonomy()
        page_needs.store("a.md", body="body", needs=NEEDS, taxonomy_id=taxonomy_id)

        row = page_needs.get("a.md")
        assert row is not None
        assert row.taxonomy_id == taxonomy_id


class TestStalePaths:
    def test_an_unextracted_page_is_stale(self, tmp_db) -> None:
        assert page_needs.stale_paths([("a.md", "body")]) == ["a.md"]

    def test_an_unchanged_page_is_not(self, tmp_db) -> None:
        page_needs.store("a.md", body="body", needs=NEEDS, model="m", taxonomy_id=None)

        assert page_needs.stale_paths([("a.md", "body")], model="m") == []

    def test_an_edited_page_is_stale(self, tmp_db) -> None:
        page_needs.store("a.md", body="body", needs=NEEDS, model="m")

        assert page_needs.stale_paths([("a.md", "edited")], model="m") == ["a.md"]

    def test_a_model_change_makes_every_page_stale(self, tmp_db) -> None:
        """A different model extracts different needs, so stored ones stop being comparable."""
        page_needs.store("a.md", body="body", needs=NEEDS, model="mini")

        assert page_needs.stale_paths([("a.md", "body")], model="gpt-5") == ["a.md"]

    def test_a_re_derived_taxonomy_makes_every_page_stale(self, tmp_db) -> None:
        """The subtle one. Entity labels were drawn from a type menu; a page skipped after a
        re-derivation keeps types the current taxonomy no longer defines — stale in a way no
        later step could detect."""
        first = _taxonomy("software_product_or_service")
        page_needs.store("a.md", body="body", needs=NEEDS, model="m", taxonomy_id=first)
        second = _taxonomy("software_product")

        assert page_needs.stale_paths([("a.md", "body")], model="m", taxonomy_id=second) == ["a.md"]

    def test_only_the_changed_pages_are_stale(self, tmp_db) -> None:
        """What makes a re-run cost one call instead of a corpus."""
        for path in ("a.md", "b.md", "c.md"):
            page_needs.store(path, body=f"body-{path}", needs=NEEDS, model="m")

        stale = page_needs.stale_paths(
            [("a.md", "body-a.md"), ("b.md", "edited"), ("c.md", "body-c.md")], model="m"
        )
        assert stale == ["b.md"]

    def test_no_pages_no_query(self, tmp_db) -> None:
        assert page_needs.stale_paths([]) == []


class TestLoadAllAndPrune:
    def test_load_all_returns_every_row_path_ordered(self, tmp_db) -> None:
        """Downstream steps embed and cluster needs ACROSS pages, so they read the corpus
        whole rather than page by page."""
        for path in ("b.md", "a.md"):
            page_needs.store(path, body="body", needs=NEEDS)

        assert [row.path for row in page_needs.load_all()] == ["a.md", "b.md"]

    def test_prune_drops_needs_of_deleted_pages(self, tmp_db) -> None:
        """Needs of a page that no longer exists would still cluster downstream, so a fact
        could be reconciled onto a page that is gone."""
        for path in ("a.md", "gone.md"):
            page_needs.store(path, body="body", needs=NEEDS)

        assert page_needs.prune({"a.md"}) == 1
        assert [row.path for row in page_needs.load_all()] == ["a.md"]

    def test_prune_is_a_no_op_when_nothing_is_gone(self, tmp_db) -> None:
        page_needs.store("a.md", body="body", needs=NEEDS)

        assert page_needs.prune({"a.md", "unextracted.md"}) == 0

    def test_delete_removes_one_page(self, tmp_db) -> None:
        page_needs.store("a.md", body="body", needs=NEEDS)
        page_needs.delete("a.md")

        assert page_needs.get("a.md") is None


class TestTaxonomyLink:
    def test_losing_the_taxonomy_keeps_the_needs(self, tmp_db) -> None:
        """ON DELETE SET NULL, not CASCADE: losing a taxonomy costs the ability to resolve
        type names, not the extracted needs themselves."""
        from sqlalchemy import delete as sa_delete

        from app.db.models import EntityTaxonomy
        from app.db.session import session

        taxonomy_id = _taxonomy()
        page_needs.store("a.md", body="body", needs=NEEDS, model="m", taxonomy_id=taxonomy_id)

        with session() as s:
            s.execute(sa_delete(EntityTaxonomy).where(EntityTaxonomy.id == taxonomy_id))

        row = page_needs.get("a.md")
        assert row is not None
        assert row.taxonomy_id is None
        assert row.needs[0]["aspect_name"] == "deal status"

    def test_needs_orphaned_by_a_deleted_taxonomy_are_stale(self, tmp_db) -> None:
        """The recovery path: a NULL link can no longer match a live taxonomy, so the next
        run re-extracts the page instead of leaving unresolvable type names in place."""
        from sqlalchemy import delete as sa_delete

        from app.db.models import EntityTaxonomy
        from app.db.session import session

        first = _taxonomy()
        page_needs.store("a.md", body="body", needs=NEEDS, model="m", taxonomy_id=first)
        with session() as s:
            s.execute(sa_delete(EntityTaxonomy).where(EntityTaxonomy.id == first))
        second = _taxonomy()

        assert page_needs.stale_paths([("a.md", "body")], model="m", taxonomy_id=second) == ["a.md"]
