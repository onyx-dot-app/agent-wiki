"""Storage for the derived topic layer.

Five tables rather than one document because of two properties nesting cannot give: an aspect
belongs to more than one topic, and a page reference is a real foreign key. Both are pinned here,
along with the ones a re-derivation could quietly break — exactly one run in force, superseded
runs still readable, and a fingerprint that answers "have the needs moved?".
"""

from __future__ import annotations

import pytest

from app.db import topic_map
from app.wiki import doc_ids, git as wiki_git


def _page(path: str) -> str:
    wiki_git.commit_file(path, "# P\n\nbody\n", "seed", author=None)
    return doc_ids.get_or_mint(path)


def _aspect(name: str, pages: list[str], *, key: str | None = None, **over) -> dict:
    return {
        "key": key or name,
        "name": name,
        "description": "what is tracked",
        "aspect_kind": "entity_status",
        "detail_level": "one line each",
        "focus": "specific",
        "pages": [{"doc_id": d, "need_name": name, "entity": ""} for d in pages],
    } | over


def _artifact(*, topics=None, fingerprint="abc123", taxonomy_id=None) -> dict:
    return {
        "corpus_fingerprint": fingerprint,
        "entity_type_taxonomy_id": taxonomy_id,
        "provenance": {"model": "test-model", "cluster_similarity": 0.60},
        "stats": {"n_needs": 172},
        "topics": topics if topics is not None else [],
    }


class TestRecord:
    def test_stores_a_topic_with_its_aspects_and_pages(self, tmp_repo) -> None:
        d1, d2 = _page("a.md"), _page("b.md")
        run_id = topic_map.record(
            _artifact(
                topics=[
                    {
                        "name": "Wiki Auto Management",
                        "description": "AI-managed wiki structure.",
                        "aspects": [_aspect("implementation status", [d1, d2])],
                    }
                ]
            )
        )

        loaded = topic_map.active()
        assert loaded is not None
        assert loaded.run_id == run_id
        assert [t.name for t in loaded.topics] == ["Wiki Auto Management"]
        aspect = loaded.topics[0].aspects[0]
        assert aspect.name == "implementation status"
        assert sorted(p.doc_id for p in aspect.pages) == sorted([d1, d2])
        assert aspect.spans_pages

    def test_an_aspect_can_belong_to_two_topics(self, tmp_repo) -> None:
        """The reason this is tables and not nested JSON: one facet, two subjects, ONE row — not
        two copies with duplicated page lists and no link between them."""
        d1 = _page("a.md")
        shared = _aspect("implementation status", [d1], key="impl")
        topic_map.record(
            _artifact(
                topics=[
                    {"name": "Wiki Auto Management", "aspects": [shared]},
                    {"name": "Craft Integration", "aspects": [shared]},
                ]
            )
        )

        loaded = topic_map.active()
        assert loaded is not None
        assert len(loaded.topics) == 2
        first, second = (t.aspects[0] for t in loaded.topics)
        assert first.aspect_id == second.aspect_id  # the same aspect, not a copy
        assert first.pages == second.pages

    def test_a_single_page_aspect_is_not_a_failure(self, tmp_repo) -> None:
        """Most of what a page tracks is its own; only a minority of facets span pages."""
        d1 = _page("a.md")
        topic_map.record(_artifact(topics=[{"name": "T", "aspects": [_aspect("solo", [d1])]}]))

        loaded = topic_map.active()
        assert loaded is not None
        assert not loaded.topics[0].aspects[0].spans_pages

    def test_only_the_newest_run_is_active(self, tmp_repo) -> None:
        d1 = _page("a.md")
        topic_map.record(_artifact(fingerprint="first", topics=[{"name": "T", "aspects": [_aspect("x", [d1])]}]))
        second = topic_map.record(
            _artifact(fingerprint="second", topics=[{"name": "T", "aspects": [_aspect("x", [d1])]}])
        )

        loaded = topic_map.active()
        assert loaded is not None
        assert (loaded.run_id, loaded.corpus_fingerprint) == (second, "second")

    def test_a_superseded_run_stays_readable(self, tmp_repo) -> None:
        """Ids are stable only WITHIN a run, so a consumer that recorded a decision against an
        aspect resolves it through the run it belongs to."""
        d1 = _page("a.md")
        first = topic_map.record(_artifact(topics=[{"name": "Auto Management", "aspects": [_aspect("x", [d1])]}]))
        topic_map.record(_artifact(topics=[{"name": "Wiki Auto Management", "aspects": [_aspect("x", [d1])]}]))

        old = topic_map.get(first)
        assert old is not None
        assert [t.name for t in old.topics] == ["Auto Management"]

    def test_refuses_a_run_with_no_topics(self, tmp_repo) -> None:
        """An empty derivation is a failure, and recording it would deactivate a good run."""
        with pytest.raises(ValueError):
            topic_map.record(_artifact(topics=[]))
        assert topic_map.active() is None

    def test_a_failed_record_leaves_the_previous_run_in_force(self, tmp_repo) -> None:
        d1 = _page("a.md")
        topic_map.record(_artifact(fingerprint="good", topics=[{"name": "T", "aspects": [_aspect("x", [d1])]}]))
        with pytest.raises(ValueError):
            topic_map.record(_artifact(topics=[]))

        loaded = topic_map.active()
        assert loaded is not None
        assert loaded.corpus_fingerprint == "good"


class TestForeignKeys:
    def test_deleting_a_page_removes_its_aspect_reference(self, tmp_repo) -> None:
        """The second reason for tables: a page reference is a real foreign key, so a deleted page
        cannot leave a row pointing at nothing the way it could inside a blob."""
        from sqlalchemy import delete as sa_delete

        from app.db.models import WikiDocId
        from app.db.session import session

        d1, d2 = _page("a.md"), _page("b.md")
        topic_map.record(_artifact(topics=[{"name": "T", "aspects": [_aspect("x", [d1, d2])]}]))

        with session() as s:
            s.execute(sa_delete(WikiDocId).where(WikiDocId.id == d2))

        loaded = topic_map.active()
        assert loaded is not None
        assert [p.doc_id for p in loaded.topics[0].aspects[0].pages] == [d1]

    def test_dropping_a_run_takes_everything_under_it(self, tmp_repo) -> None:
        d1 = _page("a.md")
        for _ in range(3):
            topic_map.record(_artifact(topics=[{"name": "T", "aspects": [_aspect("x", [d1])]}]))

        assert topic_map.prune(keep=1) == 2
        assert len(topic_map.history()) == 1

    def test_losing_the_taxonomy_keeps_the_run(self, tmp_repo) -> None:
        """SET NULL, not CASCADE: it costs the ability to resolve the type names a topic is keyed
        by, not the topics themselves."""
        from sqlalchemy import delete as sa_delete

        from app.db import entity_type_taxonomy
        from app.db.models import EntityTypeTaxonomy
        from app.db.session import session

        d1 = _page("a.md")
        taxonomy_id = entity_type_taxonomy.record(
            {"corpus_fingerprint": "c", "entity_types": [{"name": "person", "definition": "d"}]}
        )
        topic_map.record(
            _artifact(taxonomy_id=taxonomy_id, topics=[{"name": "T", "aspects": [_aspect("x", [d1])]}])
        )

        with session() as s:
            s.execute(sa_delete(EntityTypeTaxonomy).where(EntityTypeTaxonomy.id == taxonomy_id))

        loaded = topic_map.active()
        assert loaded is not None
        assert loaded.entity_type_taxonomy_id is None
        assert [t.name for t in loaded.topics] == ["T"]


class TestNaturalKey:
    def test_the_same_page_cannot_be_attached_twice(self, tmp_repo) -> None:
        """Nothing upstream guarantees a producer lists a page once. The duplicate is dropped
        rather than raised: the key alone would abort the whole run, losing a derivation that
        already paid for its LLM calls."""
        d1 = _page("a.md")
        dupe = _aspect("x", [d1])
        dupe["pages"] = dupe["pages"] * 2

        topic_map.record(_artifact(topics=[{"name": "T", "aspects": [dupe]}]))

        loaded = topic_map.active()
        assert loaded is not None
        assert [p.doc_id for p in loaded.topics[0].aspects[0].pages] == [d1]

    def test_one_page_can_hold_several_entities_rows(self, tmp_repo) -> None:
        """Why ``entity`` is IN the key: a customer-tracker page carries a deal-status row per
        customer, so the same (aspect, page) legitimately repeats."""
        d1 = _page("customers.md")
        aspect = _aspect("deal status", [])
        aspect["pages"] = [
            {"doc_id": d1, "need_name": "deal status", "entity": "Acme"},
            {"doc_id": d1, "need_name": "deal status", "entity": "Globex"},
        ]

        topic_map.record(_artifact(topics=[{"name": "T", "aspects": [aspect]}]))

        loaded = topic_map.active()
        assert loaded is not None
        assert sorted(p.entity for p in loaded.topics[0].aspects[0].pages) == ["Acme", "Globex"]


class TestAspectSummary:
    """The headline values for triage. Computed from the page rows, never stored — so they cannot
    drift from what they summarize, and a producer cannot assert one the rows disagree with."""

    @staticmethod
    def _with_pages(pages: list[dict]) -> topic_map.AspectRecord:
        aspect = _aspect("implementation status", [])
        aspect["pages"] = pages
        topic_map.record(_artifact(topics=[{"name": "T", "aspects": [aspect]}]))
        loaded = topic_map.active()
        assert loaded is not None
        return loaded.topics[0].aspects[0]

    def test_the_aspect_table_stores_no_summary_columns(self) -> None:
        """The decision itself, pinned. Re-adding a column here brings back a value that can go
        stale against its rows — and for detail_level, one that cannot be computed at all."""
        from app.db.models import Aspect

        assert {"aspect_kind", "detail_level", "focus"} & set(Aspect.__table__.columns.keys()) == set()

    def test_dominant_kind_is_the_majority_across_pages(self, tmp_repo) -> None:
        d1, d2, d3 = _page("a.md"), _page("b.md"), _page("c.md")
        aspect = self._with_pages(
            [
                {"doc_id": d1, "entity": "", "aspect_kind": "entity_status"},
                {"doc_id": d2, "entity": "", "aspect_kind": "entity_status"},
                {"doc_id": d3, "entity": "", "aspect_kind": "timeline"},
            ]
        )

        assert aspect.dominant_kind == "entity_status"

    def test_a_tied_kind_resolves_the_same_way_every_time(self, tmp_repo) -> None:
        """The real case from production: one page keeps "implementation status" as a dated log,
        another as a current-state checklist. A summary that depended on row order would report a
        different kind run to run for an unchanged corpus."""
        d1, d2 = _page("a.md"), _page("b.md")
        aspect = self._with_pages(
            [
                {"doc_id": d1, "entity": "", "aspect_kind": "timeline"},
                {"doc_id": d2, "entity": "", "aspect_kind": "entity_status"},
            ]
        )

        assert aspect.dominant_kind == "entity_status"  # tie broken by name, not by insertion

    def test_shared_detail_level_survives_when_pages_agree(self, tmp_repo) -> None:
        d1, d2 = _page("a.md"), _page("b.md")
        aspect = self._with_pages(
            [
                {"doc_id": d1, "entity": "", "detail_level": "one line per feature"},
                {"doc_id": d2, "entity": "", "detail_level": "one line per feature"},
            ]
        )

        assert aspect.shared_detail_level == "one line per feature"

    def test_the_same_granularity_in_different_words_summarizes_to_nothing(self, tmp_repo) -> None:
        """Free text, so there is no mode: both pages want one entry per feature, phrased their
        own way. Empty is the honest answer — read the page rows. Picking one would present an
        arbitrary page's wording as the aspect's, wrong for every other page under it."""
        d1, d2 = _page("a.md"), _page("b.md")
        aspect = self._with_pages(
            [
                {"doc_id": d1, "entity": "", "detail_level": "one line per feature"},
                {"doc_id": d2, "entity": "", "detail_level": "a checklist entry per feature"},
            ]
        )

        assert aspect.shared_detail_level == ""
        # ...and neither page loses its own, which is what the write actually uses.
        assert sorted(p.detail_level for p in aspect.pages) == [
            "a checklist entry per feature",
            "one line per feature",
        ]

    def test_disagreeing_focus_never_summarizes_as_open(self, tmp_repo) -> None:
        """Unanimity rather than a mode: "generic" here while a page is "specific" would advertise
        the aspect as admitting new entities when a page holding it admits none."""
        d1, d2, d3 = _page("a.md"), _page("b.md"), _page("c.md")
        aspect = self._with_pages(
            [
                {"doc_id": d1, "entity": "", "focus": "generic"},
                {"doc_id": d2, "entity": "", "focus": "generic"},
                {"doc_id": d3, "entity": "", "focus": "specific"},
            ]
        )

        assert aspect.shared_focus == ""

    def test_the_producer_supplies_a_default_the_pages_inherit(self, tmp_repo) -> None:
        """The artifact still carries aspect-level values — not stored, but used as the per-page
        default, so a producer states a facet's shape once instead of on every page."""
        d1 = _page("a.md")
        aspect = self._with_pages([{"doc_id": d1, "entity": ""}])

        assert aspect.pages[0].aspect_kind == "entity_status"  # from _aspect()'s aspect level
        assert aspect.dominant_kind == "entity_status"


class TestReverseLookup:
    def test_finds_the_aspects_a_page_holds(self, tmp_repo) -> None:
        """The query a reconciler runs per incoming document — indexed, not a scan over the map."""
        d1, d2 = _page("a.md"), _page("b.md")
        topic_map.record(
            _artifact(
                topics=[
                    {
                        "name": "T",
                        "aspects": [
                            _aspect("shared status", [d1, d2], key="s"),
                            _aspect("only on b", [d2], key="o"),
                        ],
                    }
                ]
            )
        )

        assert [a.name for a in topic_map.aspects_for_page(d1)] == ["shared status"]
        assert sorted(a.name for a in topic_map.aspects_for_page(d2)) == ["only on b", "shared status"]

    def test_a_page_in_no_aspect_returns_nothing(self, tmp_repo) -> None:
        d1, d2 = _page("a.md"), _page("b.md")
        topic_map.record(_artifact(topics=[{"name": "T", "aspects": [_aspect("x", [d1])]}]))

        assert topic_map.aspects_for_page(d2) == []

    def test_it_reads_the_active_run_only(self, tmp_repo) -> None:
        """A superseded run's page references must not answer for the current one."""
        d1 = _page("a.md")
        topic_map.record(_artifact(topics=[{"name": "T", "aspects": [_aspect("old name", [d1])]}]))
        topic_map.record(_artifact(topics=[{"name": "T", "aspects": [_aspect("new name", [d1])]}]))

        assert [a.name for a in topic_map.aspects_for_page(d1)] == ["new name"]

    def test_nothing_derived_yet_is_not_an_error(self, tmp_repo) -> None:
        assert topic_map.aspects_for_page(_page("a.md")) == []
        assert topic_map.active() is None


class TestCorpusFingerprint:
    def test_read_order_does_not_change_it(self, tmp_db) -> None:
        """Otherwise the map would look stale purely because pages came back in another order."""
        assert topic_map.corpus_fingerprint(
            [("d2", "sha2"), ("d1", "sha1")]
        ) == topic_map.corpus_fingerprint([("d1", "sha1"), ("d2", "sha2")])

    def test_a_changed_need_set_changes_it(self, tmp_db) -> None:
        before = topic_map.corpus_fingerprint([("d1", "sha1")])

        assert topic_map.corpus_fingerprint([("d1", "sha2")]) != before
        assert topic_map.corpus_fingerprint([("d1", "sha1"), ("d2", "sha2")]) != before
        assert topic_map.corpus_fingerprint([("d9", "sha1")]) != before
