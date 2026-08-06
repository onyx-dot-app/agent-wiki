"""Deriving the need map: grouping needs, then imposing topics and aspects on the groups.

The embedding call and the LLM call are not pinned here. What is pinned is everything that decides
whether the result means anything: what goes into the embed key, that the grouping is reproducible,
that a missing embedding is not reported as "nothing is shared", that a page nobody may write to is
left out — and, on the naming half, that a partial response loses a NEED rather than a cluster, that
a need cannot be written under two aspects, and that a failed derivation is not recorded as a map
saying the corpus shares nothing.
"""

from __future__ import annotations

from typing import Any

from app.ingest import need_map
from app.ingest.clustering import cosine, leader_cluster, normalize


def _need(name: str, **over: Any) -> dict[str, Any]:
    return {
        "need_name": name,
        "need_kind": "reference",
        "description": "",
        "detail_level": "",
        "update_instruction": "",
        "current_content": "",
        "entities": [],
        "focus": "specific",
    } | over


def _ref(doc_id: str, name: str, **over: Any) -> need_map.NeedRef:
    return need_map.NeedRef(doc_id=doc_id, path=f"{doc_id}.md", need=_need(name, **over))


def _cluster(*refs: need_map.NeedRef) -> need_map.Cluster:
    return need_map.Cluster(members=list(refs))


def _stub(monkeypatch, payload: dict[str, Any] | None) -> None:
    monkeypatch.setattr(need_map.json_completion, "complete_json", lambda *a, **k: payload)


def _topic(name: str, *aspects: dict[str, Any]) -> dict[str, Any]:
    return {"topic_name": name, "topic_description": f"about {name}", "aspects": list(aspects)}


def _aspect(name: str, indices: list[int]) -> dict[str, Any]:
    return {"aspect_name": name, "aspect_description": f"tracks {name}", "member_indices": indices}


class TestEmbedKey:
    def test_uses_name_and_description(self) -> None:
        key = need_map.embed_key(_need("deal status", description="status and blockers per customer"))

        assert key == "deal status. status and blockers per customer"

    def test_a_need_without_a_description_is_just_its_name(self) -> None:
        assert need_map.embed_key(_need("deal status")) == "deal status"

    def test_excludes_the_churning_state(self) -> None:
        """``current_content`` is the state, not the spec. In the key, a need would move between
        clusters when its content changed rather than when what it tracks changed — and a need
        exists precisely because the spec outlives the content."""
        key = need_map.embed_key(_need("deal status", current_content="Acme: negotiating. Globex: won."))

        assert "Acme" not in key and "negotiating" not in key

    def test_excludes_the_kind(self) -> None:
        """A closed four-value vocabulary. Appending it pulled 113 "reference" needs together into
        a 70-page cluster — grouping by kind rather than by subject."""
        assert "reference" not in need_map.embed_key(_need("deal status"))

    def test_excludes_entities(self) -> None:
        """The other axis. An entity is the ROW, a facet is the COLUMN: embedding entities would
        cluster "everything about Scania" instead of "deal status, across customers", which is
        exactly the fan-out this step exists to find."""
        key = need_map.embed_key(
            _need("deal status", entities=[{"canonical_name": "Scania", "primary": True}])
        )

        assert "Scania" not in key


class TestClusterNeeds:
    @staticmethod
    def _stub(monkeypatch, vectors):
        monkeypatch.setattr(need_map.embeddings, "embed_texts", lambda texts: vectors)

    def test_similar_needs_from_different_pages_group(self, monkeypatch) -> None:
        self._stub(monkeypatch, [[1.0, 0.0], [0.99, 0.14], [0.0, 1.0]])
        needs = [_ref("a", "deal status"), _ref("b", "deal status"), _ref("c", "font choices")]

        clusters = need_map.cluster_needs(needs)

        assert clusters is not None
        assert len(clusters) == 2
        assert clusters[0].spans_pages
        assert clusters[0].pages == {"a", "b"}

    def test_a_single_page_cluster_is_not_a_failure(self, monkeypatch) -> None:
        """Most of what a page tracks is its own. A clustering that claimed everything was shared
        would be wrong, so this is a normal outcome rather than an error."""
        self._stub(monkeypatch, [[1.0, 0.0], [0.99, 0.14]])
        clusters = need_map.cluster_needs([_ref("a", "one"), _ref("a", "two")])

        assert clusters is not None
        assert len(clusters) == 1
        assert not clusters[0].spans_pages

    def test_unavailable_embeddings_return_none_not_singletons(self, monkeypatch) -> None:
        """Without embeddings every need looks unrelated to every other, which is
        indistinguishable from a corpus that genuinely shares nothing. A caller must not record
        that as a finding."""
        self._stub(monkeypatch, None)

        assert need_map.cluster_needs([_ref("a", "x"), _ref("b", "y")]) is None

    def test_no_needs_is_no_clusters(self, monkeypatch) -> None:
        assert need_map.cluster_needs([]) == []

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

        clusters = need_map.cluster_needs(needs)

        assert clusters is not None
        assert [len(c.pages) for c in clusters] == [3, 2]

    def test_the_same_input_clusters_the_same_way_twice(self, monkeypatch) -> None:
        """A naming step downstream can only be reproducible if this is."""
        vectors = [[1.0, 0.0], [0.99, 0.14], [0.0, 1.0], [0.14, 0.99]]
        self._stub(monkeypatch, vectors)
        needs = [_ref("a", "s"), _ref("b", "s"), _ref("c", "f"), _ref("d", "f")]

        first = need_map.cluster_needs(needs)
        second = need_map.cluster_needs(needs)

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
            need_map.update_policy,
            "disabled_paths",
            lambda paths: {"closed.md"},
        )
        refs = need_map.load_needs()

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


class TestNameCluster:
    def test_splits_a_cluster_into_topics_and_their_aspects(self, monkeypatch) -> None:
        """The two levels from one call: the embedding merged two subjects, and within one of
        them two needs turn out to track the same facet from different pages."""
        _stub(
            monkeypatch,
            {
                "topics": [
                    _topic("Wiki Auto Management", _aspect("implementation status", [1, 2])),
                    _topic("Craft Integration", _aspect("rollout status", [3])),
                ]
            },
        )
        cluster = _cluster(
            _ref("a", "impl status and deferred work"),
            _ref("b", "wiki auto implementation status"),
            _ref("c", "craft rollout"),
        )

        drafts = need_map.name_cluster(cluster)

        assert [d.name for d in drafts] == ["Wiki Auto Management", "Craft Integration"]
        shared = drafts[0].aspects[0]
        assert shared.name == "implementation status"
        assert sorted(m.doc_id for m in shared.members) == ["a", "b"]

    def test_a_need_claimed_twice_lands_in_one_aspect(self, monkeypatch) -> None:
        """A need written under two aspects would be counted twice in the fan-out — the number
        the whole layer is justified by. First claim wins; the second aspect keeps the rest."""
        _stub(
            monkeypatch,
            {"topics": [_topic("T", _aspect("first", [1, 2]), _aspect("second", [2, 3]))]},
        )
        cluster = _cluster(_ref("a", "one"), _ref("b", "two"), _ref("c", "three"))

        drafts = need_map.name_cluster(cluster)

        first, second = drafts[0].aspects
        assert [m.doc_id for m in first.members] == ["a", "b"]
        assert [m.doc_id for m in second.members] == ["c"]

    def test_an_aspect_left_with_nothing_is_dropped_not_emptied(self, monkeypatch) -> None:
        _stub(monkeypatch, {"topics": [_topic("T", _aspect("first", [1]), _aspect("dupe", [1]))]})

        drafts = need_map.name_cluster(_cluster(_ref("a", "one")))

        assert [a.name for a in drafts[0].aspects] == ["first"]

    def test_an_out_of_range_index_costs_that_need_not_the_cluster(self, monkeypatch) -> None:
        """A hallucinated index is one need's placement, not a reason to discard structure the
        call has already been paid for."""
        _stub(monkeypatch, {"topics": [_topic("T", _aspect("status", [1, 99]))]})

        drafts = need_map.name_cluster(_cluster(_ref("a", "one"), _ref("b", "two")))

        assert [m.doc_id for m in drafts[0].aspects[0].members] == ["a"]

    def test_unplaced_needs_are_absent_rather_than_guessed(self, monkeypatch, caplog) -> None:
        """The prompt demands a partition. When it isn't one, the missing needs stay out of the
        map — a guessed placement is a confident wrong row — and the gap is logged."""
        _stub(monkeypatch, {"topics": [_topic("T", _aspect("status", [1]))]})

        with caplog.at_level("WARNING"):
            drafts = need_map.name_cluster(_cluster(_ref("a", "one"), _ref("b", "two")))

        placed = [m.doc_id for t in drafts for a in t.aspects for m in a.members]
        assert placed == ["a"]
        assert "left unplaced" in caplog.text

    def test_a_failed_call_yields_no_topic(self, monkeypatch) -> None:
        """Not a fallback topic named after the first member: a cluster the model could not
        structure is one we know nothing about."""
        _stub(monkeypatch, None)

        assert need_map.name_cluster(_cluster(_ref("a", "one"))) == []

    def test_a_topic_without_a_usable_aspect_is_dropped(self, monkeypatch) -> None:
        _stub(monkeypatch, {"topics": [{"topic_name": "T", "aspects": [{"aspect_name": ""}]}]})

        assert need_map.name_cluster(_cluster(_ref("a", "one"))) == []

    def test_junk_entries_do_not_abort_the_response(self, monkeypatch) -> None:
        _stub(
            monkeypatch,
            {"topics": ["nonsense", {"topic_name": "T", "aspects": ["junk", _aspect("ok", [1])]}]},
        )

        drafts = need_map.name_cluster(_cluster(_ref("a", "one")))

        assert [a.name for a in drafts[0].aspects] == ["ok"]


class TestMemberIndices:
    """The index list is the only thing tying a model's output back to real needs. Every rule here
    exists because the alternative is not an error but a WRONG assignment."""

    def test_a_fractional_index_is_dropped_not_truncated(self, monkeypatch) -> None:
        """``int(1.5)`` is 1 — a perfectly valid position. Truncating would attach a need the
        model never named, and the response would look well-formed while doing it."""
        _stub(monkeypatch, {"topics": [_topic("T", _aspect("status", [1.5]))]})

        assert need_map.name_cluster(_cluster(_ref("a", "one"), _ref("b", "two"))) == []

    def test_a_whole_float_is_kept(self, monkeypatch) -> None:
        """JSON has one number type, so an integer can arrive as 1.0."""
        _stub(monkeypatch, {"topics": [_topic("T", _aspect("status", [1.0]))]})

        drafts = need_map.name_cluster(_cluster(_ref("a", "one")))

        assert [m.doc_id for m in drafts[0].aspects[0].members] == ["a"]

    def test_a_boolean_does_not_claim_the_first_need(self, monkeypatch) -> None:
        """``True`` is an ``int`` in Python and would otherwise resolve to index 1."""
        _stub(monkeypatch, {"topics": [_topic("T", _aspect("status", [True]))]})

        assert need_map.name_cluster(_cluster(_ref("a", "one"))) == []


class TestListing:
    def test_names_the_page_so_fan_out_is_visible(self) -> None:
        """Two pages wording a need identically IS the fan-out. Without the page the model cannot
        tell that from one page restating itself."""
        listing = need_map._listing([_ref("a", "deal status"), _ref("b", "deal status")])

        assert "page=a" in listing and "page=b" in listing
        assert listing.count("deal status") == 2

    def test_excludes_the_state(self) -> None:
        """``current_content`` is the largest field and the most volatile; what a need TRACKS is
        what decides where it belongs."""
        listing = need_map._listing(
            [_ref("a", "deal status", current_content="Acme: negotiating. Globex: won.")]
        )

        assert "Acme" not in listing and "negotiating" not in listing


class TestConsolidate:
    def test_a_failing_cluster_does_not_take_the_others(self, monkeypatch) -> None:
        calls = {"n": 0}

        def flaky(*a: Any, **k: Any) -> dict[str, Any]:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return {"topics": [_topic("T", _aspect("status", [1]))]}

        monkeypatch.setattr(need_map.json_completion, "complete_json", flaky)

        drafts = need_map.name_clusters(
            [_cluster(_ref("a", "one")), _cluster(_ref("b", "two"))], workers=1
        )

        assert [d.name for d in drafts] == ["T"]

    def test_no_clusters_is_no_topics(self) -> None:
        assert need_map.name_clusters([]) == []


class TestRunConsolidation:
    """The outcomes that must NOT be recorded. Each would deactivate a good map and read as
    "the corpus shares nothing", which is indistinguishable from a real finding."""

    @staticmethod
    def _refuse(monkeypatch) -> list[Any]:
        recorded: list[Any] = []
        monkeypatch.setattr(need_map.store, "record", lambda *a, **k: recorded.append(a))
        return recorded

    def test_no_needs_records_nothing(self, tmp_db, monkeypatch) -> None:
        recorded = self._refuse(monkeypatch)
        monkeypatch.setattr(need_map, "load_needs", lambda rows=None: [])

        assert need_map.run_derivation() is None
        assert recorded == []

    def test_unavailable_embeddings_record_nothing(self, tmp_db, monkeypatch) -> None:
        """``cluster_needs`` returns None when it cannot embed. Recording then would store a map
        derived from no signal at all."""
        recorded = self._refuse(monkeypatch)
        monkeypatch.setattr(need_map, "load_needs", lambda rows=None: [_ref("a", "one")])
        monkeypatch.setattr(need_map, "cluster_needs", lambda refs: None)

        assert need_map.run_derivation() is None
        assert recorded == []

    def test_naming_that_produced_nothing_records_nothing(self, tmp_db, monkeypatch) -> None:
        recorded = self._refuse(monkeypatch)
        monkeypatch.setattr(need_map, "load_needs", lambda rows=None: [_ref("a", "one")])
        monkeypatch.setattr(
            need_map, "cluster_needs", lambda refs: [_cluster(*refs)]
        )
        _stub(monkeypatch, None)

        assert need_map.run_derivation() is None
        assert recorded == []


class TestArtifact:
    def test_two_topics_may_each_have_a_like_named_aspect(self) -> None:
        """Aspect keys are scoped to the topic. An aspect belongs to one topic, so two subjects
        each tracking "implementation status" are two facets — not one shared row."""
        drafts = [
            need_map.TopicDraft("A", "", [need_map.AspectDraft("status", "", [_ref("a", "x")])]),
            need_map.TopicDraft("B", "", [need_map.AspectDraft("status", "", [_ref("b", "y")])]),
        ]

        artifact = need_map._artifact(
            drafts, fingerprint="f", entity_type_taxonomy_id=None, model="m", stats={}
        )

        keys = [a["key"] for t in artifact["topics"] for a in t["aspects"]]
        assert len(set(keys)) == 2

    def test_carries_the_need_name_the_map_points_back_with(self) -> None:
        """``aspect_pages`` is keyed (aspect, doc_id, need_name) — the artifact must supply the
        name, or every link would collide on "" and all but one would be dropped."""
        drafts = [
            need_map.TopicDraft(
                "A", "", [need_map.AspectDraft("status", "", [_ref("a", "shipped features"), _ref("a", "deferred work")])]
            )
        ]

        artifact = need_map._artifact(
            drafts, fingerprint="f", entity_type_taxonomy_id=None, model="m", stats={}
        )

        pages = artifact["topics"][0]["aspects"][0]["pages"]
        assert sorted(p["need_name"] for p in pages) == ["deferred work", "shipped features"]


class TestEndToEnd:
    def test_a_derivation_lands_as_a_readable_map(self, tmp_repo, monkeypatch) -> None:
        """The join the unit tests cannot make: the artifact this module builds is the artifact
        ``need_map.record`` accepts. A field renamed on either side passes every test above and
        fails here.
        """
        from app.db import need_map as store, page_needs
        from app.wiki import git as wiki_git

        for path, names in (
            ("engineering.md", ["implementation status and deferred work"]),
            ("bo-todo.md", ["wiki auto implementation status"]),
            ("fonts.md", ["font choices"]),
        ):
            wiki_git.commit_file(path, "# P\n\nbody\n", "seed", author=None)
            page_needs.store(path, body="body", needs=[_need(n) for n in names])

        refs = need_map.load_needs()
        shared = [r for r in refs if "implementation" in r.need["need_name"]]
        alone = [r for r in refs if "font" in r.need["need_name"]]
        monkeypatch.setattr(
            need_map, "cluster_needs", lambda _: [_cluster(*shared), _cluster(*alone)]
        )

        payloads = iter(
            [
                {"topics": [_topic("Wiki Auto Management", _aspect("implementation status", [1, 2]))]},
                {"topics": [_topic("Typography", _aspect("font choices", [1]))]},
            ]
        )
        monkeypatch.setattr(
            need_map.json_completion, "complete_json", lambda *a, **k: next(payloads)
        )

        map_id = need_map.run_derivation(workers=1)

        assert map_id is not None
        loaded = store.active()
        assert loaded is not None
        assert loaded.need_map_id == map_id
        assert sorted(t.name for t in loaded.topics) == ["Typography", "Wiki Auto Management"]

        by_name = {t.name: t for t in loaded.topics}
        spanning = by_name["Wiki Auto Management"].aspects[0]
        assert spanning.name == "implementation status"
        assert spanning.spans_pages
        assert len(spanning.doc_ids) == 2
        assert not by_name["Typography"].aspects[0].spans_pages

        # the stats a run is judged by must describe the rows it actually wrote
        assert loaded.stats["n_topics"] == 2
        assert loaded.stats["n_aspects"] == 2
        assert loaded.stats["n_aspects_spanning_pages"] == 1
        assert loaded.stats["widest_aspect_pages"] == 2
        # ``model`` is "" here because no LLM is configured in tests; what must be recorded is
        # the knob the grouping actually depended on.
        assert loaded.provenance["cluster_similarity"] == need_map.CLUSTER_SIMILARITY
        assert "model" in loaded.provenance
        assert loaded.corpus_fingerprint

    def test_the_fingerprint_describes_what_was_actually_clustered(
        self, tmp_repo, monkeypatch
    ) -> None:
        """One read feeds both clustering and the fingerprint. Two independent reads could
        straddle an extraction, recording a fingerprint for a corpus the map was not derived
        from — which makes the staleness answer wrong in whichever direction the write fell."""
        from app.db import need_map as store, page_needs
        from app.wiki import git as wiki_git

        wiki_git.commit_file("a.md", "# P\n\nbody\n", "seed", author=None)
        page_needs.store("a.md", body="body", needs=[_need("deal status")])

        reads: list[int] = []
        real = page_needs.load_all

        def counting_load_all():
            reads.append(1)
            return real()

        monkeypatch.setattr(need_map.page_needs, "load_all", counting_load_all)
        monkeypatch.setattr(need_map, "cluster_needs", lambda refs: [_cluster(*refs)])
        _stub(monkeypatch, {"topics": [_topic("Customers", _aspect("deal status", [1]))]})

        assert need_map.run_derivation(workers=1) is not None

        assert len(reads) == 1, "the corpus must be read once, not once per use"
        loaded = store.active()
        assert loaded is not None
        rows = real()
        assert loaded.corpus_fingerprint == store.corpus_fingerprint(
            [(r.doc_id, r.content_sha256) for r in rows]
        )

    def test_the_reverse_lookup_finds_a_page_after_a_derivation(self, tmp_repo, monkeypatch) -> None:
        """The query a reconciler runs per incoming document. It is the only reason the map is
        tables rather than a blob, so it is worth proving end to end rather than from fixtures."""
        from app.db import need_map as store, page_needs
        from app.wiki import doc_ids, git as wiki_git

        for path in ("a.md", "b.md"):
            wiki_git.commit_file(path, "# P\n\nbody\n", "seed", author=None)
            page_needs.store(path, body="body", needs=[_need("deal status")])

        monkeypatch.setattr(
            need_map, "cluster_needs", lambda refs: [_cluster(*refs)]
        )
        monkeypatch.setattr(
            need_map.json_completion,
            "complete_json",
            lambda *a, **k: {"topics": [_topic("Customers", _aspect("deal status", [1, 2]))]},
        )

        assert need_map.run_derivation(workers=1) is not None

        found = store.aspects_for_page(doc_ids.get_or_mint("a.md"))
        assert [a.name for a in found] == ["deal status"]
        # ...and it reports the OTHER page too — that is the fan-out a reconciler acts on
        assert len(found[0].doc_ids) == 2
