"""Storage for the derived topic layer.

Tables rather than one document because a page reference is a real foreign key — pinned here,
along with the properties a re-derivation could quietly break: exactly one run in force,
superseded runs still readable, a join table that carries no copy of the need it points at, and a
fingerprint that answers "have the needs moved?".
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
        "pages": [{"doc_id": d, "need_name": name} for d in pages],
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
        assert sorted(p.doc_id for p in aspect.needs) == sorted([d1, d2])
        assert aspect.spans_pages

    def test_an_aspect_belongs_to_exactly_one_topic(self, tmp_repo) -> None:
        """One facet named under two subjects is two aspects, not one shared row. An earlier
        revision made this many-to-many; nothing ever produced a shared aspect, and nothing
        downstream reads the topic — reconciliation happens at the aspect, which carries its own
        pages — so the second link changed no behaviour and the join table went."""
        d1 = _page("a.md")
        same = _aspect("implementation status", [d1], key="impl")
        topic_map.record(
            _artifact(
                topics=[
                    {"name": "Wiki Auto Management", "aspects": [same]},
                    {"name": "Craft Integration", "aspects": [same]},
                ]
            )
        )

        loaded = topic_map.active()
        assert loaded is not None
        assert len(loaded.topics) == 2
        first, second = (t.aspects[0] for t in loaded.topics)
        assert first.aspect_id != second.aspect_id
        assert first.name == second.name == "implementation status"

    def test_a_repeated_aspect_within_one_topic_is_one_row(self, tmp_repo) -> None:
        """De-duplication is scoped to the topic, keyed by the producer's own identity for the
        aspect — so listing it twice under one subject does not create two rows saying the same
        thing."""
        d1 = _page("a.md")
        same = _aspect("implementation status", [d1], key="impl")
        topic_map.record(_artifact(topics=[{"name": "T", "aspects": [same, same]}]))

        loaded = topic_map.active()
        assert loaded is not None
        assert len(loaded.topics[0].aspects) == 1

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
        assert [p.doc_id for p in loaded.topics[0].aspects[0].needs] == [d1]

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
    def test_one_page_can_contribute_two_needs_to_one_aspect(self, tmp_repo) -> None:
        """Why ``need_name`` is IN the key rather than ``entity``: the unit is the NEED. Clustering
        can put two of one page's needs in the same facet, and keying on the page alone dropped the
        second silently — losing a link the derivation had already paid an LLM call to find."""
        d1 = _page("roadmap.md")
        aspect = _aspect("delivery state", [])
        aspect["pages"] = [
            {"doc_id": d1, "need_name": "shipped features"},
            {"doc_id": d1, "need_name": "deferred work"},
        ]

        topic_map.record(_artifact(topics=[{"name": "T", "aspects": [aspect]}]))

        loaded = topic_map.active()
        assert loaded is not None
        found = loaded.topics[0].aspects[0]
        assert sorted(n.need_name for n in found.needs) == ["deferred work", "shipped features"]
        assert not found.spans_pages  # two needs, one page — reach is still one

    def test_a_genuinely_repeated_need_is_dropped_not_raised(self, tmp_repo) -> None:
        """Nothing upstream guarantees a producer lists a need once. The duplicate goes; the run
        survives, because the key alone would abort a derivation already paid for."""
        d1 = _page("a.md")
        dupe = _aspect("x", [d1])
        dupe["pages"] = dupe["pages"] * 2

        topic_map.record(_artifact(topics=[{"name": "T", "aspects": [dupe]}]))

        loaded = topic_map.active()
        assert loaded is not None
        assert len(loaded.topics[0].aspects[0].needs) == 1


class TestCarriesNoNeedPayload:
    """The join table stores the connection and nothing else. Pinned, because the fields that used
    to sit here are owned by ``page_needs`` — which is re-extracted when a page changes while this
    map is a snapshot, so a copy drifts in exactly the field that decides append-vs-replace."""

    def test_neither_table_stores_a_copy_of_the_need(self) -> None:
        from app.db.models import Aspect, AspectPage

        owned_by_the_need = {"aspect_kind", "need_kind", "detail_level", "focus", "entity"}
        assert set(AspectPage.__table__.columns.keys()) == {"aspect_id", "doc_id", "need_name"}
        assert owned_by_the_need & set(Aspect.__table__.columns.keys()) == set()
        assert "topic_aspects" not in Aspect.metadata.tables

    def test_the_fan_out_is_the_distinct_pages(self, tmp_repo) -> None:
        """What a consumer actually wants from an aspect: the pages to read needs for, deduped —
        two needs on one page are one page of reach, not two."""
        d1, d2 = _page("a.md"), _page("b.md")
        aspect = _aspect("delivery state", [])
        aspect["pages"] = [
            {"doc_id": d1, "need_name": "shipped features"},
            {"doc_id": d1, "need_name": "deferred work"},
            {"doc_id": d2, "need_name": "implementation status"},
        ]
        topic_map.record(_artifact(topics=[{"name": "T", "aspects": [aspect]}]))

        loaded = topic_map.active()
        assert loaded is not None
        found = loaded.topics[0].aspects[0]
        assert found.doc_ids == sorted([d1, d2])
        assert len(found.needs) == 3
        assert found.spans_pages


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
