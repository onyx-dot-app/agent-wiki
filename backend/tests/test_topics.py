"""Grouping per-page needs into the facets that span pages.

The embedding call is not pinned here. What is pinned is everything that decides whether the
result means anything: what goes into the key, that the grouping is reproducible, that a missing
embedding is not reported as "nothing is shared", and that a page nobody may write to is left out.
"""

from __future__ import annotations

from app.ingest import topics
from app.ingest.clustering import cosine, leader_cluster, normalize


def _need(name: str, **over):
    base = {
        "need_name": name,
        "need_kind": "reference",
        "description": "",
        "detail_level": "",
        "update_instruction": "",
        "current_content": "",
        "entities": [],
        "focus": "specific",
    }
    return base | over


def _ref(doc_id: str, name: str, **over):
    return topics.NeedRef(doc_id=doc_id, path=f"{doc_id}.md", need=_need(name, **over))


class TestEmbedKey:
    def test_uses_name_and_description(self) -> None:
        key = topics.embed_key(_need("deal status", description="status and blockers per customer"))

        assert key == "deal status. status and blockers per customer"

    def test_a_need_without_a_description_is_just_its_name(self) -> None:
        assert topics.embed_key(_need("deal status")) == "deal status"

    def test_excludes_the_churning_state(self) -> None:
        """``current_content`` is the state, not the spec. In the key, a need would move between
        clusters when its content changed rather than when what it tracks changed — and a need
        exists precisely because the spec outlives the content."""
        key = topics.embed_key(_need("deal status", current_content="Acme: negotiating. Globex: won."))

        assert "Acme" not in key and "negotiating" not in key

    def test_excludes_the_kind(self) -> None:
        """A closed four-value vocabulary. Appending it pulled 113 "reference" needs together into
        a 70-page cluster — grouping by kind rather than by subject."""
        assert "reference" not in topics.embed_key(_need("deal status"))

    def test_excludes_entities(self) -> None:
        """The other axis. An entity is the ROW, a facet is the COLUMN: embedding entities would
        cluster "everything about Scania" instead of "deal status, across customers", which is
        exactly the fan-out this step exists to find."""
        key = topics.embed_key(
            _need("deal status", entities=[{"canonical_name": "Scania", "primary": True}])
        )

        assert "Scania" not in key


class TestClusterNeeds:
    @staticmethod
    def _stub(monkeypatch, vectors):
        monkeypatch.setattr(topics.embeddings, "embed_texts", lambda texts: vectors)

    def test_similar_needs_from_different_pages_group(self, monkeypatch) -> None:
        self._stub(monkeypatch, [[1.0, 0.0], [0.99, 0.14], [0.0, 1.0]])
        needs = [_ref("a", "deal status"), _ref("b", "deal status"), _ref("c", "font choices")]

        clusters = topics.cluster_needs(needs)

        assert clusters is not None
        assert len(clusters) == 2
        assert clusters[0].spans_pages
        assert clusters[0].pages == {"a", "b"}

    def test_a_single_page_cluster_is_not_a_failure(self, monkeypatch) -> None:
        """Most of what a page tracks is its own. A clustering that claimed everything was shared
        would be wrong, so this is a normal outcome rather than an error."""
        self._stub(monkeypatch, [[1.0, 0.0], [0.99, 0.14]])
        clusters = topics.cluster_needs([_ref("a", "one"), _ref("a", "two")])

        assert clusters is not None
        assert len(clusters) == 1
        assert not clusters[0].spans_pages

    def test_unavailable_embeddings_return_none_not_singletons(self, monkeypatch) -> None:
        """Without embeddings every need looks unrelated to every other, which is
        indistinguishable from a corpus that genuinely shares nothing. A caller must not record
        that as a finding."""
        self._stub(monkeypatch, None)

        assert topics.cluster_needs([_ref("a", "x"), _ref("b", "y")]) is None

    def test_no_needs_is_no_clusters(self, monkeypatch) -> None:
        assert topics.cluster_needs([]) == []

    def test_clusters_are_ordered_by_reach(self, monkeypatch) -> None:
        """The widest-reaching facet first: that is the one worth naming, and the one whose
        fan-out justifies the step."""
        self._stub(monkeypatch, [[1.0, 0.0], [0.99, 0.14], [0.98, 0.20], [0.0, 1.0], [0.14, 0.99]])
        needs = [
            _ref("a", "status"),
            _ref("b", "status"),
            _ref("c", "status"),
            _ref("d", "fonts"),
            _ref("e", "fonts"),
        ]

        clusters = topics.cluster_needs(needs)

        assert clusters is not None
        assert [len(c.pages) for c in clusters] == [3, 2]

    def test_the_same_input_clusters_the_same_way_twice(self, monkeypatch) -> None:
        """A naming step downstream can only be reproducible if this is."""
        vectors = [[1.0, 0.0], [0.99, 0.14], [0.0, 1.0], [0.14, 0.99]]
        self._stub(monkeypatch, vectors)
        needs = [_ref("a", "s"), _ref("b", "s"), _ref("c", "f"), _ref("d", "f")]

        first = topics.cluster_needs(needs)
        second = topics.cluster_needs(needs)

        assert first is not None and second is not None
        assert [sorted(c.pages) for c in first] == [sorted(c.pages) for c in second]


class TestLoadNeeds:
    def test_excludes_pages_that_cannot_be_auto_updated(self, tmp_repo, monkeypatch) -> None:
        """Clustering must not offer a page nothing may write to as somewhere a fact could be
        reconciled to. Checked here rather than trusted from extraction, so the result does not
        depend on when that last ran."""
        from app.db import page_needs
        from app.wiki import git as wiki_git

        for path in ("open.md", "closed.md"):
            wiki_git.commit_file(path, "# P\n\nbody\n", "seed", author=None)
            page_needs.store(path, body="body", needs=[_need("a need")])

        monkeypatch.setattr(
            topics.update_policy,
            "disabled_paths",
            lambda paths: {"closed.md"},
        )
        refs = topics.load_needs()

        assert [r.path for r in refs] == ["open.md"]


class TestClusteringHelpers:
    def test_normalize_makes_a_unit_vector(self) -> None:
        assert abs(sum(v * v for v in normalize([3.0, 4.0])) - 1.0) < 1e-9

    def test_normalize_survives_a_zero_vector(self) -> None:
        assert normalize([0.0, 0.0]) == [0.0, 0.0]

    def test_cosine_of_identical_unit_vectors_is_one(self) -> None:
        v = normalize([1.0, 2.0])
        assert abs(cosine(v, v) - 1.0) < 1e-9

    def test_order_decides_which_item_seeds_a_cluster(self) -> None:
        """Leader clustering is order-sensitive by construction, which is why callers pass an
        explicit order rather than relying on input sequence."""
        vectors = [normalize(v) for v in ([1.0, 0.0], [0.9, 0.44], [0.0, 1.0])]

        first = leader_cluster(vectors, [0, 1, 2], 0.8)
        reversed_order = leader_cluster(vectors, [2, 1, 0], 0.8)

        assert sorted(len(g) for g in first) == sorted(len(g) for g in reversed_order)
