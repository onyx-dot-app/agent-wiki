"""Storage for per-page information needs.

The property that matters is the re-extract guard. Extraction is one LLM call per page, so a
guard that is too eager wastes money on an unchanged wiki and a guard that is too lax leaves
needs labelled with entity types the current taxonomy no longer defines. Both are pinned here,
along with pruning — needs of a deleted page would still cluster downstream, so a fact could
be routed to a page that is gone.

Rows are keyed by ``wiki_doc_ids.id``, so a rename must keep its needs; that is pinned here too,
since it is the reason for the key. These tests need a wiki repo as well as a database, because
minting and re-keying ids is the repo's job.
"""

from __future__ import annotations

from app.db import entity_taxonomy, page_needs
from app.wiki import doc_ids, git as wiki_git

# Every test here takes ``tmp_repo`` — the conftest fixture giving a migrated database AND an
# initialized wiki git repo. The database alone is not enough: rows key on a minted doc id, and
# ids are minted against the repo. (Without a fixture a test would hit the app's own configured
# connection, which in CI is a docker-compose hostname that does not resolve from the test job.)

def _page(path: str, body: str = "# P\n\nbody\n") -> None:
    wiki_git.commit_file(path, body, "seed", author=None)

NEEDS = [
    {
        "need_name": "deal status",
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
    def test_round_trips_a_page_s_needs(self, tmp_repo) -> None:
        page_needs.store("a.md", body="body", needs=NEEDS, model="gpt-5", taxonomy_id=None)

        row = page_needs.get("a.md")
        assert row is not None
        assert row.needs[0]["need_name"] == "deal status"
        assert row.needs[0]["entities"][0]["primary"] is True
        assert row.model == "gpt-5"

    def test_re_extraction_replaces_rather_than_appends(self, tmp_repo) -> None:
        """Current-valued, unlike the taxonomy store: a page's needs describe it as it is now,
        and nothing keys facts by a need, so there is nothing to orphan."""
        page_needs.store("a.md", body="v1", needs=NEEDS, model="m")
        page_needs.store("a.md", body="v2", needs=[], model="m")

        row = page_needs.get("a.md")
        assert row is not None
        assert row.needs == []

    def test_an_empty_need_list_is_stored(self, tmp_repo) -> None:
        """"This page tracks nothing durable" is a real answer, and storing it is what stops
        the page being re-extracted on every run."""
        page_needs.store("a.md", body="body", needs=[])

        assert page_needs.get("a.md") is not None
        assert page_needs.stale_paths([("a.md", "body")]) == []

    def test_unextracted_page_is_none(self, tmp_repo) -> None:
        assert page_needs.get("missing.md") is None

    def test_records_the_taxonomy_the_types_came_from(self, tmp_repo) -> None:
        taxonomy_id = _taxonomy()
        page_needs.store("a.md", body="body", needs=NEEDS, taxonomy_id=taxonomy_id)

        row = page_needs.get("a.md")
        assert row is not None
        assert row.taxonomy_id == taxonomy_id


class TestStalePaths:
    def test_an_unextracted_page_is_stale(self, tmp_repo) -> None:
        assert page_needs.stale_paths([("a.md", "body")]) == ["a.md"]

    def test_an_unchanged_page_is_not(self, tmp_repo) -> None:
        page_needs.store("a.md", body="body", needs=NEEDS, model="m", taxonomy_id=None)

        assert page_needs.stale_paths([("a.md", "body")], model="m") == []

    def test_an_edited_page_is_stale(self, tmp_repo) -> None:
        page_needs.store("a.md", body="body", needs=NEEDS, model="m")

        assert page_needs.stale_paths([("a.md", "edited")], model="m") == ["a.md"]

    def test_a_model_change_makes_every_page_stale(self, tmp_repo) -> None:
        """A different model extracts different needs, so stored ones stop being comparable."""
        page_needs.store("a.md", body="body", needs=NEEDS, model="mini")

        assert page_needs.stale_paths([("a.md", "body")], model="gpt-5") == ["a.md"]

    def test_a_re_derived_taxonomy_makes_every_page_stale(self, tmp_repo) -> None:
        """The subtle one. Entity labels were drawn from a type menu; a page skipped after a
        re-derivation keeps types the current taxonomy no longer defines — stale in a way no
        later step could detect."""
        first = _taxonomy("software_product_or_service")
        page_needs.store("a.md", body="body", needs=NEEDS, model="m", taxonomy_id=first)
        second = _taxonomy("software_product")

        assert page_needs.stale_paths([("a.md", "body")], model="m", taxonomy_id=second) == ["a.md"]

    def test_only_the_changed_pages_are_stale(self, tmp_repo) -> None:
        """What makes a re-run cost one call instead of a corpus."""
        for path in ("a.md", "b.md", "c.md"):
            page_needs.store(path, body=f"body-{path}", needs=NEEDS, model="m")

        stale = page_needs.stale_paths(
            [("a.md", "body-a.md"), ("b.md", "edited"), ("c.md", "body-c.md")], model="m"
        )
        assert stale == ["b.md"]

    def test_no_pages_no_query(self, tmp_repo) -> None:
        assert page_needs.stale_paths([]) == []


class TestLoadAllAndPrune:
    def test_load_all_returns_every_row_path_ordered(self, tmp_repo) -> None:
        """Downstream steps embed and cluster needs ACROSS pages, so they read the corpus
        whole rather than page by page."""
        for path in ("b.md", "a.md"):
            page_needs.store(path, body="body", needs=NEEDS)

        assert [row.path for row in page_needs.load_all()] == ["a.md", "b.md"]

    def test_prune_drops_needs_of_deleted_pages(self, tmp_repo) -> None:
        """Needs of a page that no longer exists would still cluster downstream, so a fact
        could be reconciled onto a page that is gone."""
        for path in ("a.md", "gone.md"):
            page_needs.store(path, body="body", needs=NEEDS)

        assert page_needs.prune({"a.md"}) == 1
        assert [row.path for row in page_needs.load_all()] == ["a.md"]

    def test_prune_is_a_no_op_when_nothing_is_gone(self, tmp_repo) -> None:
        page_needs.store("a.md", body="body", needs=NEEDS)

        assert page_needs.prune({"a.md", "unextracted.md"}) == 0

    def test_prune_leaves_everything_outside_the_prefix(self, tmp_repo) -> None:
        """A scoped caller's ``live_paths`` only describes its own scope, so an unscoped prune
        would read every other page as deleted and discard needs that cost a call each."""
        page_needs.store("keep/a.md", body="body", needs=NEEDS)
        page_needs.store("other/b.md", body="body", needs=NEEDS)

        assert page_needs.prune({"keep/a.md"}, prefix="keep") == 0
        assert sorted(row.path for row in page_needs.load_all()) == ["keep/a.md", "other/b.md"]

    def test_prune_within_a_prefix_still_drops_the_dead(self, tmp_repo) -> None:
        page_needs.store("keep/a.md", body="body", needs=NEEDS)
        page_needs.store("keep/gone.md", body="body", needs=NEEDS)

        assert page_needs.prune({"keep/a.md"}, prefix="keep") == 1
        assert [row.path for row in page_needs.load_all()] == ["keep/a.md"]

    def test_prune_scoping_is_a_path_boundary(self, tmp_repo) -> None:
        """Scoping to "team" must not sweep "teamwork.md"."""
        page_needs.store("teamwork.md", body="body", needs=NEEDS)

        assert page_needs.prune(set(), prefix="team") == 0
        assert [row.path for row in page_needs.load_all()] == ["teamwork.md"]

    def test_a_trailing_slash_on_the_prefix_behaves_the_same(self, tmp_repo) -> None:
        page_needs.store("keep/a.md", body="body", needs=NEEDS)

        assert page_needs.prune({"keep/a.md"}, prefix="keep/") == 0

    def test_delete_removes_one_page(self, tmp_repo) -> None:
        page_needs.store("a.md", body="body", needs=NEEDS)
        page_needs.delete("a.md")

        assert page_needs.get("a.md") is None


class TestDocIdKey:
    """Why rows key on ``wiki_doc_ids.id`` rather than path.

    A move re-keys the doc-id row in place, so a rename is content-preserving and must cost
    nothing. Path-keyed it would look like two events — a new page whose needs must be bought
    again, and a vanished one to prune — charging an LLM call per page for a reorganization.
    """

    def test_a_rename_keeps_the_needs_and_stays_unstale(self, tmp_repo) -> None:
        _page("old.md", "body")
        page_needs.store("old.md", body="body", needs=NEEDS, model="m")
        doc_id = doc_ids.id_for_path("old.md")

        _sha, moves = wiki_git.move_path("old.md", "new.md", "rename")
        doc_ids.on_path_moved(moves)

        # Same id, so the needs came along...
        assert doc_ids.id_for_path("new.md") == doc_id
        row = page_needs.get("new.md")
        assert row is not None
        assert row.needs[0]["need_name"] == "deal status"
        # ...and the page is not stale, so the rename costs no LLM call.
        assert page_needs.stale_paths([("new.md", "body")], model="m") == []

    def test_load_all_reports_the_new_path_after_a_rename(self, tmp_repo) -> None:
        """The path is joined from ``wiki_doc_ids``, not stored — a stored one would go stale on
        the first rename and never be refreshed, since the row is deliberately not re-extracted."""
        _page("old.md", "body")
        page_needs.store("old.md", body="body", needs=NEEDS)

        _sha, moves = wiki_git.move_path("old.md", "sub/new.md", "rename")
        doc_ids.on_path_moved(moves)

        assert [row.path for row in page_needs.load_all()] == ["sub/new.md"]

    def test_needs_are_readable_by_id_after_a_rename(self, tmp_repo) -> None:
        """What the stable key buys an outside reference: it holds an id, not a path."""
        _page("old.md", "body")
        doc_id = page_needs.store("old.md", body="body", needs=NEEDS)

        _sha, moves = wiki_git.move_path("old.md", "new.md", "rename")
        doc_ids.on_path_moved(moves)

        row = page_needs.get_by_doc_id(doc_id)
        assert row is not None
        assert row.needs[0]["need_name"] == "deal status"

    def test_a_renamed_page_is_not_pruned(self, tmp_repo) -> None:
        _page("old.md", "body")
        page_needs.store("old.md", body="body", needs=NEEDS)

        _sha, moves = wiki_git.move_path("old.md", "new.md", "rename")
        doc_ids.on_path_moved(moves)

        assert page_needs.prune({"new.md"}) == 0
        assert [row.path for row in page_needs.load_all()] == ["new.md"]

    def test_a_page_recreated_at_a_deleted_path_is_a_different_document(self, tmp_repo) -> None:
        """``wiki_doc_ids`` mints a fresh id for a recreated path, so the new page must not
        inherit the old one's needs — it is a different document that happens to share a name."""
        _page("a.md", "body")
        page_needs.store("a.md", body="body", needs=NEEDS, model="m")
        first_id = doc_ids.id_for_path("a.md")

        doc_ids.on_deleted("a.md")
        second_id = doc_ids.get_or_mint("a.md")
        assert second_id != first_id

        assert page_needs.get("a.md") is None
        assert page_needs.stale_paths([("a.md", "body")], model="m") == ["a.md"]
        # The abandoned row matches no live page, so pruning reclaims it.
        assert page_needs.prune({"a.md"}) == 1


class TestRepositoryBoundary:
    """Reads return detached records, not ORM rows — repos hand back plain data so the rest of
    the app does not depend on SQLAlchemy, and so no read can turn into a lazy load against a
    session that has already closed."""

    def test_reads_do_not_leak_orm_rows(self, tmp_repo) -> None:
        from app.db.models import PageNeeds as PageNeedsRow

        page_needs.store("a.md", body="body", needs=NEEDS)

        for value in (page_needs.get("a.md"), *page_needs.load_all()):
            assert isinstance(value, page_needs.StoredNeeds)
            assert not isinstance(value, PageNeedsRow)

    def test_the_record_carries_the_current_path(self, tmp_repo) -> None:
        page_needs.store("a.md", body="body", needs=NEEDS, model="m", taxonomy_id=None)

        row = page_needs.get("a.md")
        assert row is not None
        assert (row.path, row.model, row.taxonomy_id) == ("a.md", "m", None)
        assert row.doc_id == doc_ids.id_for_path("a.md")


class TestLifecycle:
    """Moves need no hook — ``wiki_doc_ids`` re-keys in place and the path is joined, so a rename
    is already correct (see ``TestDocIdKey``). Deletes are what need care: the row outlives the
    delete so a restore is free, which means reads must exclude it in the meantime."""

    def test_a_trashed_page_drops_out_of_load_all_immediately(self, tmp_repo) -> None:
        """Not merely at the next extraction. In that window its needs would still cluster and be
        reconciled against, routing a fact to a page nobody can see."""
        _page("a.md")
        _page("trashed.md")
        page_needs.store("a.md", body="body", needs=NEEDS)
        page_needs.store("trashed.md", body="body", needs=NEEDS)

        doc_ids.on_deleted("trashed.md")

        assert [row.path for row in page_needs.load_all()] == ["a.md"]

    def test_a_trashed_page_is_unreachable_by_path(self, tmp_repo) -> None:
        _page("a.md")
        page_needs.store("a.md", body="body", needs=NEEDS)

        doc_ids.on_deleted("a.md")

        assert page_needs.get("a.md") is None

    def test_a_restore_keeps_the_needs_it_already_paid_for(self, tmp_repo) -> None:
        """The row survives the delete, so trash-then-restore costs no LLM call."""
        _page("a.md")
        doc_id = page_needs.store("a.md", body="body", needs=NEEDS, model="m")

        doc_ids.on_deleted("a.md")
        doc_ids.on_restored(["a.md"])

        row = page_needs.get("a.md")
        assert row is not None
        assert row.doc_id == doc_id
        assert row.needs[0]["need_name"] == "deal status"
        assert page_needs.stale_paths([("a.md", "body")], model="m") == []

    def test_needs_stay_readable_by_id_while_trashed(self, tmp_repo) -> None:
        """``get_by_doc_id`` answers for a specific document rather than for a path, so it does
        not hide a trashed one — the caller asked for it."""
        _page("a.md")
        doc_id = page_needs.store("a.md", body="body", needs=NEEDS)

        doc_ids.on_deleted("a.md")

        assert page_needs.get_by_doc_id(doc_id) is not None


class TestTaxonomyLink:
    def test_losing_the_taxonomy_keeps_the_needs(self, tmp_repo) -> None:
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
        assert row.needs[0]["need_name"] == "deal status"

    def test_needs_orphaned_by_a_deleted_taxonomy_are_stale(self, tmp_repo) -> None:
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
